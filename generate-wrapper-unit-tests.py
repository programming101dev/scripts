#!/usr/bin/env python3
"""Generate deterministic injected-failure tests for public p101 APIs.

Admitted inputs:
  * api-manifest.tsv in each active library listed by repos.txt
  * each library's public headers and implementation sources
  * a Clang executable capable of producing JSON ASTs
  * wrapper-errno-contract.json (POSIX plus per-platform manual overrides)

Outputs:
  * test/test_fault_wrappers.c in every active public-API library
  * test/unit-test-manifest.tsv in every active public-API library

Wrappers without an injected-failure path are assigned to test_behavior.c.
Those cases are intentionally handwritten because safe success-path fixtures
are part of each wrapper's contract and cannot be inferred from a signature.

Blind spot: injected failures prove propagation of documented error codes, not
that a real kernel can produce every condition on the current machine.
check-wrapper-unit-tests.py and each repository's test.sh are the executable
receipts for the generated and handwritten cases.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shutil
import subprocess
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Any, Iterator

from wrapper_errno_contract import (
    effective_error_selection,
    load_contract,
)


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
LIBRARIES = WORKSPACE / "libraries"
ERRNO_CONTRACT_PATH = SCRIPT_DIR / "wrapper-errno-contract.json"
REPOS_PATH = SCRIPT_DIR / "repos.txt"
AGGREGATE_TYPEDEFS = {"datum", "ENTRY"}


def records(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def active_libraries() -> dict[str, tuple[Path, list[dict[str, str]]]]:
    libraries: dict[str, tuple[Path, list[dict[str, str]]]] = {}
    for line in REPOS_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 2 or not fields[1].startswith("../libraries/lib_"):
            continue
        repo = (SCRIPT_DIR / fields[1]).resolve()
        manifest = repo / "api-manifest.tsv"
        if manifest.is_file():
            libraries[repo.name] = (repo, records(manifest))
    return libraries


def nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("inner", []):
        yield from nodes(child)


@cache
def clang_system_include_dirs(clang: str) -> tuple[Path, ...]:
    """Find Clang's builtin and public libclang headers."""
    candidates = {Path(clang).resolve().parent.parent / "include"}
    llvm_config_names = [
        str(Path(clang).resolve().with_name("llvm-config")),
        "llvm-config",
    ]
    for llvm_config in llvm_config_names:
        executable = shutil.which(llvm_config)
        if executable is None:
            continue
        result = subprocess.run(
            [executable, "--includedir"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidates.add(Path(result.stdout.strip()))
    brew = shutil.which("brew")
    if brew is not None:
        result = subprocess.run(
            [brew, "--prefix", "llvm"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidates.add(Path(result.stdout.strip()) / "include")
    return tuple(sorted(path for path in candidates if path.is_dir()))


def clang_ast(
    clang: str,
    source: Path,
    include_dirs: list[Path],
) -> dict[str, Any]:
    platform_definitions: list[str] = []
    platform_flags: list[str] = []
    system = platform.system()
    if system == "Darwin":
        platform_definitions.append("-D_DARWIN_C_SOURCE")
        sdk = subprocess.run(
            ["xcrun", "--show-sdk-path"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        platform_flags.extend(("-isysroot", sdk))
    elif system == "Linux":
        platform_definitions.append("-D_GNU_SOURCE")
    elif system == "FreeBSD":
        platform_definitions.extend(("-D_BSD_SOURCE", "-D__BSD_VISIBLE"))
    toolchain_includes = [
        flag
        for directory in clang_system_include_dirs(clang)
        for flag in ("-isystem", str(directory))
    ]
    command = [
        clang,
        "-std=c17",
        "-D_POSIX_C_SOURCE=200809L",
        "-D_XOPEN_SOURCE=700",
        *platform_definitions,
        *platform_flags,
        *toolchain_includes,
        *(flag for directory in include_dirs for flag in ("-I", str(directory))),
        "-fsyntax-only",
        "-Xclang",
        "-ast-dump=json",
        str(source),
    ]
    result = subprocess.run(
        command,
        cwd=WORKSPACE,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Clang could not parse {source.relative_to(WORKSPACE)}:\n"
            f"{result.stderr}"
        )
    return json.loads(result.stdout)


def referenced_names(node: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for child in nodes(node):
        referenced = child.get("referencedDecl")
        if isinstance(referenced, dict):
            name = referenced.get("name")
            if isinstance(name, str):
                names.add(name)
    return names


def function_declarations(
    ast: dict[str, Any],
    admitted: set[str],
) -> dict[str, dict[str, Any]]:
    declarations: dict[str, dict[str, Any]] = {}
    for node in nodes(ast):
        if node.get("kind") != "FunctionDecl":
            continue
        name = node.get("name")
        if name not in admitted:
            continue
        is_definition = any(
            child.get("kind") == "CompoundStmt"
            for child in node.get("inner", [])
        )
        existing = declarations.get(name)
        existing_is_definition = existing is not None and any(
            child.get("kind") == "CompoundStmt"
            for child in existing.get("inner", [])
        )
        if existing is None or (is_definition and not existing_is_definition):
            declarations[name] = node
    return declarations


def function_definitions(
    clang: str,
    rows: list[dict[str, str]],
    include_dirs: list[Path],
) -> dict[str, dict[str, Any]]:
    by_source: dict[Path, set[str]] = defaultdict(set)
    for row in rows:
        by_source[WORKSPACE / row["current_source"]].add(row["function"])

    definitions: dict[str, dict[str, Any]] = {}
    for source, admitted in sorted(by_source.items()):
        ast = clang_ast(clang, source, include_dirs)
        declarations = function_declarations(ast, admitted)
        missing = admitted - declarations.keys()
        if missing:
            raise RuntimeError(
                f"{source.relative_to(WORKSPACE)} lacks AST definitions for "
                f"{', '.join(sorted(missing))}"
            )
        definitions.update(declarations)
    return definitions


def return_type(declaration: dict[str, Any]) -> str:
    qualified = declaration["type"]["qualType"]
    marker = qualified.find("(")
    if marker < 0:
        raise RuntimeError(f"cannot determine return type from {qualified!r}")
    return qualified[:marker].strip()


def argument_expression(parameter: dict[str, Any]) -> str:
    type_info = parameter["type"]
    qualified = type_info["qualType"]
    desugared = type_info.get("desugaredQualType", qualified)
    name = parameter.get("name", "")
    if "p101_env" in qualified and "*" in qualified:
        return "env"
    if "p101_error" in qualified and "*" in qualified:
        return "err"
    if "va_list" in qualified:
        return "arguments"
    if "*" in qualified or "[" in qualified:
        return "NULL"
    if qualified in AGGREGATE_TYPEDEFS:
        return f"({qualified}){{0}}"
    stripped = desugared.removeprefix("const ").removeprefix("volatile ")
    if stripped.startswith("struct ") or stripped.startswith("union "):
        return f"({qualified}){{0}}"
    return "0"


def fault_test(
    name: str,
    declaration: dict[str, Any],
    error_names: dict[str, list[str]],
) -> str:
    parameters = [
        child
        for child in declaration.get("inner", [])
        if child.get("kind") == "ParmVarDecl"
    ]
    has_va_list = any(
        "va_list" in parameter.get("type", {}).get("qualType", "")
        for parameter in parameters
    )
    argument_setup = (
        "    va_list arguments;\n"
        "\n"
        "    memset(&arguments, 0, sizeof(arguments));\n"
        if has_va_list
        else ""
    )
    arguments = ", ".join(argument_expression(parameter) for parameter in parameters)
    result_type = return_type(declaration)
    if result_type == "void":
        invocation = f"    {name}({arguments});"
    else:
        invocation = (
            f"    {result_type} result = {name}({arguments});\n"
            "    (void)result;"
        )
    arrays = {
        key: ", ".join(error_names.get(key, []) or ["EIO"])
        for key in ("linux", "macos", "freebsd", "posix")
    }
    return f"""/* P101_TEST_CASE({name}) */
static void test_{name}(struct p101_env *env, struct p101_error *err)
{{
{argument_setup}\
#ifdef __linux__
    static const int errors[] = {{{arrays["linux"]}}};
#elif defined(__APPLE__)
    static const int errors[] = {{{arrays["macos"]}}};
#elif defined(__FreeBSD__)
    static const int errors[] = {{{arrays["freebsd"]}}};
#else
    static const int errors[] = {{{arrays["posix"]}}};
#endif

    for(size_t index = 0U; index < sizeof(errors) / sizeof(errors[0]); index++)
    {{
        struct fault_state state = {{0, errors[index]}};

        p101_env_set_fault_injector(env, fail_next_call, &state);
{invocation}
        EXPECT(state.checks == 1);
        EXPECT(p101_error_is_errno(err, state.errnum));
        p101_error_reset(err);
    }}
    p101_env_set_fault_injector(env, NULL, NULL);
}}
"""


def fault_source(
    includes: str,
    declarations: dict[str, dict[str, Any]],
    names: list[str],
    errno_contract: dict[str, dict[str, list[str]]],
) -> str:
    if not names:
        return f"""{includes}
#include <stdlib.h>

int main(void)
{{
    return EXIT_SUCCESS;
}}
"""
    tests = "\n".join(
        fault_test(name, declarations[name], errno_contract.get(name, []))
        for name in names
    )
    calls = "\n".join(f"    test_{name}(env, err);" for name in names)
    return f"""#include <errno.h>
{includes}
#include <p101_env/env.h>
#include <p101_error/error.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;

#define EXPECT(condition)                                                        \\
    do                                                                           \\
    {{                                                                            \\
        if(!(condition))                                                         \\
        {{                                                                        \\
            fprintf(stderr, "FAIL %s:%d: %s\\n", __FILE__, __LINE__, #condition); \\
            failures++;                                                          \\
        }}                                                                        \\
    }} while(0)

struct fault_state
{{
    int checks;
    int errnum;
}};

static int fail_next_call(const struct p101_env *env, const char *call_name, void *user_data)
{{
    struct fault_state *state;

    (void)env;
    (void)call_name;
    state = user_data;
    state->checks++;
    return state->errnum;
}}

{tests}
int main(void)
{{
    struct p101_error *err;
    struct p101_env   *env;

    err = p101_error_create(false);
    if(err == NULL)
    {{
        return EXIT_FAILURE;
    }}
    env = p101_env_create(err, NULL);
    if(env == NULL)
    {{
        p101_error_destroy(err);
        return EXIT_FAILURE;
    }}
{calls}
    p101_env_destroy(env);
    p101_error_destroy(err);
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}}
"""


def public_header_includes(repo: Path) -> str:
    headers = sorted((repo / "include").rglob("*.h"))
    return "\n".join(
        f"#include <{header.relative_to(repo / 'include')}>"
        for header in headers
    )


def existing_behavior_source(repo: Path, name: str) -> tuple[str, str] | None:
    invocation = re.compile(rf"\b{re.escape(name)}\s*\(")
    for source in sorted((repo / "test").glob("*.[cC]")) + sorted(
        (repo / "test").glob("*.cpp")
    ):
        if source.name == "test_fault_wrappers.c":
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        if invocation.search(text) is None:
            continue
        relative = str(source.relative_to(repo))
        if f"P101_TEST_CASE({name})" in text:
            return ("behavior", relative)
        return ("behavior-existing", relative)
    return None


def write_outputs(clang: str, clang_format: str) -> None:
    libraries = active_libraries()
    include_dirs = sorted(
        path
        for path in LIBRARIES.glob("lib_*/include")
        if path.is_dir()
    )
    generated_sources: list[Path] = []
    errno_contract = load_contract(ERRNO_CONTRACT_PATH)
    wrapper_errors: dict[str, dict[str, list[str]]] = {}
    for wrapper, binding in errno_contract["wrappers"].items():
        function = binding.get("function")
        if function is None:
            wrapper_errors[wrapper] = {
                platform_name: []
                for platform_name in ("linux", "macos", "freebsd", "posix")
            }
            continue
        wrapper_errors[wrapper] = {}
        for platform_name in ("linux", "macos", "freebsd"):
            errors, _selection, _source = effective_error_selection(
                errno_contract,
                function,
                platform_name,
            )
            wrapper_errors[wrapper][platform_name] = errors
        errors, _selection, _source = effective_error_selection(
            errno_contract,
            function,
            None,
        )
        wrapper_errors[wrapper]["posix"] = errors

    for library, (repo, library_rows) in sorted(libraries.items()):
        admitted = {row["function"] for row in library_rows}
        declarations = function_definitions(clang, library_rows, include_dirs)
        manifest_path = repo / "test" / "unit-test-manifest.tsv"
        fault_calls = {"p101_env_check_fault", "p101_env_check_fault_action"}
        faultable = {
            name
            for name, declaration in declarations.items()
            if referenced_names(declaration) & fault_calls
        }
        fault_names = sorted(admitted & faultable)
        behavior_names = sorted(admitted - faultable)
        test_dir = repo / "test"
        fault_path = test_dir / "test_fault_wrappers.c"
        cmake_uses_fault_test = "test_fault_wrappers" in (
            test_dir / "CMakeLists.txt"
        ).read_text(encoding="utf-8", errors="replace")
        if fault_names or cmake_uses_fault_test:
            fault_path.write_text(
                fault_source(
                    public_header_includes(repo),
                    declarations,
                    fault_names,
                    wrapper_errors,
                ),
                encoding="utf-8",
            )
            generated_sources.append(fault_path)
        elif fault_path.is_file():
            fault_path.unlink()
        manifest = ["function\ttest_kind\ttest_source\n"]
        manifest.extend(
            f"{name}\tfault\ttest/test_fault_wrappers.c\n"
            for name in fault_names
        )
        for name in behavior_names:
            existing = existing_behavior_source(repo, name)
            if existing is None:
                manifest.append(f"{name}\tbehavior\ttest/test_behavior.c\n")
            else:
                kind, source = existing
                manifest.append(f"{name}\t{kind}\t{source}\n")
        manifest_path.write_text(
            "".join(manifest),
            encoding="utf-8",
        )
        print(
            f"{library}: {len(fault_names)} injected-failure, "
            f"{len(behavior_names)} behavior tests"
        )
    formatter = shutil.which(clang_format)
    if formatter is None:
        raise RuntimeError(f"cannot find clang-format executable {clang_format!r}")
    subprocess.run(
        [formatter, "-i", *map(str, generated_sources)],
        cwd=WORKSPACE,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clang", default=os.environ.get("CC", "clang"))
    parser.add_argument(
        "--clang-format",
        default=os.environ.get("CLANG_FORMAT", "clang-format"),
    )
    args = parser.parse_args()
    write_outputs(args.clang, args.clang_format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
