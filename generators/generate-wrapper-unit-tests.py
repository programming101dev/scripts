#!/usr/bin/env python3
"""Generate deterministic injected-failure tests for public p101 APIs.

Admitted inputs:
  * api-manifest.tsv in each active library listed by repos.txt
  * each library's public headers and implementation sources
  * a Clang executable capable of producing JSON ASTs
  * wrapper-platform-faults.json (POSIX plus platform manual overrides)

Outputs:
  * test/test_fault_wrappers.c in every active public-API library
  * test/unit-test-manifest.tsv in every active public-API library
  * one P101WRAPPER outcome record per executed platform fault when
    P101_WRAPPER_OUTCOME_LOG names a receipt file

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
import sys
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Any, Iterator

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from wrapper_fault_contract import (  # noqa: E402
    effective_fault_selection,
    fault_domain,
    has_documented_faults,
    load_contract,
)


WORKSPACE = SCRIPTS_ROOT.parent
LIBRARIES = WORKSPACE / "libraries"
PLATFORM_FAULTS_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-platform-faults.json"
)
FAILURE_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-failure-contract.json"
)
OUTCOME_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-outcome-contract.json"
)
REPOS_PATH = SCRIPTS_ROOT / "repos.txt"
AGGREGATE_TYPEDEFS = {
    "datum",
    "div_t",
    "ENTRY",
    "imaxdiv_t",
    "ldiv_t",
    "lldiv_t",
}
AGGREGATE_MEMBERS = {
    "datum": ("dptr", "dsize"),
    "div_t": ("quot", "rem"),
    "imaxdiv_t": ("quot", "rem"),
    "ldiv_t": ("quot", "rem"),
    "lldiv_t": ("quot", "rem"),
}
OPAQUE_POINTEE_TYPES = {
    "DBM",
    "DIR",
    "FILE",
    "iconv_t",
}


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
        repo = (SCRIPTS_ROOT / fields[1]).resolve()
        manifest = repo / "api-manifest.tsv"
        if manifest.is_file():
            libraries[repo.name] = (repo, records(manifest))
    return libraries


def nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("inner", []):
        yield from nodes(child)


def versioned_libclang_include_dirs(root: Path) -> tuple[Path, ...]:
    """Return package-manager libclang include roots below a prefix."""
    candidates = {
        *root.glob("lib/llvm-*/include"),
        *root.glob("llvm*/include"),
    }
    return tuple(sorted(path for path in candidates if path.is_dir()))


@cache
def clang_system_include_dirs(clang: str) -> tuple[Path, ...]:
    """Find Clang's builtin and public libclang headers."""
    candidates = {
        Path(clang).resolve().parent.parent / "include",
        Path("/usr/include"),
        Path("/usr/local/include"),
    }
    candidates.update(versioned_libclang_include_dirs(Path("/usr")))
    candidates.update(versioned_libclang_include_dirs(Path("/usr/local")))
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


def called_functions(node: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for child in nodes(node):
        if child.get("kind") != "CallExpr":
            continue
        for nested in nodes(child):
            referenced = nested.get("referencedDecl")
            if (
                isinstance(referenced, dict)
                and referenced.get("kind") == "FunctionDecl"
                and isinstance(referenced.get("name"), str)
            ):
                names.add(referenced["name"])
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


def result_declaration(
    declaration: dict[str, Any],
    name: str,
) -> str:
    qualified = declaration["type"]["qualType"]
    function_pointer = re.fullmatch(
        r"(.+?)\s*\(\*\((.*)\)\)(\(.*\))",
        qualified,
    )
    if function_pointer is not None:
        base, _parameters, suffix = function_pointer.groups()
        return f"{base.strip()} (*{name}){suffix}"
    return f"{return_type(declaration)} {name}"


def has_va_list_parameter(declaration: dict[str, Any]) -> bool:
    """Return whether a wrapper accepts an already-started va_list.

    A va_list is not an ordinary zero-initializable object. Generated tests
    must create it in a variadic function with va_start(), even when injected
    failure is expected to return before the wrapped function consumes it.
    """
    return any(
        child.get("kind") == "ParmVarDecl"
        and "va_list" in child.get("type", {}).get("qualType", "")
        for child in declaration.get("inner", [])
    )


def accepts_error_parameter(declaration: dict[str, Any]) -> bool:
    return any(
        child.get("kind") == "ParmVarDecl"
        and "p101_error" in child.get("type", {}).get("qualType", "")
        and "*" in child.get("type", {}).get("qualType", "")
        for child in declaration.get("inner", [])
    )


def fault_test_signature_suffix(declaration: dict[str, Any]) -> str:
    return ", ..." if has_va_list_parameter(declaration) else ""


def fault_test_call_suffix(declaration: dict[str, Any]) -> str:
    return ", 0" if has_va_list_parameter(declaration) else ""


def va_list_setup(declaration: dict[str, Any]) -> str:
    if not has_va_list_parameter(declaration):
        return ""
    return "    va_list arguments;\n\n    va_start(arguments, err);\n"


def va_list_teardown(declaration: dict[str, Any]) -> str:
    if not has_va_list_parameter(declaration):
        return ""
    return "    va_end(arguments);\n"


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
    if name in {"format", "fmt"} and "*" in qualified:
        return 'L"p101"' if "wchar_t" in qualified else '"p101"'
    if "*" in qualified or "[" in qualified:
        return "NULL"
    if qualified in AGGREGATE_TYPEDEFS:
        return f"({qualified}){{0}}"
    stripped = desugared.removeprefix("const ").removeprefix("volatile ")
    if stripped.startswith("struct ") or stripped.startswith("union "):
        return f"({qualified}){{0}}"
    return "0"


def source_location(
    node: dict[str, Any],
    source: Path,
) -> tuple[Path, int] | None:
    begin = node.get("range", {}).get("begin", {})
    location = begin.get("expansionLoc", begin)
    offset = location.get("offset")
    if not isinstance(offset, int):
        return None
    file_name = location.get("file")
    return (Path(file_name) if isinstance(file_name, str) else source, offset)


def macro_invocation(text: str, offset: int) -> tuple[str, list[str]] | None:
    """Read one macro invocation without pretending to parse general C."""
    opening = text.find("(", offset)
    if opening < 0:
        return None
    name = text[offset:opening].strip()
    if not re.fullmatch(r"P101_[A-Z0-9_]+", name):
        return None
    arguments: list[str] = []
    argument_start = opening + 1
    depth = 0
    quote: str | None = None
    escaped = False
    index = argument_start
    while index < len(text):
        character = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                arguments.append(text[argument_start:index].strip())
                return name, arguments
            depth -= 1
        elif character == "," and depth == 0:
            arguments.append(text[argument_start:index].strip())
            argument_start = index + 1
        index += 1
    return None


def direct_return_expression(
    node: dict[str, Any],
    source: Path,
) -> str | None:
    begin = node.get("range", {}).get("begin", {})
    end = node.get("range", {}).get("end", {})
    if "spellingLoc" in begin or "expansionLoc" in begin:
        return None
    begin_offset = begin.get("offset")
    end_offset = end.get("offset")
    token_length = end.get("tokLen", 0)
    if not all(
        isinstance(value, int)
        for value in (begin_offset, end_offset, token_length)
    ):
        return None
    text = source.read_text(encoding="utf-8", errors="replace")
    fragment = text[begin_offset : end_offset + token_length].strip()
    match = re.fullmatch(r"return\s+(.+?)\s*;?", fragment, re.DOTALL)
    return match.group(1).strip() if match is not None else None


def explicit_fault_result(fragment: str) -> str | None:
    """Return the first single-exit result assigned by a manual fault path."""
    if "p101_env_check_fault" not in fragment:
        return None
    match = re.search(
        r"\bp101_single_result_\s*=\s*([^;]+)\s*;",
        fragment,
    )
    return match.group(1).strip() if match is not None else None


def fault_return_contract(
    declaration: dict[str, Any],
    source: Path,
) -> dict[str, str]:
    """Extract the injected branch's return contract from Clang locations."""
    text = source.read_text(encoding="utf-8", errors="replace")
    declaration_range = declaration.get("range", {})
    begin = declaration_range.get("begin", {})
    end = declaration_range.get("end", {})
    begin = begin.get("expansionLoc", begin)
    end = end.get("expansionLoc", end)
    begin_offset = begin.get("offset")
    end_offset = end.get("offset")
    token_length = end.get("tokLen", 0)
    if all(
        isinstance(value, int)
        for value in (begin_offset, end_offset, token_length)
    ):
        fragment = text[begin_offset : end_offset + token_length]
        for match in re.finditer(
            r"\bP101_[A-Z0-9_]*FAULT[A-Z0-9_]*RETURN[A-Z0-9_]*\s*\(",
            fragment,
        ):
            invocation = macro_invocation(text, begin_offset + match.start())
            if invocation is None:
                continue
            macro_name, arguments = invocation
            domain = "system" if "_SYSTEM" in macro_name else "errno"
            if macro_name.endswith("_CODE"):
                return {
                    "kind": "error-code",
                    "expression": "fault-code",
                    "error_domain": domain,
                }
            if macro_name.endswith("_VOID"):
                return {
                    "kind": "void",
                    "expression": "void",
                    "error_domain": domain,
                }
            if macro_name == "P101_FAULT_RETURN_PARSED":
                default_parameter = next(
                    (
                        parameter
                        for parameter in declaration.get("inner", [])
                        if parameter.get("kind") == "ParmVarDecl"
                        and parameter.get("name") == "default_value"
                    ),
                    None,
                )
                if default_parameter is None:
                    raise RuntimeError(
                        f"{declaration.get('name', '?')} uses "
                        "P101_FAULT_RETURN_PARSED without default_value"
                    )
                return {
                    "kind": "value",
                    "expression": argument_expression(default_parameter),
                    "error_domain": domain,
                }
            if arguments:
                return {
                    "kind": "value",
                    "expression": arguments[-1],
                    "error_domain": domain,
                }
        expression = explicit_fault_result(fragment)
        if expression is not None:
            return {
                "kind": "value",
                "expression": expression,
                "error_domain": "errno",
            }

    direct_returns: list[str] = []
    for node in nodes(declaration):
        if node.get("kind") != "ReturnStmt":
            continue
        location = source_location(node, source)
        if location is not None:
            location_path, offset = location
            text = location_path.read_text(encoding="utf-8", errors="replace")
            invocation = macro_invocation(text, offset)
            if invocation is not None:
                macro_name, arguments = invocation
                domain = (
                    "system" if "_SYSTEM" in macro_name else "errno"
                )
                if macro_name.endswith("_CODE"):
                    return {
                        "kind": "error-code",
                        "expression": "fault-code",
                        "error_domain": domain,
                    }
                if macro_name.endswith("_VOID"):
                    return {
                        "kind": "void",
                        "expression": "void",
                        "error_domain": domain,
                    }
                if macro_name == "P101_FAULT_RETURN_PARSED":
                    default_parameter = next(
                        (
                            parameter
                            for parameter in declaration.get("inner", [])
                            if parameter.get("kind") == "ParmVarDecl"
                            and parameter.get("name") == "default_value"
                        ),
                        None,
                    )
                    if default_parameter is None:
                        raise RuntimeError(
                            f"{declaration.get('name', '?')} uses "
                            "P101_FAULT_RETURN_PARSED without default_value"
                        )
                    return {
                        "kind": "value",
                        "expression": argument_expression(default_parameter),
                        "error_domain": domain,
                    }
                if (
                    "FAULT" in macro_name
                    and "RETURN" in macro_name
                    and arguments
                ):
                    return {
                        "kind": "value",
                        "expression": arguments[-1],
                        "error_domain": domain,
                    }
        expression = direct_return_expression(node, source)
        if expression is not None:
            direct_returns.append(expression)
    if direct_returns:
        # Handwritten short-I/O/resource wrappers put the injected-error
        # return before their native operation and final return.
        return {
            "kind": "value",
            "expression": direct_returns[0],
            "error_domain": "errno",
        }
    if return_type(declaration) == "void":
        return {
            "kind": "void",
            "expression": "void",
            "error_domain": "errno",
        }
    raise RuntimeError(
        f"cannot determine injected failure return for "
        f"{declaration.get('name', '?')}"
    )


def validate_fault_boundary(declaration: dict[str, Any]) -> None:
    """Require fault selection before any observable wrapper operation."""
    body = next(
        (
            child
            for child in declaration.get("inner", [])
            if child.get("kind") == "CompoundStmt"
        ),
        None,
    )
    if body is None:
        raise RuntimeError(
            f"{declaration.get('name', '?')} has no function body"
        )
    fault_calls = {"p101_env_check_fault", "p101_env_check_fault_action"}
    children = body.get("inner", [])
    fault_index = next(
        (
            index
            for index, child in enumerate(children)
            if referenced_names(child) & fault_calls
        ),
        None,
    )
    if fault_index is None:
        raise RuntimeError(
            f"{declaration.get('name', '?')} has no fault boundary"
        )
    for child in children[:fault_index]:
        calls = called_functions(child)
        if child.get("kind") == "DeclStmt" and not any(
            nested.get("kind") == "CallExpr" for nested in nodes(child)
        ):
            continue
        if child.get("kind") == "CallExpr" and calls <= {
            "p101_env_trace"
        }:
            continue
        raise RuntimeError(
            f"{declaration.get('name', '?')} performs work before its "
            "fault boundary"
        )


def is_aggregate_return(declaration: dict[str, Any]) -> bool:
    result = return_type(declaration)
    if "*" in result:
        return False
    if result in AGGREGATE_TYPEDEFS:
        return True
    desugared = declaration.get("type", {}).get(
        "desugaredQualType",
        result,
    )
    prefix = desugared.split("(", 1)[0].strip()
    return prefix.startswith(("struct ", "union "))


def result_assertion(
    declaration: dict[str, Any],
    failure: dict[str, str],
) -> str:
    kind = failure["kind"]
    if kind == "void":
        return ""
    if kind == "error-code":
        return "        EXPECT(result == state.code);\n"
    expression = failure["expression"]
    expression = expression.replace(
        "P101_INET_ADDR_NONE_VALUE",
        "-1",
    )
    result = return_type(declaration)
    if "p101_nan(" in expression:
        return "        EXPECT(isnan(result));\n"
    if "mutable_fallback(" in expression:
        return "        EXPECT(result == NULL);\n"
    if is_aggregate_return(declaration):
        members = AGGREGATE_MEMBERS.get(result)
        if members is None:
            raise RuntimeError(
                f"{declaration.get('name', '?')}: aggregate result {result} "
                "has no field-wise assertion contract"
            )
        assertions = "".join(
            f"        EXPECT(result.{member} == expected_result.{member});\n"
            for member in members
        )
        return f"        {result} expected_result = {expression};\n" + assertions
    return f"        EXPECT(result == ({expression}));\n"


def writable_fixture(
    parameter: dict[str, Any],
    index: int,
) -> tuple[list[str], str, list[str]]:
    """Provide a canary for writable pointer arguments on the fault path."""
    qualified = parameter.get("type", {}).get("qualType", "")
    if "*" not in qualified or "(*" in qualified:
        return [], argument_expression(parameter), []
    pointee, _separator, _tail = qualified.rpartition("*")
    pointee = re.sub(r"\brestrict\b", "", pointee).strip()
    if re.search(r"\bconst\b", pointee):
        return [], argument_expression(parameter), []
    name = f"argument_{index}"
    if pointee == "void":
        declarations = [
            f"    unsigned char {name}[64];",
            f"    unsigned char {name}_before[sizeof({name})];",
            f"    memset({name}, 0xA5, sizeof({name}));",
            f"    memcpy({name}_before, {name}, sizeof({name}));",
        ]
        assertions = [
            f"        EXPECT(memcmp({name}, {name}_before, "
            f"sizeof({name})) == 0);"
        ]
        return declarations, name, assertions
    bare_pointee = re.sub(
        r"\b(?:const|volatile|restrict|_Atomic)\b",
        "",
        pointee,
    ).strip()
    if bare_pointee in OPAQUE_POINTEE_TYPES or bare_pointee.startswith(
        ("struct ", "union ")
    ):
        return [], argument_expression(parameter), []
    declarations = [
        f"    {pointee} {name}[4];",
        f"    unsigned char {name}_before[sizeof({name})];",
        f"    memset({name}, 0xA5, sizeof({name}));",
        f"    memcpy({name}_before, {name}, sizeof({name}));",
    ]
    assertions = [
        f"        EXPECT(memcmp({name}, {name}_before, "
        f"sizeof({name})) == 0);"
    ]
    return declarations, name, assertions


def fault_test(
    name: str,
    declaration: dict[str, Any],
    error_names: dict[str, list[str]],
    failure: dict[str, str],
) -> str:
    parameters = [
        child
        for child in declaration.get("inner", [])
        if child.get("kind") == "ParmVarDecl"
    ]
    argument_setup = va_list_setup(declaration)
    fixture_declarations: list[str] = []
    argument_values: list[str] = []
    fixture_assertions: list[str] = []
    for index, parameter in enumerate(parameters):
        if parameter.get("name") in {"env", "err"}:
            argument_values.append(argument_expression(parameter))
            continue
        declarations, expression, assertions = writable_fixture(
            parameter,
            index,
        )
        fixture_declarations.extend(declarations)
        argument_values.append(expression)
        fixture_assertions.extend(assertions)
    arguments = ", ".join(argument_values)
    if failure["kind"] == "void":
        invocation = f"    {name}({arguments});"
    else:
        invocation = (
            f"    {result_declaration(declaration, 'result')} = "
            f"{name}({arguments});\n"
            "    (void)result;"
        )
    fixture_setup = (
        "\n".join(fixture_declarations) + "\n"
        if fixture_declarations
        else ""
    )
    output_assertions = (
        "\n".join(fixture_assertions) + "\n"
        if fixture_assertions
        else ""
    )
    return_assertion = result_assertion(declaration, failure)
    error_assertion = (
        "p101_error_is_error(err, P101_ERROR_SYSTEM, state.code)"
        if failure["error_domain"] == "system"
        else "p101_error_is_errno(err, state.code)"
    )
    arrays = {
        key: ", ".join(error_names.get(key, []) or ["EIO"])
        for key in ("linux", "macos", "freebsd", "posix")
    }
    labels = {
        key: ", ".join(
            json.dumps(error_name)
            for error_name in (error_names.get(key, []) or ["EIO"])
        )
        for key in ("linux", "macos", "freebsd", "posix")
    }
    return f"""/* P101_TEST_CASE({name}) */
static void test_{name}(struct p101_env *env, struct p101_error *err{fault_test_signature_suffix(declaration)})
{{
{argument_setup}\
{fixture_setup}\
#ifdef __linux__
    static const int errors[] = {{{arrays["linux"]}}};
    static const char *const error_names[] = {{{labels["linux"]}}};
#elif defined(__APPLE__)
    static const int errors[] = {{{arrays["macos"]}}};
    static const char *const error_names[] = {{{labels["macos"]}}};
#elif defined(__FreeBSD__)
    static const int errors[] = {{{arrays["freebsd"]}}};
    static const char *const error_names[] = {{{labels["freebsd"]}}};
#else
    static const int errors[] = {{{arrays["posix"]}}};
    static const char *const error_names[] = {{{labels["posix"]}}};
#endif

    for(size_t index = 0U; index < sizeof(errors) / sizeof(errors[0]); index++)
    {{
        struct fault_state state = {{0, errors[index]}};
        int                failures_before;

        failures_before       = failures;
        EXPECT(p101_error_has_no_error(err));
        fault_resource_events = 0U;
        errno                 = P101_TEST_ERRNO_SENTINEL;
        p101_env_set_fault_injector(env, fail_next_call, &state);
{invocation}
        EXPECT(state.checks == 1);
        EXPECT({error_assertion});
        EXPECT(errno == P101_TEST_ERRNO_SENTINEL);
{return_assertion}\
{output_assertions}\
        EXPECT(fault_resource_events == 0U);
        write_outcome("{name}",
                      "{failure["error_domain"]}",
                      error_names[index],
                      state.code,
                      failures == failures_before);
        p101_error_reset(err);
    }}
    p101_env_set_fault_injector(env, NULL, NULL);
{va_list_teardown(declaration)}\
}}
"""


def fault_source(
    library: str,
    includes: str,
    declarations: dict[str, dict[str, Any]],
    names: list[str],
    platform_faults: dict[str, dict[str, list[str]]],
    failure_contract: dict[str, dict[str, str]],
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
        fault_test(
            name,
            declarations[name],
            platform_faults.get(name, []),
            failure_contract[name],
        )
        for name in names
    )
    calls = "\n".join(
        f"    test_{name}(env, err{fault_test_call_suffix(declarations[name])});"
        for name in names
    )
    return f"""#include <errno.h>
#include <arpa/inet.h>
#include <fmtmsg.h>
#include <fnmatch.h>
{includes}
#include <p101_env/env.h>
#include <p101_error/error.h>
#include <math.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;
static size_t fault_resource_events;
static FILE *outcome_stream;

#define P101_TEST_ERRNO_SENTINEL 0x5A5A

#ifdef __linux__
    #define P101_TEST_PLATFORM "linux"
#elif defined(__APPLE__)
    #define P101_TEST_PLATFORM "macos"
#elif defined(__FreeBSD__)
    #define P101_TEST_PLATFORM "freebsd"
#else
    #define P101_TEST_PLATFORM "posix"
#endif

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
    int code;
}};

static void write_outcome(const char *wrapper,
                          const char *domain,
                          const char *symbol,
                          int code,
                          int passed)
{{
    int written;

    if(outcome_stream == NULL)
    {{
        return;
    }}
    written = fprintf(outcome_stream,
                      "P101WRAPPER\\t1\\tFAULT\\t%s\\t{library}\\t%s\\t%s\\t%s\\t%d\\t%s\\n",
                      P101_TEST_PLATFORM,
                      wrapper,
                      domain,
                      symbol,
                      code,
                      passed ? "PASS" : "FAIL");
    if(written < 0 || fflush(outcome_stream) != 0)
    {{
        fprintf(stderr, "FAIL: cannot write wrapper outcome receipt\\n");
        failures++;
    }}
}}

static int fail_next_call(const struct p101_env *env, const char *call_name, void *user_data)
{{
    struct fault_state *state;

    (void)env;
    (void)call_name;
    state = user_data;
    state->checks++;
    return state->code;
}}

static void count_fd_event(const struct p101_env *env,
                           p101_env_fd_event event,
                           int fd,
                           const char *file_name,
                           const char *function_name,
                           int line_number,
                           void *user_data)
{{
    (void)env;
    (void)event;
    (void)fd;
    (void)file_name;
    (void)function_name;
    (void)line_number;
    (void)user_data;
    fault_resource_events++;
}}

static void count_alloc_event(const struct p101_env *env,
                              p101_env_alloc_event event,
                              const void *ptr,
                              const void *new_ptr,
                              size_t size,
                              const char *file_name,
                              const char *function_name,
                              int line_number,
                              void *user_data)
{{
    (void)env;
    (void)event;
    (void)ptr;
    (void)new_ptr;
    (void)size;
    (void)file_name;
    (void)function_name;
    (void)line_number;
    (void)user_data;
    fault_resource_events++;
}}

static void count_resource_event(const struct p101_env *env,
                                 p101_env_resource_kind event,
                                 const char *resource_class,
                                 const char *resource_id,
                                 const char *related_id,
                                 size_t size,
                                 const char *metadata,
                                 const char *file_name,
                                 const char *function_name,
                                 int line_number,
                                 void *user_data)
{{
    (void)env;
    (void)event;
    (void)resource_class;
    (void)resource_id;
    (void)related_id;
    (void)size;
    (void)metadata;
    (void)file_name;
    (void)function_name;
    (void)line_number;
    (void)user_data;
    fault_resource_events++;
}}

{tests}
int main(void)
{{
    const char        *outcome_path;
    struct p101_error *err;
    struct p101_env   *env;

    outcome_path = getenv("P101_WRAPPER_OUTCOME_LOG");
    if(outcome_path != NULL && outcome_path[0] != '\\0')
    {{
        outcome_stream = fopen(outcome_path, "a");
        if(outcome_stream == NULL)
        {{
            fprintf(stderr, "FAIL: cannot open wrapper outcome receipt\\n");
            return EXIT_FAILURE;
        }}
    }}
    err = p101_error_create(false);
    if(err == NULL)
    {{
        if(outcome_stream != NULL)
        {{
            (void)fclose(outcome_stream);
        }}
        return EXIT_FAILURE;
    }}
    env = p101_env_create(err, NULL);
    if(env == NULL)
    {{
        p101_error_destroy(err);
        if(outcome_stream != NULL)
        {{
            (void)fclose(outcome_stream);
        }}
        return EXIT_FAILURE;
    }}
    p101_env_set_fd_observer(env, count_fd_event, NULL);
    p101_env_set_alloc_observer(env, count_alloc_event, NULL);
    p101_env_set_resource_observer(env, count_resource_event, NULL);
{calls}
    p101_env_destroy(env);
    p101_error_destroy(err);
    if(outcome_stream != NULL && fclose(outcome_stream) != 0)
    {{
        fprintf(stderr, "FAIL: cannot close wrapper outcome receipt\\n");
        failures++;
    }}
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


def formatted_source(formatter: str, path: Path, text: str) -> str:
    result = subprocess.run(
        [
            formatter,
            "--style=file",
            f"--assume-filename={path}",
        ],
        cwd=WORKSPACE,
        input=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"clang-format failed for {path}: {result.stderr.strip()}"
        )
    return result.stdout


def write_outputs(clang: str, clang_format: str, check: bool) -> int:
    libraries = active_libraries()
    include_dirs = sorted(
        path
        for path in LIBRARIES.glob("lib_*/include")
        if path.is_dir()
    )
    formatter = shutil.which(clang_format)
    if formatter is None:
        raise RuntimeError(
            f"cannot find clang-format executable {clang_format!r}"
        )
    drift: list[Path] = []
    fault_contract = load_contract(PLATFORM_FAULTS_PATH)
    outcome_contract = json.loads(
        OUTCOME_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    if outcome_contract.get("schema") != "p101-wrapper-outcome-contract-v1":
        raise RuntimeError("unsupported wrapper outcome contract")
    outcome_apis = outcome_contract.get("apis", {})
    valid_outcome_classes = {
        "direct-hard-failure",
        "short-partial-result",
        "delegated-failure",
        "deterministic-rejection",
        "genuinely-infallible",
        "non-returning-cleanup",
    }
    failure_contract: dict[str, Any] = {
        "schema": "p101-wrapper-failure-contract-v1",
        "semantics": {
            "error_object": "exact-injected-code-and-domain",
            "errno": "preserved",
            "fault_boundary": "before-observable-work",
            "writable_arguments": (
                "unchanged-by-early-return-with-portable-runtime-canaries"
            ),
            "resource_events": "none",
        },
        "wrappers": {},
    }
    wrapper_errors: dict[str, dict[str, list[str]]] = {}
    admitted_apis: set[str] = set()
    for wrapper, binding in fault_contract["wrappers"].items():
        function = binding.get("function")
        if function is None:
            wrapper_errors[wrapper] = {
                platform_name: []
                for platform_name in ("linux", "macos", "freebsd", "posix")
            }
            continue
        wrapper_errors[wrapper] = {}
        for platform_name in ("linux", "macos", "freebsd"):
            errors, _domain, _selection, _source, _coverage = (
                effective_fault_selection(
                    fault_contract,
                    function,
                    platform_name,
                )
            )
            wrapper_errors[wrapper][platform_name] = errors
        errors, _domain, _selection, _source, _coverage = (
            effective_fault_selection(
                fault_contract,
                function,
                None,
            )
        )
        wrapper_errors[wrapper]["posix"] = errors

    for library, (repo, library_rows) in sorted(libraries.items()):
        admitted = {row["function"] for row in library_rows}
        admitted_apis.update(admitted)
        declarations = function_definitions(clang, library_rows, include_dirs)
        sources = {
            row["function"]: WORKSPACE / row["current_source"]
            for row in library_rows
        }
        source_names = {
            row["function"]: row["current_source"]
            for row in library_rows
        }
        manifest_path = repo / "test" / "unit-test-manifest.tsv"
        fault_calls = {"p101_env_check_fault", "p101_env_check_fault_action"}
        faultable = {
            name
            for name, declaration in declarations.items()
            if referenced_names(declaration) & fault_calls
        }
        fault_names = sorted(admitted & faultable)
        behavior_names = sorted(admitted - faultable)
        for name in sorted(admitted):
            outcome = outcome_apis.get(name)
            if outcome is None:
                raise RuntimeError(
                    f"{name}: absent from explicit wrapper outcome contract"
                )
            if outcome.get("library") != library:
                raise RuntimeError(
                    f"{name}: outcome contract assigns "
                    f"{outcome.get('library')!r}, expected {library!r}"
                )
            if outcome.get("source") != source_names[name]:
                raise RuntimeError(
                    f"{name}: outcome contract source differs from the "
                    "public API manifest"
                )
            expected_role = fault_contract["wrappers"][name].get("role")
            if outcome.get("role") != expected_role:
                raise RuntimeError(
                    f"{name}: outcome contract role differs "
                    f"(expected {expected_role!r})"
                )
            if not outcome.get("rationale"):
                raise RuntimeError(
                    f"{name}: outcome contract lacks a rationale"
                )
            classification = outcome.get("classification")
            if classification not in valid_outcome_classes:
                raise RuntimeError(
                    f"{name}: invalid outcome classification "
                    f"{classification!r}"
                )
            declaration = declarations[name]
            accepts_error = accepts_error_parameter(declaration)
            if outcome.get("accepts_error") is not accepts_error:
                raise RuntimeError(
                    f"{name}: outcome contract accepts_error differs "
                    "from its public declaration"
                )
            references = referenced_names(declaration)
            has_hard_boundary = "p101_env_check_fault" in references
            has_action_boundary = (
                "p101_env_check_fault_action" in references
            )
            expected_class = (
                "short-partial-result"
                if has_action_boundary
                else "direct-hard-failure"
                if has_hard_boundary
                else None
            )
            if expected_class is not None and classification != expected_class:
                raise RuntimeError(
                    f"{name}: implementation has {expected_class}, but "
                    f"outcome contract declares {classification}"
                )
            if expected_class is None and classification in {
                "direct-hard-failure",
                "short-partial-result",
            }:
                raise RuntimeError(
                    f"{name}: outcome contract declares {classification} "
                    "without the corresponding direct fault boundary"
                )
            if accepts_error and classification not in {
                "direct-hard-failure",
                "short-partial-result",
            }:
                raise RuntimeError(
                    f"{name}: APIs accepting p101_error must be directly "
                    "injectable"
                )
            native_function = fault_contract["wrappers"][name].get(
                "function"
            )
            if (
                expected_role == "native-wrapper"
                and has_documented_faults(
                    fault_contract,
                    native_function,
                )
                and not accepts_error
            ):
                raise RuntimeError(
                    f"{name}: {native_function} has documented failure "
                    "outcomes but the wrapper does not accept p101_error"
                )
            if classification == "delegated-failure":
                delegated_targets = {
                    target
                    for target in called_functions(declaration)
                    if outcome_apis.get(target, {}).get("classification")
                    in {
                        "direct-hard-failure",
                        "short-partial-result",
                    }
                }
                if not delegated_targets:
                    raise RuntimeError(
                        f"{name}: delegated-failure has no called "
                        "injectable API"
                    )
            is_noreturn = any(
                node.get("kind") in {"C11NoReturnAttr", "NoReturnAttr"}
                for node in nodes(declaration)
            )
            if (
                is_noreturn
                and classification != "non-returning-cleanup"
            ):
                raise RuntimeError(
                    f"{name}: non-returning API must be classified as "
                    "non-returning-cleanup"
                )
        library_failures: dict[str, dict[str, str]] = {}
        for name in fault_names:
            validate_fault_boundary(declarations[name])
            failure = fault_return_contract(
                declarations[name],
                sources[name],
            )
            expected_domain = fault_domain(
                fault_contract,
                fault_contract["wrappers"][name].get("function"),
            )
            if failure["error_domain"] != expected_domain:
                raise RuntimeError(
                    f"{name}: injected {failure['error_domain']} failure "
                    f"does not match documented {expected_domain} domain"
                )
            parameters = [
                child
                for child in declarations[name].get("inner", [])
                if child.get("kind") == "ParmVarDecl"
            ]
            canary_arguments = [
                parameter.get("name", f"argument-{index}")
                for index, parameter in enumerate(parameters)
                if parameter.get("name") not in {"env", "err"}
                and writable_fixture(parameter, index)[0]
            ]
            library_failures[name] = failure
            failure_contract["wrappers"][name] = {
                "library": library,
                "error_domain": expected_domain,
                "return_kind": failure["kind"],
                "return_expression": failure["expression"],
                "errno": "preserved",
                "fault_boundary": "before-observable-work",
                "fault_modes": (
                    ["error", "short"]
                    if "p101_env_check_fault_action"
                    in referenced_names(declarations[name])
                    else ["error"]
                ),
                "runtime_canary_arguments": canary_arguments,
                "writable_arguments": "unchanged",
                "resource_events": "none",
            }
        test_dir = repo / "test"
        fault_path = test_dir / "test_fault_wrappers.c"
        cmake_uses_fault_test = "test_fault_wrappers" in (
            test_dir / "CMakeLists.txt"
        ).read_text(encoding="utf-8", errors="replace")
        if fault_names or cmake_uses_fault_test:
            expected_fault_source = formatted_source(
                formatter,
                fault_path,
                fault_source(
                    library,
                    public_header_includes(repo),
                    declarations,
                    fault_names,
                    wrapper_errors,
                    library_failures,
                ),
            )
            if check:
                actual_fault_source = (
                    fault_path.read_text(encoding="utf-8")
                    if fault_path.is_file()
                    else None
                )
                if actual_fault_source != expected_fault_source:
                    drift.append(fault_path)
            else:
                fault_path.write_text(
                    expected_fault_source,
                    encoding="utf-8",
                )
        elif fault_path.is_file():
            if check:
                drift.append(fault_path)
            else:
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
        expected_manifest = "".join(manifest)
        if check:
            actual_manifest = (
                manifest_path.read_text(encoding="utf-8")
                if manifest_path.is_file()
                else None
            )
            if actual_manifest != expected_manifest:
                drift.append(manifest_path)
        else:
            manifest_path.write_text(
                expected_manifest,
                encoding="utf-8",
            )
        print(
            f"{library}: {len(fault_names)} injected-failure, "
            f"{len(behavior_names)} behavior tests"
        )
    extra_outcomes = set(outcome_apis) - admitted_apis
    if extra_outcomes:
        raise RuntimeError(
            "wrapper outcome contract contains non-public APIs: "
            + ", ".join(sorted(extra_outcomes))
        )
    expected_contract = (
        json.dumps(failure_contract, indent=2, sort_keys=True) + "\n"
    )
    if check:
        actual_contract = (
            FAILURE_CONTRACT_PATH.read_text(encoding="utf-8")
            if FAILURE_CONTRACT_PATH.is_file()
            else None
        )
        if actual_contract != expected_contract:
            drift.append(FAILURE_CONTRACT_PATH)
    else:
        FAILURE_CONTRACT_PATH.write_text(
            expected_contract,
            encoding="utf-8",
        )
    if drift:
        for path in drift:
            print(f"FAIL: generated wrapper contract drift: {path}")
        return 1
    if check:
        print("generated wrapper failure contract is current")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clang", default=os.environ.get("CC", "clang"))
    parser.add_argument(
        "--clang-format",
        default=os.environ.get("CLANG_FORMAT", "clang-format"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated tests/manifests/contracts without writing",
    )
    args = parser.parse_args()
    return write_outputs(args.clang, args.clang_format, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
