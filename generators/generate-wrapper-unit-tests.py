#!/usr/bin/env python3
"""Generate deterministic injected-failure tests for public p101 APIs.

Admitted inputs:
  * api-manifest.tsv in each active library listed by repos.txt
  * each library's public headers and implementation sources
  * a Clang executable capable of producing JSON ASTs
  * wrapper-platform-faults.json (POSIX plus platform manual overrides)
  * wrapper-native-smoke-contract.json (declared deterministic native outcomes)
  * wrapper-portable-input-contract.json (the Linux/macOS/FreeBSD input
    compatibility intersection)

Outputs:
  * test/test_fault_wrappers.c in every active public-API library
  * test/unit-test-manifest.tsv in every active public-API library
  * one P101WRAPPER outcome record per executed platform fault when
    P101_WRAPPER_OUTCOME_LOG names a receipt file

Wrappers without an injected-failure path are assigned to test_behavior.c.
Those cases are intentionally handwritten because safe success-path fixtures
are part of each wrapper's contract and cannot be inferred from a signature.

Blind spot: injected failures prove propagation of documented error codes, not
that a real kernel can produce every condition on the current machine. Portable
input rules cover only values that are unconditionally invalid on at least one
supported platform; context-dependent values are deliberately delegated to the
native implementation.
check-wrapper-unit-tests.py and each repository's test.sh are the executable
receipts for the generated and handwritten cases.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
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
from c_facts import CFactError, acquire  # noqa: E402


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
FAULT_SEMANTICS_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-fault-semantics.json"
)
NATIVE_SMOKE_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-native-smoke-contract.json"
)
PORTABLE_INPUT_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-portable-input-contract.json"
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
PORTABLE_ZERO_TYPEDEFS = AGGREGATE_TYPEDEFS | {
    "iconv_t",
    "nl_catd",
    "pthread_t",
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
    "struct p101_fsm_effect_batch",
    "struct p101_fsm_info",
}
NATIVE_CALLBACKS = {
    "int (*)(const char *, int)": "native_path_error_callback",
    (
        "int (*)(const char *, const struct stat *, int, struct FTW *)"
    ): "native_nftw_callback",
    "int (*)(const struct dirent *)": "native_dirent_filter",
    (
        "int (*)(const struct dirent **, const struct dirent **)"
    ): "native_dirent_compare",
    "int (*)(const void *, const void *)": "native_compare_callback",
    "void (*)(int)": "native_signal_callback",
    "void (*)(void *)": "native_pointer_callback",
    "void (*)(void)": "native_void_callback",
    "void *(*)(void *)": "native_thread_callback",
}
NATIVE_CALLBACK_DEFINITIONS = {
    "native_path_error_callback": """
static int native_path_error_callback(const char *path, int error_code)
{
    (void)path;
    (void)error_code;
    return 0;
}
""",
    "native_nftw_callback": """
static int native_nftw_callback(const char *path,
                                const struct stat *status,
                                int type,
                                struct FTW *information)
{
    (void)path;
    (void)status;
    (void)type;
    (void)information;
    return 0;
}
""",
    "native_dirent_filter": """
static int native_dirent_filter(const struct dirent *entry)
{
    (void)entry;
    return 1;
}
""",
    "native_dirent_compare": """
static int native_dirent_compare(const struct dirent **left,
                                 const struct dirent **right)
{
    (void)left;
    (void)right;
    return 0;
}
""",
    "native_compare_callback": """
static int native_compare_callback(const void *left, const void *right)
{
    (void)left;
    (void)right;
    return 0;
}
""",
    "native_signal_callback": """
static void native_signal_callback(int signal_number)
{
    (void)signal_number;
}
""",
    "native_pointer_callback": """
static void native_pointer_callback(void *value)
{
    (void)value;
}
""",
    "native_void_callback": """
static void native_void_callback(void)
{
}
""",
    "native_thread_callback": """
static void *native_thread_callback(void *value)
{
    return value;
}
""",
    "native_condition_signal_thread": """
struct native_condition_signal_context
{
    pthread_cond_t *condition;
    pthread_mutex_t *mutex;
    int              status;
};

static void *native_condition_signal_thread(void *value)
{
    struct native_condition_signal_context *context = value;
    int                                     lock_status;
    int                                     signal_status = 0;
    int                                     unlock_status = 0;

    lock_status = pthread_mutex_lock(context->mutex);
    if(lock_status == 0)
    {
        signal_status = pthread_cond_signal(context->condition);
        unlock_status = pthread_mutex_unlock(context->mutex);
    }
    context->status = lock_status != 0     ? lock_status
                      : signal_status != 0 ? signal_status
                                           : unlock_status;
    return NULL;
}
""",
}
NATIVE_PTHREAD_OBJECTS = {
    "pthread_attr_t": (
        "pthread_attr_init",
        "pthread_attr_destroy",
        "c:@F@p101_pthread_attr_init",
        "c:@F@p101_pthread_attr_destroy",
        "",
    ),
    "pthread_cond_t": (
        "pthread_cond_init",
        "pthread_cond_destroy",
        "c:@F@p101_pthread_cond_init",
        "c:@F@p101_pthread_cond_destroy",
        ", NULL",
    ),
    "pthread_condattr_t": (
        "pthread_condattr_init",
        "pthread_condattr_destroy",
        "c:@F@p101_pthread_condattr_init",
        "c:@F@p101_pthread_condattr_destroy",
        "",
    ),
    "pthread_mutex_t": (
        "pthread_mutex_init",
        "pthread_mutex_destroy",
        "c:@F@p101_pthread_mutex_init",
        "c:@F@p101_pthread_mutex_destroy",
        ", NULL",
    ),
    "pthread_mutexattr_t": (
        "pthread_mutexattr_init",
        "pthread_mutexattr_destroy",
        "c:@F@p101_pthread_mutexattr_init",
        "c:@F@p101_pthread_mutexattr_destroy",
        "",
    ),
    "pthread_rwlock_t": (
        "pthread_rwlock_init",
        "pthread_rwlock_destroy",
        "c:@F@p101_pthread_rwlock_init",
        "c:@F@p101_pthread_rwlock_destroy",
        ", NULL",
    ),
    "pthread_rwlockattr_t": (
        "pthread_rwlockattr_init",
        "pthread_rwlockattr_destroy",
        "c:@F@p101_pthread_rwlockattr_init",
        "c:@F@p101_pthread_rwlockattr_destroy",
        "",
    ),
}

UNSAFE_NATIVE_CALL_USRS = {
    "c:@F@__builtin___sprintf_chk",
    "c:@F@__builtin___strcat_chk",
    "c:@F@__builtin___strcpy_chk",
    "c:@F@sprintf",
    "c:@F@strcat",
    "c:@F@strcpy",
}
DIRECT_WAIT_USRS = {"c:@F@waitpid"}
EINTR_SAFE_WAIT_ROLE = "SEMANTIC_ROLE:p101:test:eintr-safe-wait-adapter"
PROCESS_TERMINATION_USRS = {
    "c:@F@_Exit",
    "c:@F@_exit",
    "c:@F@abort",
    "c:@F@exit",
    "c:@F@quick_exit",
}
STATUS_BEARING_CLEANUP_USRS = {
    f"c:@F@{name}"
    for name in {
        "close",
        "closedir",
        "fclose",
        "msgctl",
        "pclose",
        "pthread_attr_destroy",
        "pthread_cancel",
        "pthread_cond_destroy",
        "pthread_cond_signal",
        "pthread_condattr_destroy",
        "pthread_create",
        "pthread_detach",
        "pthread_join",
        "pthread_key_delete",
        "pthread_mutex_destroy",
        "pthread_mutex_lock",
        "pthread_mutex_unlock",
        "pthread_mutexattr_destroy",
        "pthread_rwlock_destroy",
        "pthread_rwlock_rdlock",
        "pthread_rwlock_unlock",
        "pthread_rwlock_wrlock",
        "pthread_rwlockattr_destroy",
        "sem_close",
        "sem_unlink",
        "semctl",
        "shm_unlink",
        "shmctl",
        "shmdt",
        "sigprocmask",
        "snprintf",
        "unlink",
        "unsetenv",
        "waitpid",
    }
}


def normalized_c_type(qualified: str) -> str:
    """Normalize insignificant spelling without changing C qualifiers."""
    without_restrict = re.sub(r"\brestrict\b", "", qualified)
    return re.sub(r"\s+", " ", without_restrict).strip()


def function_pointer_type(qualified: str) -> bool:
    return re.search(r"\(\s*\*", qualified) is not None


def records(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def workspace_path(value: object) -> Path:
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (WORKSPACE / path).resolve()


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


def node_offsets(node: dict[str, Any]) -> tuple[int, int] | None:
    """Return one AST node's expansion extent without interpreting spellings."""
    extent = node.get("range", {})
    begin = extent.get("begin", {})
    end = extent.get("end", {})
    begin = begin.get("expansionLoc", begin)
    end = end.get("expansionLoc", end)
    start = begin.get("offset")
    finish = end.get("offset")
    token_length = end.get("tokLen", 0)
    if not all(
        isinstance(value, int)
        for value in (start, finish, token_length)
    ):
        return None
    return start, finish + token_length


def function_declarations(
    ast: dict[str, Any],
    identities_by_extent: dict[tuple[int, int], tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    declarations: dict[str, dict[str, Any]] = {}
    for node in nodes(ast):
        if node.get("kind") != "FunctionDecl":
            continue
        extent = node_offsets(node)
        identity = identities_by_extent.get(extent) if extent is not None else None
        if identity is None:
            continue
        name, usr = identity
        is_definition = any(
            child.get("kind") == "CompoundStmt"
            for child in node.get("inner", [])
        )
        if not is_definition:
            continue
        existing = declarations.get(name)
        existing_is_definition = existing is not None and any(
            child.get("kind") == "CompoundStmt"
            for child in existing.get("inner", [])
        )
        if existing is None or (is_definition and not existing_is_definition):
            node["_p101_function_usr"] = usr
            declarations[name] = node
    return declarations


def semantic_definition_extents(
    source: Path,
    admitted_by_usr: dict[str, str],
    semantic_facts: list[dict[str, object]],
) -> dict[tuple[int, int], tuple[str, str]]:
    """Bind definitions using the protocol's declaration bit and stable USR."""
    return {
        (int(fact["start"]), int(fact["end"])): (
            admitted_by_usr[str(fact["usr"])],
            str(fact["usr"]),
        )
        for fact in semantic_facts
        if fact.get("kind") == "FUNCTION"
        and not fact.get("is_declaration")
        and workspace_path(fact.get("path", "")) == source
        and str(fact.get("usr", "")) in admitted_by_usr
    }


def function_definitions(
    clang: str,
    rows: list[dict[str, str]],
    include_dirs: list[Path],
    semantic_facts: list[dict[str, object]],
) -> dict[str, dict[str, Any]]:
    by_source: dict[Path, dict[str, str]] = defaultdict(dict)
    for row in rows:
        by_source[(WORKSPACE / row["current_source"]).resolve()][
            row["function_usr"]
        ] = row["function"]

    definitions: dict[str, dict[str, Any]] = {}
    for source, admitted_by_usr in sorted(by_source.items()):
        identities_by_extent = semantic_definition_extents(
            source,
            admitted_by_usr,
            semantic_facts,
        )
        found_usrs = {usr for _name, usr in identities_by_extent.values()}
        missing_usrs = admitted_by_usr.keys() - found_usrs
        if missing_usrs:
            raise RuntimeError(
                f"{source.relative_to(WORKSPACE)} lacks semantic definitions "
                f"for {', '.join(sorted(missing_usrs))}"
            )
        ast = clang_ast(clang, source, include_dirs)
        declarations = function_declarations(ast, identities_by_extent)
        missing = set(admitted_by_usr.values()) - declarations.keys()
        if missing:
            raise RuntimeError(
                f"{source.relative_to(WORKSPACE)} lacks identity-aligned AST "
                "definitions for "
                f"{', '.join(sorted(missing))}"
            )
        for declaration in declarations.values():
            declaration["_p101_source"] = str(source)
        definitions.update(declarations)
    return definitions


def return_type(declaration: dict[str, Any]) -> str:
    qualified = declaration["type"]["qualType"]
    marker = qualified.find("(")
    if marker < 0:
        raise RuntimeError(f"cannot determine return type from {qualified!r}")
    result = qualified[:marker].strip()
    return "bool" if result == "_Bool" else result


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


@cache
def source_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def parameter_source_fragment(
    declaration: dict[str, Any],
    parameter: dict[str, Any],
) -> str:
    """Return the parameter declaration exactly as written in project source."""
    text = declaration.get("_p101_source_text")
    if not isinstance(text, str):
        path = declaration.get("_p101_source")
        if not isinstance(path, str):
            return ""
        text = source_text(path)
    parameter_range = parameter.get("range", {})
    begin = parameter_range.get("begin", {})
    end = parameter_range.get("end", {})
    begin = begin.get("expansionLoc", begin)
    end = end.get("expansionLoc", end)
    begin_offset = begin.get("offset")
    end_offset = end.get("offset")
    token_length = end.get("tokLen", 0)
    if not all(
        isinstance(value, int)
        for value in (begin_offset, end_offset, token_length)
    ):
        return ""
    return text[begin_offset : end_offset + token_length]


def is_va_list_parameter(
    declaration: dict[str, Any],
    parameter: dict[str, Any],
) -> bool:
    """Recognize va_list by its public source type or resolved AST type."""
    type_info = parameter.get("type", {})
    qualified = type_info.get("qualType", "")
    desugared = type_info.get("desugaredQualType", "")
    fragment = parameter_source_fragment(declaration, parameter)
    return any(
        re.search(r"\bva_list\b", spelling) is not None
        for spelling in (fragment, qualified, desugared)
    )


def has_va_list_parameter(declaration: dict[str, Any]) -> bool:
    """Return whether a wrapper accepts an already-started va_list.

    A va_list is not an ordinary zero-initializable object. Generated tests
    must create it in a variadic function with va_start(), even when injected
    failure is expected to return before the wrapped function consumes it.
    """
    return any(
        child.get("kind") == "ParmVarDecl"
        and is_va_list_parameter(declaration, child)
        for child in declaration.get("inner", [])
    )


def canonical_parameter_type(parameter: dict[str, Any]) -> str:
    type_info = parameter.get("type", {})
    return normalized_c_type(
        type_info.get("desugaredQualType", type_info.get("qualType", ""))
    )


def is_env_parameter(parameter: dict[str, Any]) -> bool:
    return canonical_parameter_type(parameter) in {
        "const struct p101_env *",
        "struct p101_env *",
    }


def is_error_parameter(parameter: dict[str, Any]) -> bool:
    return canonical_parameter_type(parameter) == "struct p101_error *"


def accepts_error_parameter(declaration: dict[str, Any]) -> bool:
    return any(
        child.get("kind") == "ParmVarDecl"
        and is_error_parameter(child)
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


def argument_expression(
    parameter: dict[str, Any],
    declaration: dict[str, Any] | None = None,
) -> str:
    type_info = parameter["type"]
    qualified = type_info["qualType"]
    desugared = type_info.get("desugaredQualType", qualified)
    if is_env_parameter(parameter):
        return "env"
    if is_error_parameter(parameter):
        return "err"
    if declaration is not None and is_va_list_parameter(
        declaration,
        parameter,
    ):
        return "arguments"
    if "va_list" in qualified or "va_list" in desugared:
        return "arguments"
    if "*" in qualified or "[" in qualified:
        return "NULL"
    if qualified in PORTABLE_ZERO_TYPEDEFS:
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


def referenced_declaration_ids(node: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    for candidate in nodes(node):
        referenced = candidate.get("referencedDecl", {})
        identity = referenced.get("id") if isinstance(referenced, dict) else None
        if isinstance(identity, str) and identity:
            identities.add(identity)
    return identities


def expression_source(node: dict[str, Any], source: Path) -> str | None:
    extent = node_offsets(node)
    if extent is None:
        return None
    start, end = extent
    return source.read_text(encoding="utf-8", errors="replace")[
        start:end
    ].strip()


def semantic_single_exit_fault_result(
    declaration: dict[str, Any],
    source: Path,
    wrapper_usr: str,
    calls: list[dict[str, object]],
    selector_usrs: set[str],
) -> str | None:
    """Find the value committed by a selector-controlled failure branch.

    The selector call is identified by its resolved USR and source extent.
    The result object is identified by the declaration referenced from the
    function's final return. Local variable and function spellings are never
    consulted.
    """
    selector_extents = {
        (int(call.get("start", -1)), int(call.get("end", -1)))
        for call in calls
        if call.get("caller_usr") == wrapper_usr
        and call.get("usr") in selector_usrs
    }
    selector_result_identities: set[str] = set()
    for assignment in nodes(declaration):
        children = assignment.get("inner", [])
        assignment_extent = node_offsets(assignment)
        if (
            assignment.get("kind") != "BinaryOperator"
            or assignment.get("opcode") != "="
            or len(children) != 2
            or assignment_extent is None
            or not any(
                assignment_extent[0] <= start
                and end <= assignment_extent[1]
                for start, end in selector_extents
            )
        ):
            continue
        selector_result_identities.update(
            referenced_declaration_ids(children[0])
        )
    returns = [
        node
        for node in nodes(declaration)
        if node.get("kind") == "ReturnStmt"
    ]
    if not selector_extents or not returns:
        return None
    final_return = max(
        returns,
        key=lambda node: (node_offsets(node) or (-1, -1))[0],
    )
    result_identities = referenced_declaration_ids(final_return)
    if len(result_identities) != 1:
        return None
    result_identity = next(iter(result_identities))
    for branch in nodes(declaration):
        if branch.get("kind") != "IfStmt":
            continue
        branch_extent = node_offsets(branch)
        branch_children = branch.get("inner", [])
        condition_identities = (
            referenced_declaration_ids(branch_children[0])
            if branch_children
            else set()
        )
        selector_is_nested = branch_extent is not None and any(
            branch_extent[0] <= start and end <= branch_extent[1]
            for start, end in selector_extents
        )
        selector_result_is_tested = bool(
            condition_identities & selector_result_identities
        )
        if not selector_is_nested and not selector_result_is_tested:
            continue
        for assignment in nodes(branch):
            children = assignment.get("inner", [])
            if (
                assignment.get("kind") != "BinaryOperator"
                or assignment.get("opcode") != "="
                or len(children) != 2
                or result_identity
                not in referenced_declaration_ids(children[0])
            ):
                continue
            return expression_source(children[1], source)
    return None


def indexed_fallback_expression(
    declaration: dict[str, Any],
    macro_name: str,
    arguments: list[str],
) -> str | None:
    """Resolve an explicitly indexed fallback from parameter types.

    The macro name carries the zero-based parameter position. The source
    spelling and parameter name are deliberately irrelevant: the generated
    fixture comes from the selected parameter's resolved C type.
    """
    fallback_match = re.fullmatch(
        r"(?:P101_FAULT_RETURN_PARSED|P101_PARSE_PROLOGUE)_ARG([0-9]+)",
        macro_name,
    )
    if fallback_match is None:
        return None
    expected_argument_count = (
        3 if macro_name.startswith("P101_PARSE_PROLOGUE_") else 4
    )
    if len(arguments) != expected_argument_count:
        raise RuntimeError(
            f"{declaration.get('name', '?')} uses {macro_name} "
            "with an invalid argument count"
        )
    parameters = [
        child
        for child in declaration.get("inner", [])
        if child.get("kind") == "ParmVarDecl"
    ]
    fallback_index = int(fallback_match.group(1))
    if fallback_index >= len(parameters):
        raise RuntimeError(
            f"{declaration.get('name', '?')} uses {macro_name}, "
            f"but has only {len(parameters)} parameters"
        )
    return argument_expression(parameters[fallback_index], declaration)


def fault_return_contract(
    declaration: dict[str, Any],
    source: Path,
    wrapper_usr: str,
    calls: list[dict[str, object]],
    selector_usrs: set[str],
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
            (
                r"\b(?:"
                r"P101_[A-Z0-9_]*FAULT[A-Z0-9_]*RETURN[A-Z0-9_]*"
                r"|P101_PARSE_PROLOGUE_ARG[0-9]+"
                r")\s*\("
            ),
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
            fallback_expression = indexed_fallback_expression(
                declaration,
                macro_name,
                arguments,
            )
            if fallback_expression is not None:
                return {
                    "kind": "value",
                    "expression": fallback_expression,
                    "error_domain": domain,
                }
            if arguments:
                return {
                    "kind": "value",
                    "expression": arguments[-1],
                    "error_domain": domain,
                }
        expression = semantic_single_exit_fault_result(
            declaration,
            source,
            wrapper_usr,
            calls,
            selector_usrs,
        )
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
                fallback_expression = indexed_fallback_expression(
                    declaration,
                    macro_name,
                    arguments,
                )
                if fallback_expression is not None:
                    return {
                        "kind": "value",
                        "expression": fallback_expression,
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


def validate_fault_boundary(
    declaration: dict[str, Any],
    wrapper_usr: str,
    calls: list[dict[str, object]],
    selector_usrs: set[str],
    entry_trace_usr: str,
) -> None:
    """Require fault selection after entry tracing but before native work."""
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
    children = body.get("inner", [])
    wrapper_calls = [
        fact
        for fact in calls
        if fact.get("caller_usr") == wrapper_usr
    ]
    selector_starts = [
        int(fact["start"])
        for fact in wrapper_calls
        if fact.get("usr") in selector_usrs
    ]
    selector_start = min(selector_starts) if selector_starts else None
    fault_index = None
    if selector_start is not None:
        for index, child in enumerate(children):
            extent = node_offsets(child)
            if (
                extent is not None
                and extent[0] <= selector_start < extent[1]
            ):
                fault_index = index
                break
    if fault_index is None:
        raise RuntimeError(
            f"{declaration.get('name', '?')} has no fault boundary"
        )
    for child in children[:fault_index]:
        extent = node_offsets(child)
        child_calls = (
            []
            if extent is None
            else [
                fact
                for fact in wrapper_calls
                if extent[0] <= int(fact["start"]) < extent[1]
            ]
        )
        if child.get("kind") == "DeclStmt" and not child_calls:
            continue
        if (
            child_calls
            and all(fact.get("usr") == entry_trace_usr for fact in child_calls)
        ):
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
    declaration: dict[str, Any] | None = None,
) -> tuple[list[str], str, list[str]]:
    """Provide a canary for writable pointer arguments on the fault path."""
    if declaration is not None and is_va_list_parameter(
        declaration,
        parameter,
    ):
        return [], "arguments", []
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


def native_pointer_fixture(
    function_name: str,
    function_usr: str,
    parameter: dict[str, Any],
    index: int,
    declaration: dict[str, Any] | None = None,
) -> tuple[list[str], str, list[str], list[str]] | None:
    """Build a native-path fixture from the public source-declared C type.

    Variable names are deliberately ignored. Opaque resource types use an
    explicit type contract; complete object pointers use zero-initialized
    storage. Public aggregate tags are recovered from source so platform AST
    aliases cannot leak private implementation names into generated tests.
    Function-specific cleanup exceptions are limited to wrappers whose native
    operation itself consumes the typed resource.
    """
    qualified = parameter.get("type", {}).get("qualType", "")
    normalized = normalized_c_type(qualified)
    if function_pointer_type(normalized):
        callback = NATIVE_CALLBACKS.get(normalized)
        if callback is None:
            raise RuntimeError(
                f"{function_name}: native fixture has no callback contract "
                f"for {qualified!r}"
            )
        return [], callback, [], []
    if "*" not in qualified:
        return None
    pointer_depth = normalized.count("*")
    fixture = f"native_argument_{index}"

    if re.search(r"\bDBM\s*\*", normalized):
        declarations = [
            f"            char {fixture}_path[96];",
            f"            char {fixture}_path_db[96];",
            f"            char {fixture}_path_dir[96];",
            f"            char {fixture}_path_pag[96];",
            f"            DBM *{fixture};",
            f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}_path, "
            '"/tmp/p101-wrapper-dbm-%ld");',
            f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}_path_db, "
            '"/tmp/p101-wrapper-dbm-%ld.db");',
            f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}_path_dir, "
            '"/tmp/p101-wrapper-dbm-%ld.dir");',
            f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}_path_pag, "
            '"/tmp/p101-wrapper-dbm-%ld.pag");',
            f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path);",
            f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path_db);",
            f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path_dir);",
            f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path_pag);",
            "            if(!native_passed)",
            "            {",
            "                native_child_status = 77;",
            "                goto native_child_done_;",
            "            }",
            f"            {fixture} = dbm_open({fixture}_path, "
            "O_RDWR | O_CREAT, 0600);",
            f"            if({fixture} == NULL)",
            "            {",
            f"                P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path);",
            f"                P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path_db);",
            f"                P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path_dir);",
            f"                P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT({fixture}_path_pag);",
            "                _Exit(77);",
            "            }",
        ]
        cleanup = []
        if function_usr != "c:@F@p101_dbm_close":
            cleanup.append(f"            dbm_close({fixture});")
        cleanup.extend(
            [
                f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT("
                f"{fixture}_path);",
                f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT("
                f"{fixture}_path_db);",
                f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT("
                f"{fixture}_path_dir);",
                f"            P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT("
                f"{fixture}_path_pag);",
            ]
        )
        return declarations, fixture, [], cleanup

    if re.search(r"\bDIR\s*\*", normalized):
        declarations = [
            f"            DIR *{fixture} = opendir(\".\");",
            f"            if({fixture} == NULL)",
            "            {",
            "                _Exit(77);",
            "            }",
        ]
        cleanup = (
            []
            if function_usr == "c:@F@p101_closedir"
            else [
                f"            P101_NATIVE_CLEANUP_ERRNO(closedir({fixture}));"
            ]
        )
        return declarations, fixture, [], cleanup

    if "regex_t" in normalized and pointer_depth == 1:
        declarations = [f"            regex_t {fixture};"]
        if re.search(r"\bconst\b", normalized):
            declarations.extend(
                [
                    f"            if(regcomp(&{fixture}, \".*\", "
                    "REG_EXTENDED) != 0)",
                    "            {",
                    "                _Exit(77);",
                    "            }",
                ]
            )
        return declarations, f"&{fixture}", [], [
            f"            regfree(&{fixture});"
        ]

    if "struct ether_addr" in normalized and pointer_depth == 1:
        declarations = [
            f"            struct ether_addr *{fixture};",
            f"            {fixture} = p101_ether_aton("
            "native_env, native_err, \"00:00:00:00:00:00\");",
            f"            if({fixture} == NULL)",
            "            {",
            "                _Exit(77);",
            "            }",
        ]
        return declarations, fixture, [], []

    if "struct ifaddrs" in normalized and pointer_depth == 2:
        declarations = [f"            struct ifaddrs *{fixture} = NULL;"]
        return declarations, f"&{fixture}", [], [
            f"            if({fixture} != NULL)",
            "            {",
            f"                freeifaddrs({fixture});",
            "            }",
        ]

    pointee = normalized.rsplit("*", 1)[0].strip()
    bare_pointee = re.sub(
        r"\b(?:const|volatile|_Atomic)\b",
        "",
        pointee,
    ).strip()
    if bare_pointee == "pthread_once_t" and pointer_depth == 1:
        return [
            f"            pthread_once_t {fixture} = PTHREAD_ONCE_INIT;"
        ], f"&{fixture}", [], []

    pthread_contract = NATIVE_PTHREAD_OBJECTS.get(bare_pointee)
    if pthread_contract is not None and pointer_depth == 1:
        (
            initializer,
            destructor,
            initializer_usr,
            destructor_usr,
            initializer_suffix,
        ) = pthread_contract
        declarations = [f"            {bare_pointee} {fixture};"]
        cleanup: list[str] = []
        is_initializer = function_usr == initializer_usr
        is_destructor = function_usr == destructor_usr
        if not is_initializer:
            declarations.extend(
                [
                    f"            if({initializer}(&{fixture}"
                    f"{initializer_suffix}) != 0)",
                    "            {",
                    "                _Exit(77);",
                    "            }",
                ]
            )
        if is_initializer:
            cleanup.append(
                "            if(native_result == 0)\n"
                "            {\n"
                f"                P101_NATIVE_CLEANUP_STATUS("
                f"{destructor}(&{fixture}));\n"
                "            }"
            )
        elif is_destructor:
            cleanup.append(
                "            if(native_result != 0)\n"
                "            {\n"
                f"                P101_NATIVE_CLEANUP_STATUS("
                f"{destructor}(&{fixture}));\n"
                "            }"
            )
        else:
            cleanup.append(
                f"            P101_NATIVE_CLEANUP_STATUS("
                f"{destructor}(&{fixture}));"
            )
        return declarations, f"&{fixture}", [], cleanup

    if pointer_depth >= 2:
        pointee = normalized.rsplit("*", 1)[0].strip()
        if pointee == "char *const":
            return [
                f'            char *{fixture}[2] = '
                '{(char *)"p101", NULL};'
            ], fixture, [], []
        if pointee == "const char *":
            return [
                f'            const char *{fixture}[2] = {{"p101", NULL}};'
            ], fixture, [], []
        if pointee == "const wchar_t *":
            return [
                f'            const wchar_t *{fixture}[2] = '
                '{L"p101", NULL};'
            ], fixture, [], []
        # Preserve qualifiers on the pointed-to object. Removing the inner
        # pointer's `const` changes `char *const *` into incompatible
        # `char **`.
        element_type = pointee
        return [
            f"            {element_type} {fixture} = NULL;"
        ], f"&{fixture}", [], []

    if re.search(r"\bwchar_t\b", pointee):
        if re.search(r"\bconst\b", pointee):
            return None
        return [
            f"            wchar_t {fixture}[PATH_MAX] = {{0}};"
        ], fixture, [], []
    if re.search(r"\bchar\b", pointee):
        if re.search(r"\bconst\b", pointee):
            return None
        return [
            f"            char {fixture}[PATH_MAX] = {{0}};"
        ], fixture, [], []
    if re.sub(r"\b(?:const|volatile|_Atomic)\b", "", pointee).strip() == "void":
        if re.search(r"\bconst\b", pointee):
            return None
        return [
            f"            unsigned char {fixture}[4096] = {{0}};"
        ], fixture, [], []
    bare = re.sub(
        r"\b(?:const|volatile|restrict|_Atomic)\b",
        "",
        pointee,
    ).strip()
    if bare in OPAQUE_POINTEE_TYPES:
        return None
    if declaration is not None:
        fragment = parameter_source_fragment(declaration, parameter)
        public_aggregate = re.search(
            r"\b((?:struct|union)\s+[A-Za-z_][A-Za-z0-9_]*)\b",
            fragment,
        )
        if public_aggregate is not None and "*" in fragment:
            bare = public_aggregate.group(1)
    return [f"            {bare} {fixture} = {{0}};"], f"&{fixture}", [], []


def native_contract_fixture(
    function_name: str,
    function_usr: str,
    parameter: dict[str, Any],
    index: int,
) -> tuple[list[str], str, list[str], list[str]] | None:
    """Provide semantic fixtures that a C parameter type cannot express.

    C adjusts array parameters to pointers in the AST, and scalar types do
    not retain constraints such as POSIX alignment. These contracts are
    therefore identified by public API plus parameter position, never by a
    source-level variable name.
    """
    qualified = normalized_c_type(
        parameter.get("type", {}).get("qualType", "")
    )
    fixture = f"native_argument_{index}"

    decimal_text_apis = {
        "c:@F@p101_parse_char",
        "c:@F@p101_parse_in_port_t",
        "c:@F@p101_parse_int",
        "c:@F@p101_parse_int16_t",
        "c:@F@p101_parse_int32_t",
        "c:@F@p101_parse_int64_t",
        "c:@F@p101_parse_int8_t",
        "c:@F@p101_parse_long",
        "c:@F@p101_parse_long_long",
        "c:@F@p101_parse_positive_char",
        "c:@F@p101_parse_positive_int",
        "c:@F@p101_parse_positive_int16_t",
        "c:@F@p101_parse_positive_int32_t",
        "c:@F@p101_parse_positive_int64_t",
        "c:@F@p101_parse_positive_int8_t",
        "c:@F@p101_parse_positive_long",
        "c:@F@p101_parse_positive_long_long",
        "c:@F@p101_parse_positive_short",
        "c:@F@p101_parse_short",
        "c:@F@p101_parse_uint16_t",
        "c:@F@p101_parse_uint32_t",
        "c:@F@p101_parse_uint64_t",
        "c:@F@p101_parse_uint8_t",
        "c:@F@p101_parse_unsigned_char",
        "c:@F@p101_parse_unsigned_int",
        "c:@F@p101_parse_unsigned_long",
        "c:@F@p101_parse_unsigned_long_long",
        "c:@F@p101_parse_unsigned_short",
    }
    negative_decimal_text_apis = {
        "c:@F@p101_parse_negative_char",
        "c:@F@p101_parse_negative_int",
        "c:@F@p101_parse_negative_int16_t",
        "c:@F@p101_parse_negative_int32_t",
        "c:@F@p101_parse_negative_int64_t",
        "c:@F@p101_parse_negative_int8_t",
        "c:@F@p101_parse_negative_long",
        "c:@F@p101_parse_negative_long_long",
        "c:@F@p101_parse_negative_short",
    }
    if index == 2 and function_usr in decimal_text_apis:
        return [], '"1"', [], []
    if index == 2 and function_usr in negative_decimal_text_apis:
        return [], '"-1"', [], []
    if index == 2 and function_usr == "c:@F@p101_convert_address":
        return [], '"127.0.0.1"', [], []

    socket_pair_apis = {
        "c:@F@p101_getpeername",
        "c:@F@p101_recv",
        "c:@F@p101_recvfrom",
        "c:@F@p101_recvmsg",
        "c:@F@p101_send",
        "c:@F@p101_sendmsg",
        "c:@F@p101_sendto",
        "c:@F@p101_shutdown",
        "c:@F@p101_sockatmark",
    }
    if function_usr in socket_pair_apis:
        if index == 2:
            declarations = [
                f"            int {fixture}_pair[2] = {{-1, -1}};",
                f"            int {fixture}_status;",
                f"            {fixture}_status = socketpair("
                f"AF_UNIX, SOCK_STREAM, 0, {fixture}_pair);",
                f"            if({fixture}_status != 0)",
                "            {",
                "                native_child_status = 77;",
                "                goto native_child_done_;",
                "            }",
            ]
            setup = []
            if function_usr in {
                "c:@F@p101_recv",
                "c:@F@p101_recvfrom",
                "c:@F@p101_recvmsg",
            }:
                setup.extend(
                    [
                        f"            {fixture}_status = "
                        f"(int)send({fixture}_pair[1], \"p101\", 4U, 0);",
                        f"            if({fixture}_status != 4)",
                        "            {",
                        "                native_child_status = 77;",
                        "                goto native_child_done_;",
                        "            }",
                    ]
                )
            cleanup = [
                f"            P101_NATIVE_CLEANUP_ERRNO("
                f"close({fixture}_pair[0]));",
                f"            P101_NATIVE_CLEANUP_ERRNO("
                f"close({fixture}_pair[1]));",
            ]
            return declarations, f"{fixture}_pair[0]", setup, cleanup
        if function_usr in {
            "c:@F@p101_recv",
            "c:@F@p101_recvfrom",
            "c:@F@p101_send",
            "c:@F@p101_sendto",
        }:
            if (
                function_usr in {"c:@F@p101_send", "c:@F@p101_sendto"}
                and index == 3
            ):
                return [], '"p101"', [], []
            if index == 4:
                return [], "4U", [], []
            if index == 5:
                return [], "0", [], []
        if function_usr == "c:@F@p101_getpeername":
            if index == 3:
                return [
                    f"            struct sockaddr_storage {fixture} = {{0}};"
                ], f"(struct sockaddr *)&{fixture}", [], []
            if index == 4:
                return [
                    f"            socklen_t {fixture} = "
                    "(socklen_t)sizeof(struct sockaddr_storage);"
                ], f"&{fixture}", [], []
        if function_usr in {
            "c:@F@p101_recvfrom",
            "c:@F@p101_sendto",
        }:
            if index == 6:
                return [], "NULL", [], []
            if index == 7:
                return [], "NULL" if function_usr.endswith("recvfrom") else "0", [], []
        if function_usr == "c:@F@p101_shutdown" and index == 3:
            return [], "SHUT_RDWR", [], []
        if function_usr in {
            "c:@F@p101_recvmsg",
            "c:@F@p101_sendmsg",
        } and index == 3:
            declarations = [
                f"            char {fixture}_payload[4] = "
                f"{{'p', '1', '0', '1'}};",
                f"            struct iovec {fixture}_iov = {{",
                f"                {fixture}_payload,",
                f"                sizeof({fixture}_payload),",
                "            };",
                f"            struct msghdr {fixture} = {{0}};",
                f"            {fixture}.msg_iov = &{fixture}_iov;",
                f"            {fixture}.msg_iovlen = 1U;",
            ]
            return declarations, f"&{fixture}", [], []

    if function_usr == "c:@F@p101_socket":
        if index == 2:
            return [], "AF_INET", [], []
        if index == 3:
            return [], "SOCK_STREAM", [], []
        if index == 4:
            return [], "0", [], [
                "            if(native_result >= 0)",
                "            {",
                "                P101_NATIVE_CLEANUP_ERRNO(close(native_result));",
                "            }",
            ]

    if function_usr == "c:@F@p101_socketpair":
        if index == 2:
            return [], "AF_UNIX", [], []
        if index == 3:
            return [], "SOCK_STREAM", [], []
        if index == 4:
            return [], "0", [], []
        if index == 5:
            return [
                f"            int {fixture}[2] = {{-1, -1}};"
            ], fixture, [], [
                "            if(native_result == 0)",
                "            {",
                f"                P101_NATIVE_CLEANUP_ERRNO(close({fixture}[0]));",
                f"                P101_NATIVE_CLEANUP_ERRNO(close({fixture}[1]));",
                "            }",
            ]

    simple_socket_apis = {
        "c:@F@p101_bind",
        "c:@F@p101_getsockname",
        "c:@F@p101_getsockopt",
        "c:@F@p101_listen",
        "c:@F@p101_setsockopt",
    }
    if function_usr in simple_socket_apis:
        if index == 2:
            declarations = [
                f"            int {fixture};",
                f"            {fixture} = socket(AF_INET, SOCK_STREAM, 0);",
                f"            if({fixture} < 0)",
                "            {",
                "                native_child_status = 77;",
                "                goto native_child_done_;",
                "            }",
            ]
            if function_usr == "c:@F@p101_listen":
                declarations.extend(
                    [
                        f"            struct sockaddr_in {fixture}_address = "
                        "{0};",
                        f"            int {fixture}_bind_status;",
                        f"            {fixture}_address.sin_family = AF_INET;",
                        f"            {fixture}_address.sin_addr.s_addr = "
                        "htonl(INADDR_LOOPBACK);",
                        f"            {fixture}_address.sin_port = 0;",
                        f"            {fixture}_bind_status = bind("
                        f"{fixture}, "
                        f"(const struct sockaddr *)&{fixture}_address, "
                        f"(socklen_t)sizeof({fixture}_address));",
                        f"            if({fixture}_bind_status != 0)",
                        "            {",
                        "                native_child_status = 77;",
                        "                goto native_child_done_;",
                        "            }",
                    ]
                )
            return declarations, fixture, [], [
                f"            P101_NATIVE_CLEANUP_ERRNO(close({fixture}));"
            ]
        if function_usr == "c:@F@p101_bind":
            if index == 3:
                return [
                    f"            struct sockaddr_in {fixture} = {{0}};",
                    f"            {fixture}.sin_family = AF_INET;",
                    f"            {fixture}.sin_addr.s_addr = "
                    "htonl(INADDR_LOOPBACK);",
                    f"            {fixture}.sin_port = 0;",
                ], f"(const struct sockaddr *)&{fixture}", [], []
            if index == 4:
                return [], "(socklen_t)sizeof(struct sockaddr_in)", [], []
        if function_usr == "c:@F@p101_listen" and index == 3:
            return [], "1", [], []
        if function_usr == "c:@F@p101_getsockname":
            if index == 3:
                return [
                    f"            struct sockaddr_storage {fixture} = {{0}};"
                ], f"(struct sockaddr *)&{fixture}", [], []
            if index == 4:
                return [
                    f"            socklen_t {fixture} = "
                    "(socklen_t)sizeof(struct sockaddr_storage);"
                ], f"&{fixture}", [], []
        if function_usr == "c:@F@p101_getsockopt":
            if index == 3:
                return [], "SOL_SOCKET", [], []
            if index == 4:
                return [], "SO_TYPE", [], []
            if index == 5:
                return [
                    f"            int {fixture} = 0;"
                ], f"&{fixture}", [], []
            if index == 6:
                return [
                    f"            socklen_t {fixture} = "
                    "(socklen_t)sizeof(int);"
                ], f"&{fixture}", [], []
        if function_usr == "c:@F@p101_setsockopt":
            if index == 3:
                return [], "SOL_SOCKET", [], []
            if index == 4:
                return [], "SO_REUSEADDR", [], []
            if index == 5:
                return [
                    f"            int {fixture} = 1;"
                ], f"&{fixture}", [], []
            if index == 6:
                return [], "(socklen_t)sizeof(int)", [], []

    if function_usr in {"c:@F@p101_accept", "c:@F@p101_connect"}:
        if index == 2:
            listener = f"{fixture}_listener"
            client = f"{fixture}_client"
            address = f"{fixture}_address"
            declarations = [
                f"            int {listener};",
                f"            int {client};",
                f"            int {fixture}_status;",
                f"            socklen_t {fixture}_address_len;",
                f"            struct sockaddr_in {address} = {{0}};",
                f"            {listener} = socket(AF_INET, SOCK_STREAM, 0);",
                f"            if({listener} < 0)",
                "            {",
                "                native_child_status = 77;",
                "                goto native_child_done_;",
                "            }",
                f"            {address}.sin_family = AF_INET;",
                f"            {address}.sin_addr.s_addr = "
                "htonl(INADDR_LOOPBACK);",
                f"            {address}.sin_port = 0;",
                f"            {fixture}_status = bind("
                f"{listener}, (const struct sockaddr *)&{address}, "
                f"(socklen_t)sizeof({address}));",
                f"            if({fixture}_status != 0)",
                "            {",
                "                native_child_status = 77;",
                "                goto native_child_done_;",
                "            }",
                f"            {fixture}_status = listen({listener}, 1);",
                f"            if({fixture}_status != 0)",
                "            {",
                "                native_child_status = 77;",
                "                goto native_child_done_;",
                "            }",
                f"            {fixture}_address_len = "
                f"(socklen_t)sizeof({address});",
                f"            {fixture}_status = getsockname("
                f"{listener}, (struct sockaddr *)&{address}, "
                f"&{fixture}_address_len);",
                f"            if({fixture}_status != 0)",
                "            {",
                "                native_child_status = 77;",
                "                goto native_child_done_;",
                "            }",
                f"            {client} = socket(AF_INET, SOCK_STREAM, 0);",
                f"            if({client} < 0)",
                "            {",
                "                native_child_status = 77;",
                "                goto native_child_done_;",
                "            }",
            ]
            if function_usr == "c:@F@p101_accept":
                declarations.extend(
                    [
                        f"            {fixture}_status = connect("
                        f"{client}, (const struct sockaddr *)&{address}, "
                        f"{fixture}_address_len);",
                        f"            if({fixture}_status != 0)",
                        "            {",
                        "                native_child_status = 77;",
                        "                goto native_child_done_;",
                        "            }",
                    ]
                )
                expression = listener
                cleanup = [
                    "            if(native_result >= 0)",
                    "            {",
                    "                P101_NATIVE_CLEANUP_ERRNO("
                    "close(native_result));",
                    "            }",
                ]
            else:
                expression = client
                cleanup = []
            cleanup.extend(
                [
                    f"            P101_NATIVE_CLEANUP_ERRNO(close({client}));",
                    f"            P101_NATIVE_CLEANUP_ERRNO(close({listener}));",
                ]
            )
            return declarations, expression, [], cleanup
        if index == 3:
            if function_usr == "c:@F@p101_accept":
                return [
                    f"            struct sockaddr_storage {fixture} = {{0}};"
                ], f"(struct sockaddr *)&{fixture}", [], []
            return [], (
                "(const struct sockaddr *)&native_argument_2_address"
            ), [], []
        if index == 4:
            if function_usr == "c:@F@p101_accept":
                return [
                    f"            socklen_t {fixture} = "
                    "(socklen_t)sizeof(struct sockaddr_storage);"
                ], f"&{fixture}", [], []
            return [], "native_argument_2_address_len", [], []

    if function_usr in {
        "c:@F@p101_if_indextoname",
        "c:@F@p101_if_nametoindex",
    } and index == 2:
        declarations = [
            f"            struct if_nameindex *{fixture}_interfaces;",
            f"            {fixture}_interfaces = if_nameindex();",
            f"            if({fixture}_interfaces == NULL ||",
            f"               {fixture}_interfaces[0].if_index == 0U ||",
            f"               {fixture}_interfaces[0].if_name == NULL)",
            "            {",
            "                native_child_status = 77;",
            "                goto native_child_done_;",
            "            }",
        ]
        expression = (
            f"{fixture}_interfaces[0].if_index"
            if function_usr == "c:@F@p101_if_indextoname"
            else f"{fixture}_interfaces[0].if_name"
        )
        return declarations, expression, [], [
            f"            if_freenameindex({fixture}_interfaces);"
        ]
    if function_usr == "c:@F@p101_if_indextoname" and index == 3:
        return [
            f"            char {fixture}[IF_NAMESIZE] = {{0}};"
        ], fixture, [], []

    if function_usr == "c:@F@p101_getaddrinfo":
        if index == 2:
            return [], '"localhost"', [], []
        if index == 3:
            return [], '"0"', [], []
        if index == 4:
            return [], "NULL", [], []
        if index == 5:
            return [
                f"            struct addrinfo *{fixture} = NULL;"
            ], f"&{fixture}", [], [
                f"            if({fixture} != NULL)",
                "            {",
                f"                freeaddrinfo({fixture});",
                "            }",
            ]

    if function_usr == "c:@F@p101_getnameinfo":
        if index == 2:
            return [
                f"            struct sockaddr_in {fixture} = {{0}};",
                f"            {fixture}.sin_family = AF_INET;",
                f"            {fixture}.sin_addr.s_addr = "
                "htonl(INADDR_LOOPBACK);",
                f"            {fixture}.sin_port = htons(80U);",
            ], f"(const struct sockaddr *)&{fixture}", [], []
        if index == 3:
            return [], "(socklen_t)sizeof(struct sockaddr_in)", [], []
        if index == 4:
            return [
                f"            char {fixture}[NI_MAXHOST] = {{0}};"
            ], fixture, [], []
        if index == 5:
            return [], "NI_MAXHOST", [], []
        if index == 6:
            return [
                f"            char {fixture}[NI_MAXSERV] = {{0}};"
            ], fixture, [], []
        if index == 7:
            return [], "NI_MAXSERV", [], []
        if index == 8:
            return [], "NI_NUMERICHOST | NI_NUMERICSERV", [], []

    if function_usr == "c:@F@p101_ether_aton" and index == 2:
        return [], '"00:00:00:00:00:00"', [], []
    if function_usr == "c:@F@p101_ether_line" and index == 2:
        return [], '"00:00:00:00:00:00 localhost"', [], []

    if function_usr in {
        "c:@F@p101_inet_addr",
        "c:@F@p101_inet_aton",
        "c:@F@p101_inet_network",
    } and index == 2:
        return [], '"127.0.0.1"', [], []

    if function_usr in {
        "c:@F@p101_inet_net_ntop",
        "c:@F@p101_inet_net_pton",
        "c:@F@p101_inet_ntop",
        "c:@F@p101_inet_pton",
    }:
        if index == 2:
            return [], "AF_INET", [], []
        if (
            function_usr
            in {"c:@F@p101_inet_net_ntop", "c:@F@p101_inet_ntop"}
            and index == 3
        ):
            return [
                f"            struct in_addr {fixture} = {{0}};",
                f"            {fixture}.s_addr = htonl(INADDR_LOOPBACK);",
            ], f"&{fixture}", [], []
        if (
            function_usr
            in {"c:@F@p101_inet_net_pton", "c:@F@p101_inet_pton"}
            and index == 3
        ):
            return [], '"127.0.0.1"', [], []
        if function_usr == "c:@F@p101_inet_net_ntop" and index == 4:
            return [], "32", [], []
        if function_usr == "c:@F@p101_inet_net_pton" and index == 5:
            return [], "sizeof(struct in_addr)", [], []
        if function_usr == "c:@F@p101_inet_net_ntop" and index == 6:
            return [], "INET_ADDRSTRLEN", [], []
        if function_usr == "c:@F@p101_inet_ntop" and index == 5:
            return [], "INET_ADDRSTRLEN", [], []

    if function_usr in {
        "c:@F@p101_execv",
        "c:@F@p101_execve",
        "c:@F@p101_execvp",
    }:
        if index == 2:
            return [], (
                '"true"'
                if function_usr == "c:@F@p101_execvp"
                else '"/usr/bin/true"'
            ), [], []
        if index == 3:
            executable = (
                '"true"'
                if function_usr == "c:@F@p101_execvp"
                else '"/usr/bin/true"'
            )
            return [
                f"            char *{fixture}[2] = "
                f"{{(char *){executable}, NULL}};"
            ], fixture, [], []
        if index == 4:
            return [
                f"            char *{fixture}[2] = "
                '{(char *)"PATH=/usr/bin:/bin", NULL};'
            ], fixture, [], []

    if function_usr in {
        "c:@F@p101_posix_spawn",
        "c:@F@p101_posix_spawnp",
    }:
        if index == 2:
            return [
                f"            pid_t {fixture} = -1;"
            ], f"&{fixture}", [], [
                "            if(native_result == 0)",
                "            {",
                f"                if(native_waitpid_nointr({fixture}, NULL) "
                f"!= {fixture})",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                f'{function_name}: waitpid\\n");',
                "                    native_passed = false;",
                "                }",
                "            }",
            ]
        if index == 3:
            executable = (
                '"/usr/bin/true"'
                if function_usr == "c:@F@p101_posix_spawn"
                else '"true"'
            )
            return [], executable, [], []
        if index in {4, 5}:
            return [], "NULL", [], []
        if index == 6:
            executable = (
                '"/usr/bin/true"'
                if function_usr == "c:@F@p101_posix_spawn"
                else '"true"'
            )
            return [
                f"            char *{fixture}[2] = "
                f"{{(char *){executable}, NULL}};"
            ], fixture, [], []
        if index == 7:
            return [
                f"            char *{fixture}[2] = "
                '{(char *)"PATH=/usr/bin:/bin", NULL};'
            ], fixture, [], []

    if function_usr in {"c:@F@p101_pipe", "c:@F@p101_pipe2"} and index == 2:
        return [
            f"            int {fixture}[2] = {{-1, -1}};"
        ], fixture, [], [
            f"            if({fixture}[0] >= 0)",
            "            {",
            f"                P101_NATIVE_CLEANUP_ERRNO(close({fixture}[0]));",
            "            }",
            f"            if({fixture}[1] >= 0)",
            "            {",
            f"                P101_NATIVE_CLEANUP_ERRNO(close({fixture}[1]));",
            "            }",
        ]

    if function_usr == "c:@F@p101_posix_memalign":
        if index == 2:
            return [
                f"            void *{fixture} = NULL;"
            ], f"&{fixture}", [], [
                f"            free({fixture});"
            ]
        if index == 3:
            return [], "sizeof(void *)", [], []
        if index == 4:
            return [], "16U", [], []

    if function_usr == "c:@F@p101_setrlimit" and index == 2:
        return [], "-1", [], []

    if function_usr == "c:@F@p101_aio_fsync":
        if index == 2:
            return [], "O_SYNC", [], []
        if index == 3:
            return [
                f"            FILE *{fixture}_stream = tmpfile();",
                f"            struct aiocb {fixture} = {{0}};",
                f"            if({fixture}_stream == NULL)",
                "            {",
                "                _Exit(77);",
                "            }",
                f"            {fixture}.aio_fildes = "
                f"fileno({fixture}_stream);",
            ], f"&{fixture}", [], [
                f"            P101_NATIVE_CLEANUP_ERRNO("
                f"fclose({fixture}_stream));"
            ]

    if function_usr == "c:@F@p101_ftok":
        if index == 2:
            return [], '"."', [], []
        if index == 3:
            return [], "1", [], []

    if function_usr == "c:@F@p101_creat":
        if index == 2:
            return [], '"/dev/null"', [], [
                "            if(native_result >= 0)",
                "            {",
                "                P101_NATIVE_CLEANUP_ERRNO(close(native_result));",
                "            }",
            ]
        if index == 3:
            return [], "0600", [], []

    if function_usr == "c:@F@p101_open":
        if index == 2:
            return [], '"/dev/null"', [], [
                "            if(native_result >= 0)",
                "            {",
                "                P101_NATIVE_CLEANUP_ERRNO(close(native_result));",
                "            }",
            ]
        if index == 3:
            return [], "O_RDONLY", [], []

    if function_usr == "c:@F@p101_openat":
        if index == 2:
            return [], "AT_FDCWD", [], []
        if index == 3:
            return [], '"/dev/null"', [], [
                "            if(native_result >= 0)",
                "            {",
                "                P101_NATIVE_CLEANUP_ERRNO(close(native_result));",
                "            }",
            ]
        if index == 4:
            return [], "O_RDONLY", [], []

    native_fd_function_usrs = {
        "c:@F@p101_fcntl",
        "c:@F@p101_pwrite",
        "c:@F@p101_readv",
        "c:@F@p101_vdprintf",
        "c:@F@p101_write",
        "c:@F@p101_writev",
    }
    if function_usr in native_fd_function_usrs and index == 2:
        declarations = [
            f"            FILE *{fixture}_stream = tmpfile();",
            f"            if({fixture}_stream == NULL)",
            "            {",
            "                _Exit(77);",
            "            }",
        ]
        cleanup = [
            f"            P101_NATIVE_CLEANUP_ERRNO("
            f"fclose({fixture}_stream));"
        ]
        return (
            declarations,
            f"fileno({fixture}_stream)",
            [],
            cleanup,
        )

    if function_usr == "c:@F@p101_fcntl" and index == 3:
        return [], "F_GETFD", [], []

    if function_usr in {"c:@F@p101_readv", "c:@F@p101_writev"}:
        if index == 3:
            return [
                f"            unsigned char {fixture}_byte = 0U;",
                f"            struct iovec {fixture} = "
                f"{{&{fixture}_byte, 1U}};",
            ], f"&{fixture}", [], []
        if index == 4:
            return [], "1", [], []

    if function_usr == "c:@F@p101_fdopen":
        if index == 2:
            return [
                f"            int {fixture} = "
                'open("/dev/null", O_RDONLY);',
                f"            if({fixture} < 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ], fixture, [], [
                "            if(native_result != NULL)",
                "            {",
                "                P101_NATIVE_CLEANUP_ERRNO(fclose(native_result));",
                "            }",
                "            else",
                "            {",
                f"                P101_NATIVE_CLEANUP_ERRNO(close({fixture}));",
                "            }",
            ]
        if index == 3:
            return [], '"r"', [], []

    if function_usr == "c:@F@p101_fmemopen":
        if index == 2:
            return [
                f"            unsigned char {fixture}[16] = {{0}};"
            ], fixture, [], [
                "            if(native_result != NULL)",
                "            {",
                "                P101_NATIVE_CLEANUP_ERRNO(fclose(native_result));",
                "            }",
            ]
        if index == 3:
            return [], "16U", [], []
        if index == 4:
            return [], '"r+"', [], []

    if function_usr == "c:@F@p101_mkfifo":
        if index == 2:
            return [
                f"            char {fixture}[96];",
                f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}, "
                '"/tmp/p101-wrapper-fifo-%ld");',
                "            errno = 0;",
                f"            if(unlink({fixture}) != 0 && errno != ENOENT)",
                "            {",
                f'                fprintf(stderr, "native setup failed: '
                f'{function_name}: unlink: %s\\n", strerror(errno));',
                "                _Exit(77);",
                "            }",
            ], fixture, [], [
                f"            P101_NATIVE_CLEANUP_ERRNO(unlink({fixture}));"
            ]
        if index == 3:
            return [], "0600", [], []

    if function_usr == "c:@F@p101_msgget":
        if index == 2:
            return [], "IPC_PRIVATE", [], []
        if index == 3:
            return [], "IPC_CREAT | 0600", [], [
                "            if(native_result >= 0)",
                "            {",
                "                if(msgctl(native_result, IPC_RMID, NULL) != 0)",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_msgget: msgctl(IPC_RMID): %s\\n", strerror(errno));',
                "                    native_passed = false;",
                "                }",
                "            }",
            ]

    if function_usr in {
        "c:@F@p101_msgctl",
        "c:@F@p101_msgrcv",
        "c:@F@p101_msgsnd",
    }:
        if index == 2:
            return [
                f"            int {fixture} = "
                "msgget(IPC_PRIVATE, IPC_CREAT | 0600);",
                f"            if({fixture} < 0)",
                "            {",
                "                _Exit(77);",
                "            }",
                *(
                    [
                        f"            struct {{ long type; }} "
                        f"{fixture}_message = {{1L}};",
                        f"            if(msgsnd({fixture}, "
                        f"&{fixture}_message, 0U, 0) != 0)",
                        "            {",
                        f"                if(msgctl({fixture}, IPC_RMID, "
                        "NULL) != 0)",
                        "                {",
                        '                    fprintf(stderr, "native cleanup '
                        f'failed: {function_name}: msgctl(IPC_RMID): %s\\n", '
                        "strerror(errno));",
                        "                    native_child_status = "
                        "EXIT_FAILURE;",
                        "                    goto native_child_done_;",
                        "                }",
                        "                _Exit(77);",
                        "            }",
                    ]
                    if function_usr == "c:@F@p101_msgrcv"
                    else []
                ),
            ], fixture, [], (
                [
                    "            if(native_result != 0)",
                    "            {",
                    f"                if(msgctl({fixture}, IPC_RMID, NULL) "
                    "!= 0)",
                    "                {",
                    '                    fprintf(stderr, "native cleanup failed: '
                    'p101_msgctl: msgctl(IPC_RMID): %s\\n", '
                    "strerror(errno));",
                    "                    native_passed = false;",
                    "                }",
                    "            }",
                ]
                if function_usr == "c:@F@p101_msgctl"
                else [
                    f"            if(msgctl({fixture}, IPC_RMID, NULL) != 0)",
                    "            {",
                    '                fprintf(stderr, "native cleanup failed: '
                    f'{function_name}: msgctl(IPC_RMID): %s\\n", '
                    "strerror(errno));",
                    "                native_passed = false;",
                    "            }",
                ]
            )
        if function_usr == "c:@F@p101_msgctl":
            if index == 3:
                return [], "IPC_RMID", [], []
            if index == 4:
                return [], "NULL", [], []
        if function_usr in {
            "c:@F@p101_msgrcv",
            "c:@F@p101_msgsnd",
        } and index == 3:
            return [
                f"            struct {{ long type; }} {fixture} = {{1L}};"
            ], f"&{fixture}", [], []
        if function_usr in {
            "c:@F@p101_msgrcv",
            "c:@F@p101_msgsnd",
        } and index == 4:
            return [], "0U", [], []
        if function_usr == "c:@F@p101_msgrcv" and index in {5, 6}:
            return [], "0", [], []
        if function_usr == "c:@F@p101_msgsnd" and index == 5:
            return [], "0", [], []

    if function_usr == "c:@F@p101_semget":
        if index == 2:
            return [], "IPC_PRIVATE", [], []
        if index == 3:
            return [], "1", [], []
        if index == 4:
            return [], "IPC_CREAT | 0600", [], [
                "            if(native_result >= 0)",
                "            {",
                "                if(semctl(native_result, 0, IPC_RMID) != 0)",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_semget: semctl(IPC_RMID): %s\\n", strerror(errno));',
                "                    native_passed = false;",
                "                }",
                "            }",
            ]

    if function_usr in {
        "c:@F@p101_semctl",
        "c:@F@p101_semctl_arg",
        "c:@F@p101_semop",
    }:
        if index == 2:
            return [
                f"            int {fixture} = "
                "semget(IPC_PRIVATE, 1, IPC_CREAT | 0600);",
                f"            if({fixture} < 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ], fixture, [], (
                [
                    "            if(native_result != 0)",
                    "            {",
                    f"                if(semctl({fixture}, 0, IPC_RMID) != 0)",
                    "                {",
                    '                    fprintf(stderr, "native cleanup failed: '
                    'p101_semctl: semctl(IPC_RMID): %s\\n", '
                    "strerror(errno));",
                    "                    native_passed = false;",
                    "                }",
                    "            }",
                ]
                if function_usr == "c:@F@p101_semctl"
                else [
                    f"            if(semctl({fixture}, 0, IPC_RMID) != 0)",
                    "            {",
                    '                fprintf(stderr, "native cleanup failed: '
                    f'{function_name}: semctl(IPC_RMID): %s\\n", '
                    "strerror(errno));",
                    "                native_passed = false;",
                    "            }",
                ]
            )
        if index == 3:
            if function_usr == "c:@F@p101_semop":
                return [
                    f"            struct sembuf {fixture} = "
                    "{0U, 0, 0};"
                ], f"&{fixture}", [], []
            return [], "0", [], []
        if index == 4:
            if function_usr == "c:@F@p101_semctl":
                return [], "IPC_RMID", [], []
            if function_usr == "c:@F@p101_semctl_arg":
                return [], "SETVAL", [], []
            return [], "1U", [], []
        if function_usr == "c:@F@p101_semctl_arg" and index == 5:
            return [], "(union p101_semun){.val = 1}", [], []

    if function_usr == "c:@F@p101_shmget":
        if index == 2:
            return [], "IPC_PRIVATE", [], []
        if index == 3:
            return [], "1U", [], []
        if index == 4:
            return [], "IPC_CREAT | 0600", [], [
                "            if(native_result >= 0)",
                "            {",
                "                if(shmctl(native_result, IPC_RMID, NULL) != 0)",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_shmget: shmctl(IPC_RMID): %s\\n", strerror(errno));',
                "                    native_passed = false;",
                "                }",
                "            }",
            ]

    if function_usr in {
        "c:@F@p101_shmat",
        "c:@F@p101_shmctl",
        "c:@F@p101_shmdt",
    }:
        if (
            function_usr in {"c:@F@p101_shmat", "c:@F@p101_shmctl"}
            and index == 2
        ):
            return [
                f"            int {fixture} = "
                "shmget(IPC_PRIVATE, 1U, IPC_CREAT | 0600);",
                f"            if({fixture} < 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ], fixture, [], (
                [
                    "            if(native_result != 0)",
                    "            {",
                    f"                if(shmctl({fixture}, IPC_RMID, NULL) "
                    "!= 0)",
                    "                {",
                    '                    fprintf(stderr, "native cleanup failed: '
                    'p101_shmctl: shmctl(IPC_RMID): %s\\n", '
                    "strerror(errno));",
                    "                    native_passed = false;",
                    "                }",
                    "            }",
                ]
                if function_usr == "c:@F@p101_shmctl"
                else [
                    "            if(native_result != (void *)-1)",
                    "            {",
                    "                if(shmdt(native_result) != 0)",
                    "                {",
                    '                    fprintf(stderr, "native cleanup failed: '
                    f'{function_name}: shmdt: %s\\n", strerror(errno));',
                    "                    native_passed = false;",
                    "                }",
                    "            }",
                    f"            if(shmctl({fixture}, IPC_RMID, NULL) != 0)",
                    "            {",
                    '                fprintf(stderr, "native cleanup failed: '
                    f'{function_name}: shmctl(IPC_RMID): %s\\n", '
                    "strerror(errno));",
                    "                native_passed = false;",
                    "            }",
                ]
            )
        if function_usr == "c:@F@p101_shmdt" and index == 2:
            return [
                f"            int {fixture}_id = "
                "shmget(IPC_PRIVATE, 1U, IPC_CREAT | 0600);",
                f"            void *{fixture};",
                f"            if({fixture}_id < 0)",
                "            {",
                "                _Exit(77);",
                "            }",
                f"            {fixture} = shmat({fixture}_id, NULL, 0);",
                f"            if({fixture} == (void *)-1)",
                "            {",
                f"                if(shmctl({fixture}_id, IPC_RMID, "
                "NULL) != 0)",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_shmdt: shmctl(IPC_RMID): %s\\n", strerror(errno));',
                "                    native_child_status = EXIT_FAILURE;",
                "                    goto native_child_done_;",
                "                }",
                "                _Exit(77);",
                "            }",
            ], fixture, [], [
                "            if(native_result != 0)",
                "            {",
                f"                if(shmdt({fixture}) != 0)",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_shmdt: shmdt: %s\\n", strerror(errno));',
                "                    native_passed = false;",
                "                }",
                "            }",
                f"            if(shmctl({fixture}_id, IPC_RMID, NULL) != 0)",
                "            {",
                '                fprintf(stderr, "native cleanup failed: '
                'p101_shmdt: shmctl(IPC_RMID): %s\\n", strerror(errno));',
                "                native_passed = false;",
                "            }",
            ]
        if function_usr == "c:@F@p101_shmat" and index == 3:
            return [], "NULL", [], []
        if function_usr == "c:@F@p101_shmat" and index == 4:
            return [], "0", [], []
        if function_usr == "c:@F@p101_shmctl":
            if index == 3:
                return [], "IPC_RMID", [], []
            if index == 4:
                return [], "NULL", [], []

    if function_usr in {
        "c:@F@p101_shm_open",
        "c:@F@p101_shm_unlink",
    }:
        if index == 2:
            declarations = [
                f"            char {fixture}[96];",
                f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}, "
                '"/p101-wrapper-shm-%ld");',
                "            errno = 0;",
                f"            if(shm_unlink({fixture}) != 0 && "
                "errno != ENOENT)",
                "            {",
                f'                fprintf(stderr, "native setup failed: '
                f'{function_name}: shm_unlink: %s\\n", strerror(errno));',
                "                _Exit(77);",
                "            }",
            ]
            setup = []
            if function_usr == "c:@F@p101_shm_unlink":
                declarations.extend(
                    [
                        f"            int {fixture}_fd = "
                        f"shm_open({fixture}, O_CREAT | O_EXCL | O_RDWR, "
                        "0600);",
                        f"            if({fixture}_fd < 0)",
                        "            {",
                        "                _Exit(77);",
                        "            }",
                        f"            if(close({fixture}_fd) != 0)",
                        "            {",
                        f"                if(shm_unlink({fixture}) != 0)",
                        "                {",
                        '                    fprintf(stderr, "native cleanup '
                        f'failed: {function_name}: shm_unlink: %s\\n", '
                        "strerror(errno));",
                        "                }",
                        "                native_child_status = EXIT_FAILURE;",
                        "                goto native_child_done_;",
                        "            }",
                    ]
                )
            cleanup = (
                [
                    "            if(native_result >= 0)",
                    "            {",
                    "                if(close(native_result) != 0)",
                    "                {",
                    '                    fprintf(stderr, "native cleanup failed: '
                    'p101_shm_open: close: %s\\n", strerror(errno));',
                    "                    native_passed = false;",
                    "                }",
                    "            }",
                    f"            if(shm_unlink({fixture}) != 0)",
                    "            {",
                    '                fprintf(stderr, "native cleanup failed: '
                    'p101_shm_open: shm_unlink: %s\\n", strerror(errno));',
                    "                native_passed = false;",
                    "            }",
                ]
                if function_usr == "c:@F@p101_shm_open"
                else [
                    "            if(native_result != 0)",
                    "            {",
                    f"                P101_NATIVE_CLEANUP_ERRNO("
                    f"shm_unlink({fixture}));",
                    "            }",
                ]
            )
            return declarations, fixture, setup, cleanup
        if function_usr == "c:@F@p101_shm_open" and index == 3:
            return [], "O_CREAT | O_EXCL | O_RDWR", [], []
        if function_usr == "c:@F@p101_shm_open" and index == 4:
            return [], "0600", [], []

    if function_usr == "c:@F@p101_pclose" and "FILE" in qualified:
        return [
            f"            FILE *{fixture} = popen(\":\", \"r\");",
            f"            if({fixture} == NULL)",
            "            {",
            "                _Exit(77);",
            "            }",
        ], fixture, [], []

    if function_usr == "c:@F@p101_popen":
        if index == 2:
            return [], '":"', [], [
                "            if(native_result != NULL)",
                "            {",
                "                if(pclose(native_result) != 0)",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_popen: pclose\\n");',
                "                    native_passed = false;",
                "                }",
                "            }",
            ]
        if index == 3:
            return [], '"r"', [], []

    if function_usr == "c:@F@p101_putenv" and index == 2:
        return [
            f"            char {fixture}[] = "
            '"P101_WRAPPER_SMOKE=1";'
        ], fixture, [], [
            '            P101_NATIVE_CLEANUP_ERRNO('
            'unsetenv("P101_WRAPPER_SMOKE"));'
        ]

    if function_usr == "c:@F@p101_nice" and index == 2:
        return [], "1", [], []

    if function_usr == "c:@F@p101_setpriority":
        if index == 2:
            return [], "PRIO_PROCESS", [], []
        if index == 3:
            return [], "0", [], []
        if index == 4:
            return [
                f"            int {fixture};",
                "            errno = 0;",
                f"            {fixture} = getpriority(PRIO_PROCESS, 0);",
                f"            if({fixture} == -1 && errno != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
                f"            if({fixture} < 19)",
                "            {",
                f"                {fixture}++;",
                "            }",
            ], fixture, [], []

    if function_usr == "c:@F@p101_sigaction":
        if index == 2:
            return [], "SIGUSR1", [], []
        if index == 3:
            return [
                f"            struct sigaction {fixture} = {{0}};",
                f"            {fixture}.sa_handler = SIG_IGN;",
                f"            if(sigemptyset(&{fixture}.sa_mask) != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ], f"&{fixture}", [], []
        if index == 4:
            return [], "NULL", [], []

    if function_usr == "c:@F@p101_sigaltstack":
        if index == 2:
            return [], "NULL", [], []
        if index == 3:
            return [
                f"            stack_t {fixture} = {{0}};"
            ], f"&{fixture}", [], []

    if function_usr == "c:@F@p101_sigprocmask" and index == 2:
        return [], "SIG_BLOCK", [], []

    if function_usr == "c:@F@p101_wait" and index == 2:
        return [
            f"            int {fixture} = 0;",
            f"            pid_t {fixture}_child = fork();",
            f"            if({fixture}_child < 0)",
            "            {",
            "                _Exit(77);",
            "            }",
            f"            if({fixture}_child == 0)",
            "            {",
            "                _Exit(EXIT_SUCCESS);",
            "            }",
        ], f"&{fixture}", [], [
            f"            if(native_result != {fixture}_child)",
            "            {",
            f"                if(native_waitpid_nointr("
            f"{fixture}_child, NULL) "
            f"!= {fixture}_child)",
            "                {",
            '                    fprintf(stderr, "native cleanup failed: '
            'p101_wait: waitpid\\n");',
            "                    native_passed = false;",
            "                }",
            "            }",
        ]

    if function_usr == "c:@F@p101_waitpid":
        if index == 2:
            return [
                f"            pid_t {fixture} = fork();",
                f"            if({fixture} < 0)",
                "            {",
                "                _Exit(77);",
                "            }",
                f"            if({fixture} == 0)",
                "            {",
                "                _Exit(EXIT_SUCCESS);",
                "            }",
            ], fixture, [], [
                f"            if(native_result != {fixture})",
                "            {",
                f"                if(native_waitpid_nointr({fixture}, NULL) "
                f"!= {fixture})",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_waitpid: waitpid\\n");',
                "                    native_passed = false;",
                "                }",
                "            }",
            ]
        if index == 3:
            return [
                f"            int {fixture} = 0;"
            ], f"&{fixture}", [], []
        if index == 4:
            return [], "0", [], []

    if function_usr == "c:@F@p101_waitid":
        if index == 2:
            return [], "P_PID", [], []
        if index == 3:
            return [
                f"            pid_t {fixture} = fork();",
                f"            if({fixture} < 0)",
                "            {",
                "                _Exit(77);",
                "            }",
                f"            if({fixture} == 0)",
                "            {",
                "                _Exit(EXIT_SUCCESS);",
                "            }",
            ], f"(id_t){fixture}", [], [
                "            if(native_result != 0)",
                "            {",
                f"                if(native_waitpid_nointr({fixture}, NULL) "
                f"!= {fixture})",
                "                {",
                '                    fprintf(stderr, "native cleanup failed: '
                'p101_waitid: waitpid\\n");',
                "                    native_passed = false;",
                "                }",
                "            }",
            ]
        if index == 4:
            return [
                f"            siginfo_t {fixture} = {{0}};"
            ], f"&{fixture}", [], []
        if index == 5:
            return [], "WEXITED", [], []

    if function_usr in {
        "c:@F@p101_pthread_condattr_setpshared",
        "c:@F@p101_pthread_mutexattr_setpshared",
        "c:@F@p101_pthread_rwlockattr_setpshared",
    } and index == 3:
        return [], "PTHREAD_PROCESS_PRIVATE", [], []

    if function_usr in {
        "c:@F@p101_sem_open",
        "c:@F@p101_sem_unlink",
    }:
        if index == 2:
            declarations = [
                f"            char {fixture}[96];",
                f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}, "
                '"/p101-wrapper-sem-open-%ld");',
                "            errno = 0;",
                f"            if(sem_unlink({fixture}) != 0 && "
                "errno != ENOENT)",
                "            {",
                f'                fprintf(stderr, "native setup failed: '
                f'{function_name}: sem_unlink: %s\\n", strerror(errno));',
                "                _Exit(77);",
                "            }",
            ]
            if function_usr == "c:@F@p101_sem_unlink":
                declarations.extend(
                    [
                        f"            sem_t *{fixture}_sem = "
                        f"sem_open({fixture}, O_CREAT | O_EXCL, 0600, 0U);",
                        f"            if({fixture}_sem == SEM_FAILED)",
                        "            {",
                        "                _Exit(77);",
                        "            }",
                        f"            if(sem_close({fixture}_sem) != 0)",
                        "            {",
                        f"                if(sem_unlink({fixture}) != 0)",
                        "                {",
                        '                    fprintf(stderr, "native cleanup '
                        f'failed: {function_name}: sem_unlink: %s\\n", '
                        "strerror(errno));",
                        "                }",
                        "                native_child_status = EXIT_FAILURE;",
                        "                goto native_child_done_;",
                        "            }",
                    ]
                )
            else:
                declarations.extend(
                    [
                        f"            sem_t *{fixture}_seed = "
                        f"sem_open({fixture}, O_CREAT | O_EXCL, 0600, 0U);",
                        f"            if({fixture}_seed == SEM_FAILED)",
                        "            {",
                        "                _Exit(77);",
                        "            }",
                        f"            if(sem_close({fixture}_seed) != 0)",
                        "            {",
                        f"                if(sem_unlink({fixture}) != 0)",
                        "                {",
                        '                    fprintf(stderr, "native cleanup '
                        f'failed: {function_name}: sem_unlink: %s\\n", '
                        "strerror(errno));",
                        "                }",
                        "                native_child_status = EXIT_FAILURE;",
                        "                goto native_child_done_;",
                        "            }",
                    ]
                )
            cleanup = (
                [
                    "            if(native_result != SEM_FAILED)",
                    "            {",
                    "                P101_NATIVE_CLEANUP_ERRNO("
                    "sem_close(native_result));",
                    "            }",
                    f"            P101_NATIVE_CLEANUP_ERRNO("
                    f"sem_unlink({fixture}));",
                ]
                if function_usr == "c:@F@p101_sem_open"
                else [
                    "            if(native_result != 0)",
                    "            {",
                    f"                P101_NATIVE_CLEANUP_ERRNO("
                    f"sem_unlink({fixture}));",
                    "            }",
                ]
            )
            return declarations, fixture, [], cleanup
        if function_usr == "c:@F@p101_sem_open" and index == 3:
            return [], "0", [], []

    if "posix_spawn_file_actions_t" in qualified and "*" in qualified:
        declarations = [
            f"            posix_spawn_file_actions_t {fixture};"
        ]
        is_initializer = (
            function_usr == "c:@F@p101_posix_spawn_file_actions_init"
        )
        is_destructor = (
            function_usr == "c:@F@p101_posix_spawn_file_actions_destroy"
        )
        setup = []
        if not is_initializer:
            setup = [
                f"            if(posix_spawn_file_actions_init(&{fixture}) "
                "!= 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ]
        cleanup = []
        if not is_destructor:
            if is_initializer:
                cleanup = [
                    "            if(native_result == 0)",
                    "            {",
                    f"                P101_NATIVE_CLEANUP_STATUS("
                    f"posix_spawn_file_actions_destroy(&{fixture}));",
                    "            }",
                ]
            else:
                cleanup = [
                    f"            P101_NATIVE_CLEANUP_STATUS("
                    f"posix_spawn_file_actions_destroy(&{fixture}));"
                ]
        return declarations, f"&{fixture}", setup, cleanup

    if "posix_spawnattr_t" in qualified and "*" in qualified:
        declarations = [f"            posix_spawnattr_t {fixture};"]
        is_initializer = (
            function_usr == "c:@F@p101_posix_spawnattr_init"
        )
        is_destructor = (
            function_usr == "c:@F@p101_posix_spawnattr_destroy"
        )
        setup = []
        if not is_initializer:
            setup = [
                f"            if(posix_spawnattr_init(&{fixture}) != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ]
        cleanup = []
        if not is_destructor:
            if is_initializer:
                cleanup = [
                    "            if(native_result == 0)",
                    "            {",
                    f"                P101_NATIVE_CLEANUP_STATUS("
                    f"posix_spawnattr_destroy(&{fixture}));",
                    "            }",
                ]
            else:
                cleanup = [
                    f"            P101_NATIVE_CLEANUP_STATUS("
                    f"posix_spawnattr_destroy(&{fixture}));"
                ]
        return declarations, f"&{fixture}", setup, cleanup

    if re.search(r"\bsem_t\s*\*", qualified):
        initial_value = (
            "1U"
            if function_usr
            in {"c:@F@p101_sem_trywait", "c:@F@p101_sem_wait"}
            else "0U"
        )
        declarations = [
            f"            char {fixture}_name[96];",
            f"            sem_t *{fixture};",
            f"            P101_NATIVE_FORMAT_PID_PATH_OR_SKIP({fixture}_name, "
            '"/p101-wrapper-sem-%ld");',
            f"            {fixture} = sem_open({fixture}_name, "
            f"O_CREAT | O_EXCL, 0600, {initial_value});",
            f"            if({fixture} == SEM_FAILED)",
            "            {",
            "                _Exit(77);",
            "            }",
        ]
        cleanup = []
        if function_usr != "c:@F@p101_sem_close":
            cleanup.append(
                f"            P101_NATIVE_CLEANUP_ERRNO(sem_close({fixture}));"
            )
        cleanup.append(
            f"            P101_NATIVE_CLEANUP_ERRNO("
            f"sem_unlink({fixture}_name));"
        )
        return declarations, fixture, [], cleanup

    if qualified == "nl_catd":
        return [], "(nl_catd)0", [], []

    if function_usr == "c:@F@p101_iconv_open":
        if index in {2, 3}:
            cleanup = (
                [
                    "            if(native_result != (iconv_t)-1)",
                    "            {",
                    "                if(p101_iconv_close("
                    "native_env, native_err, native_result) != 0)",
                    "                {",
                    '                    fprintf(stderr, "native cleanup failed: '
                    'p101_iconv_open: p101_iconv_close: %s\\n", '
                    "p101_error_get_message(native_err));",
                    "                    native_passed = false;",
                    "                    p101_error_reset(native_err);",
                    "                }",
                    "            }",
                ]
                if index == 2
                else []
            )
            return [], '"UTF-8"', [], cleanup

    if qualified == "ENTRY":
        return [
            f'            ENTRY {fixture} = '
            '{(char *)"p101", NULL};'
        ], fixture, [
            "            if(hcreate(8U) == 0)",
            "            {",
            "                _Exit(77);",
            "            }",
        ], [
            "            hdestroy();"
        ]

    if qualified == "pthread_t":
        if function_usr in {
            "c:@F@p101_pthread_cancel",
            "c:@F@p101_pthread_detach",
            "c:@F@p101_pthread_join",
        }:
            return [
                f"            pthread_t {fixture};",
                f"            if(pthread_create(&{fixture}, NULL, "
                "native_thread_callback, NULL) != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ], fixture, [], (
                [
                    f"            P101_NATIVE_CLEANUP_STATUS("
                    f"pthread_join({fixture}, NULL));"
                ]
                if function_usr == "c:@F@p101_pthread_cancel"
                else []
            )
        return [], "pthread_self()", [], []

    if (
        function_usr == "c:@F@p101_sigwait"
        and index == 2
        and "sigset_t" in qualified
        and "*" in qualified
    ):
        return [
            f"            sigset_t {fixture};",
            f"            sigset_t {fixture}_previous;",
            f"            sigset_t {fixture}_pending;",
            f"            if(sigemptyset(&{fixture}) != 0)",
            "            {",
            "                _Exit(77);",
            "            }",
            f"            if(sigaddset(&{fixture}, SIGUSR1) != 0)",
            "            {",
            "                _Exit(77);",
            "            }",
            f"            if(sigprocmask(SIG_BLOCK, &{fixture}, "
            f"&{fixture}_previous) != 0)",
            "            {",
            "                _Exit(77);",
            "            }",
            "            if(raise(SIGUSR1) != 0)",
            "            {",
            "                _Exit(77);",
            "            }",
        ], f"&{fixture}", [], [
            f"            if(sigpending(&{fixture}_pending) != 0)",
            "            {",
            '                fprintf(stderr, "native cleanup failed: '
            'p101_sigwait: sigpending: %s\\n", strerror(errno));',
            "                native_passed = false;",
            "            }",
            f"            else if(sigismember(&{fixture}_pending, SIGUSR1) == 1)",
            "            {",
            f"                int {fixture}_drained_signal = 0;",
            f"                int {fixture}_drain_status = "
            f"sigwait(&{fixture}, &{fixture}_drained_signal);",
            f"                if({fixture}_drain_status != 0)",
            "                {",
            '                    fprintf(stderr, "native cleanup failed: '
            'p101_sigwait: drain pending signal: status %d\\n", '
            f"{fixture}_drain_status);",
            "                    native_passed = false;",
            "                }",
            "            }",
            f"            P101_NATIVE_CLEANUP_ERRNO("
            f"sigprocmask(SIG_SETMASK, &{fixture}_previous, NULL));"
        ]

    if "sigset_t" in qualified and "*" in qualified:
        return [
            f"            sigset_t {fixture};",
            f"            if(sigemptyset(&{fixture}) != 0)",
            "            {",
            "                _Exit(77);",
            "            }",
        ], f"&{fixture}", [], []

    if qualified == "iconv_t":
        declarations = [
            f"            iconv_t {fixture};",
            f"            {fixture} = p101_iconv_open("
            'native_env, native_err, "UTF-8", "UTF-8");',
            f"            if({fixture} == (iconv_t)-1)",
            "            {",
            "                _Exit(77);",
            "            }",
        ]
        cleanup = (
            []
            if function_usr == "c:@F@p101_iconv_close"
            else [
                f"            if(p101_iconv_close(native_env, native_err, "
                f"{fixture}) != 0)",
                "            {",
                '                fprintf(stderr, "native cleanup failed: '
                f'{function_name}: p101_iconv_close: %s\\n", '
                "p101_error_get_message(native_err));",
                "                native_passed = false;",
                "                p101_error_reset(native_err);",
                "            }",
            ]
        )
        return declarations, fixture, [], cleanup

    return None


def portable_case_value(case: dict[str, Any]) -> str:
    kind = case["input_kind"]
    if kind == "negative-one":
        return "-1"
    if kind == "null":
        return "NULL"
    if kind == "catalog-zero":
        return "(nl_catd)0"
    if kind == "catalog-failure":
        return "(nl_catd)-1"
    if kind == "text-without-leading-slash":
        return '"p101"'
    if kind == "text-root-only":
        return '"/"'
    if kind == "text-with-extra-slash":
        return '"/p101/invalid"'
    raise RuntimeError(f"unsupported portable input kind {kind!r}")


def portable_companion_value(companion: dict[str, Any]) -> str:
    if companion["value_kind"] == "text":
        return json.dumps(companion["value"])
    raise RuntimeError(
        "unsupported portable companion value kind "
        f"{companion['value_kind']!r}"
    )


def portable_result_check(
    case: dict[str, Any],
    argument_values: list[str],
) -> str:
    kind = case["result_kind"]
    if kind == "negative-one":
        expected = "-1"
    elif kind == "null":
        expected = "NULL"
    elif kind == "catalog-failure":
        expected = "(nl_catd)-1"
    elif kind == "pointer-failure":
        expected = "(void *)-1"
    elif kind == "argument":
        expected = argument_values[case["result_parameter_index"]]
    else:
        raise RuntimeError(f"unsupported portable result kind {kind!r}")
    return f"        EXPECT(portable_result == {expected});"


def portable_rejection_tests(
    name: str,
    declaration: dict[str, Any],
    base_argument_values: list[str],
    rules: list[dict[str, Any]],
) -> str:
    tests: list[str] = []
    for rule in rules:
        for case in rule["cases"]:
            argument_values = list(base_argument_values)
            companion_declarations: list[str] = []
            argument_values[rule["parameter_index"]] = portable_case_value(
                case
            )
            for companion in case.get("companion_arguments", []):
                companion_name = (
                    "portable_argument_"
                    f"{companion['parameter_index']}"
                )
                companion_declarations.append(
                    f"        const char *{companion_name} = "
                    f"{portable_companion_value(companion)};"
                )
                argument_values[companion["parameter_index"]] = companion_name
            arguments = ", ".join(argument_values)
            declarations = (
                "\n".join(companion_declarations) + "\n"
                if companion_declarations
                else ""
            )
            result = (
                f"        {result_declaration(declaration, 'portable_result')} "
                f"= {name}({arguments});\n"
                "        (void)portable_result;"
            )
            result_check = portable_result_check(case, argument_values)
            tests.append(
                f"""    {{
        int failures_before = failures;

        EXPECT(p101_error_has_no_error(err));
        fault_resource_events = 0U;
        errno                 = P101_TEST_ERRNO_SENTINEL;
{declarations}\
{result}
        EXPECT(p101_error_is_errno(err, {rule["error_code"]}));
        EXPECT(errno == P101_TEST_ERRNO_SENTINEL);
{result_check}
        EXPECT(fault_resource_events == 0U);
        if(failures != failures_before)
        {{
            fprintf(stderr,
                    "portable rejection failed: {name}: {case['input_kind']}\\n");
        }}
        p101_error_reset(err);
    }}"""
            )
    if not tests:
        return ""
    return "\n".join(tests) + "\n"


def fault_test(
    name: str,
    function_usr: str,
    declaration: dict[str, Any],
    error_names: dict[str, list[str]],
    failure: dict[str, str],
    native_outcome: dict[str, str] | None = None,
    portable_rules: list[dict[str, Any]] | None = None,
) -> str:
    parameters = [
        child
        for child in declaration.get("inner", [])
        if child.get("kind") == "ParmVarDecl"
    ]
    argument_setup = va_list_setup(declaration)
    fixture_declarations: list[str] = []
    argument_values: list[str] = []
    native_argument_values: list[str] = []
    native_setup: list[str] = []
    native_fixture_declarations: list[str] = []
    native_cleanup: list[str] = []
    needs_native_stream = False
    needs_native_fenv = False
    needs_native_fexcept = False
    needs_native_fpos = False
    fixture_assertions: list[str] = []
    for index, parameter in enumerate(parameters):
        expression = argument_expression(parameter, declaration)
        if expression in {"env", "err"}:
            argument_values.append(expression)
            native_argument_values.append(
                "native_env" if expression == "env" else "native_err"
            )
            continue
        if expression == "arguments":
            argument_values.append(expression)
            native_argument_values.append(expression)
            continue
        declarations, expression, assertions = writable_fixture(
            parameter,
            index,
            declaration,
        )
        fixture_declarations.extend(declarations)
        argument_values.append(expression)
        fixture_assertions.extend(assertions)
        qualified = parameter.get("type", {}).get("qualType", "")
        pointer_depth = qualified.count("*")
        if (
            fixture := native_contract_fixture(
                name,
                function_usr,
                parameter,
                index,
            )
        ) is not None:
            declarations, native_expression, setup, cleanup = fixture
            native_fixture_declarations.extend(declarations)
            native_argument_values.append(native_expression)
            native_setup.extend(setup)
            native_cleanup.extend(cleanup)
        elif qualified == "locale_t":
            native_fixture_declarations.extend(
                [
                    f"            locale_t native_argument_{index};",
                    f"            native_argument_{index} = "
                    "newlocale(LC_ALL_MASK, \"C\", (locale_t)0);",
                    f"            if(native_argument_{index} == (locale_t)0)",
                    "            {",
                    "                _Exit(77);",
                    "            }",
                ]
            )
            native_argument_values.append(f"native_argument_{index}")
            native_cleanup.append(
                f"            freelocale(native_argument_{index});"
            )
        elif "FILE" in qualified and "*" in qualified:
            native_argument_values.append("native_stream")
            needs_native_stream = True
        elif "fenv_t" in qualified and "*" in qualified:
            native_argument_values.append("&native_fenv")
            needs_native_fenv = True
        elif "fexcept_t" in qualified and "*" in qualified:
            native_argument_values.append("&native_fexcept")
            needs_native_fexcept = True
        elif "fpos_t" in qualified and "*" in qualified:
            native_argument_values.append("&native_fpos")
            needs_native_fpos = True
        elif (
            fixture := native_pointer_fixture(
                name,
                function_usr,
                parameter,
                index,
                declaration,
            )
        ) is not None:
            declarations, native_expression, setup, cleanup = fixture
            native_fixture_declarations.extend(declarations)
            native_argument_values.append(native_expression)
            native_setup.extend(setup)
            native_cleanup.extend(cleanup)
        elif (
            pointer_depth == 1
            and re.search(r"\bconst\b", qualified)
            and re.search(r"\bchar\s*\*", qualified)
        ):
            native_argument_values.append('"p101"')
        elif (
            pointer_depth == 1
            and "wchar_t" in qualified
            and re.search(r"\bconst\b", qualified)
        ):
            native_argument_values.append('L"p101"')
        else:
            native_argument_values.append(expression)
            if expression.startswith("argument_"):
                native_setup.append(
                    f"            memset({expression}, 0, "
                    f"sizeof({expression}));"
                )
    if needs_native_stream and function_usr != "c:@F@p101_fclose":
        native_cleanup.append(
            "            P101_NATIVE_CLEANUP_ERRNO(fclose(native_stream));"
        )
    if function_usr in {"c:@F@p101_pause", "c:@F@p101_sigsuspend"}:
        native_setup.extend(
            [
                "            if(signal(SIGALRM, "
                "native_signal_callback) == SIG_ERR)",
                "            {",
                "                _Exit(77);",
                "            }",
                "            (void)alarm(1U);",
            ]
        )
    if function_usr in {
        "c:@F@p101_pthread_cond_timedwait",
        "c:@F@p101_pthread_cond_wait",
    }:
        native_setup.extend(
            [
                "            if(pthread_mutex_lock("
                "&native_argument_3) != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ]
        )
        native_cleanup.insert(
            0,
            "            P101_NATIVE_CLEANUP_STATUS("
            "pthread_mutex_unlock(&native_argument_3));",
        )
    if function_usr == "c:@F@p101_pthread_cond_wait":
        native_fixture_declarations.extend(
            [
                "            pthread_t native_condition_thread;",
                "            struct native_condition_signal_context "
                "native_condition_context = {",
                "                &native_argument_2,",
                "                &native_argument_3,",
                "                0,",
                "            };",
            ]
        )
        native_setup.extend(
            [
                "            if(pthread_create(&native_condition_thread, "
                "NULL, native_condition_signal_thread, "
                "&native_condition_context) != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ]
        )
        native_cleanup[0:0] = [
            "            P101_NATIVE_CLEANUP_STATUS("
            "pthread_join(native_condition_thread, NULL));",
            "            if(native_condition_context.status != 0)",
            "            {",
            '                fprintf(stderr, "native helper failed: '
            'p101_pthread_cond_wait: status %d\\n", '
            "native_condition_context.status);",
            "                native_passed = false;",
            "            }",
        ]
    if function_usr == "c:@F@p101_pthread_mutex_unlock":
        native_setup.extend(
            [
                "            if(pthread_mutex_lock("
                "&native_argument_2) != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ]
        )
    if function_usr in {
        "c:@F@p101_pthread_mutex_lock",
        "c:@F@p101_pthread_mutex_trylock",
    }:
        native_cleanup.insert(
            0,
            "            P101_NATIVE_CLEANUP_STATUS("
            "pthread_mutex_unlock(&native_argument_2));",
        )
    if function_usr == "c:@F@p101_pthread_rwlock_unlock":
        native_setup.extend(
            [
                "            if(pthread_rwlock_rdlock("
                "&native_argument_2) != 0)",
                "            {",
                "                _Exit(77);",
                "            }",
            ]
        )
    if function_usr in {
        "c:@F@p101_pthread_rwlock_rdlock",
        "c:@F@p101_pthread_rwlock_tryrdlock",
        "c:@F@p101_pthread_rwlock_trywrlock",
        "c:@F@p101_pthread_rwlock_wrlock",
    }:
        native_cleanup.insert(
            0,
            "            P101_NATIVE_CLEANUP_STATUS("
            "pthread_rwlock_unlock(&native_argument_2));",
        )
    if function_usr == "c:@F@p101_pthread_create":
        native_cleanup.insert(
            0,
            "            if(native_result == 0)\n"
            "            {\n"
            "                P101_NATIVE_CLEANUP_STATUS("
            "pthread_join(native_argument_2, NULL));\n"
            "            }",
        )
    if function_usr == "c:@F@p101_pthread_key_create":
        native_cleanup.insert(
            0,
            "            if(native_result == 0)\n"
            "            {\n"
            "                P101_NATIVE_CLEANUP_STATUS("
            "pthread_key_delete(native_argument_2));\n"
            "            }",
        )
    if function_usr == "c:@F@p101_if_nameindex":
        native_cleanup.extend(
            [
                "            if(native_result != NULL)",
                "            {",
                "                if_freenameindex(native_result);",
                "            }",
            ]
        )
    arguments = ", ".join(argument_values)
    native_arguments = ", ".join(native_argument_values)
    if failure["kind"] == "void":
        invocation = f"    {name}({arguments});"
    else:
        invocation = (
            f"    {result_declaration(declaration, 'result')} = "
            f"{name}({arguments});\n"
            "    (void)result;"
        )
    if failure["kind"] == "void":
        native_call = f"            {name}({native_arguments});"
    else:
        native_call = (
            f"            {result_declaration(declaration, 'native_result')} = "
            f"{name}({native_arguments});\n"
            "            (void)native_result;"
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
    native_declarations = ""
    if needs_native_stream:
        native_declarations += (
            "            FILE *native_stream = tmpfile();\n"
            "            if(native_stream == NULL)\n"
            "            {\n"
            "                _Exit(77);\n"
            "            }\n"
        )
    if needs_native_fenv:
        native_declarations += (
            "            fenv_t native_fenv;\n"
            "            if(fegetenv(&native_fenv) != 0)\n"
            "            {\n"
            "                _Exit(77);\n"
            "            }\n"
        )
    if needs_native_fexcept:
        native_declarations += (
            "            fexcept_t native_fexcept;\n"
            "            if(fegetexceptflag(&native_fexcept, FE_ALL_EXCEPT) != 0)\n"
            "            {\n"
            "                _Exit(77);\n"
            "            }\n"
        )
    if needs_native_fpos:
        native_declarations += (
            "            fpos_t native_fpos;\n"
            "            if(fgetpos(native_stream, &native_fpos) != 0)\n"
            "            {\n"
            "                _Exit(77);\n"
            "            }\n"
        )
    native_setup_text = (
        "\n".join(native_setup) + "\n" if native_setup else ""
    )
    native_fixture_text = (
        "\n".join(native_fixture_declarations) + "\n"
        if native_fixture_declarations
        else ""
    )
    native_cleanup_text = (
        "\n".join(native_cleanup) + "\n" if native_cleanup else ""
    )
    portable_tests = portable_rejection_tests(
        name,
        declaration,
        argument_values,
        portable_rules or [],
    )
    unchecked_cleanup = [
        line
        for line in native_cleanup
        if re.search(
            r"\(void\)(?:close|closedir|fclose|msgctl|pclose|"
            r"pthread_[A-Za-z0-9_]+|sem_close|sem_unlink|"
            r"semctl|shm_unlink|shmctl|shmdt|sigprocmask|"
            r"unlink|waitpid)\s*\(",
            line,
        )
    ]
    if unchecked_cleanup:
        raise RuntimeError(
            f"{name}: native cleanup ignores a reportable result: "
            + ", ".join(line.strip() for line in unchecked_cleanup)
        )
    native_outcome = native_outcome or {"outcome": "success"}
    if native_outcome["outcome"] == "success":
        native_assertion = (
            "            if(p101_error_has_error(native_err))\n"
            "            {\n"
            '                fprintf(stderr, "native smoke failed: '
            f'{name}: %s\\n", p101_error_get_message(native_err));\n'
            "                native_passed = false;\n"
            "            }\n"
        )
    elif native_outcome["outcome"] == "error":
        native_error_domain = native_outcome["error_domain"]
        if native_error_domain == "errno":
            native_error_assertion = (
                f"p101_error_is_errno(native_err, "
                f"{native_outcome['error_code']})"
            )
        else:
            native_error_type = {
                "check": "P101_ERROR_CHECK",
                "system": "P101_ERROR_SYSTEM",
                "user": "P101_ERROR_USER",
            }[native_error_domain]
            native_error_assertion = (
                f"p101_error_is_error(native_err, {native_error_type}, "
                f"{native_outcome['error_code']})"
            )
        native_assertion = (
            f"            if(!{native_error_assertion})\n"
            "            {\n"
                '                fprintf(stderr, "native smoke did not produce '
            f'the declared failure: {name}: %s\\n", '
            "p101_error_get_message(native_err));\n"
            "                native_passed = false;\n"
            "            }\n"
        )
        result_kind = native_outcome.get("result_kind")
        if result_kind == "equals":
            native_assertion += (
                "            if(native_result != "
                f"{native_outcome['result_expression']})\n"
                "            {\n"
                '                fprintf(stderr, "native smoke returned an '
                f'undeclared result: {name}\\n");\n'
                "                native_passed = false;\n"
                "            }\n"
            )
        elif result_kind == "text":
            native_assertion += (
                "            if(native_result == NULL ||\n"
                "               strcmp(native_result, "
                f"{json.dumps(native_outcome['result_text'])}) != 0)\n"
                "            {\n"
                '                fprintf(stderr, "native smoke returned an '
                f'undeclared result: {name}\\n");\n'
                "                native_passed = false;\n"
                "            }\n"
            )
        elif result_kind is not None:
            raise RuntimeError(
                f"{name}: unsupported native result assertion {result_kind!r}"
            )
        native_assertion += "            p101_error_reset(native_err);\n"
    else:
        allowed = native_outcome["allowed_error_codes"]
        allowed_assertion = " &&\n                ".join(
            f"!p101_error_is_errno(native_err, {code})"
            for code in allowed
        )
        native_assertion = (
            "            if(p101_error_has_error(native_err) &&\n"
            f"               {allowed_assertion})\n"
            "            {\n"
            '                fprintf(stderr, "native smoke produced an '
            f'undeclared conditional failure: {name}\\n");\n'
            "                native_passed = false;\n"
            "            }\n"
            "            if(p101_error_has_error(native_err))\n"
            "            {\n"
            f"                if(native_result != "
            f"{native_outcome['error_result_expression']})\n"
            "                {\n"
            '                    fprintf(stderr, "native smoke returned an '
            f'undeclared conditional result: {name}\\n");\n'
            "                    native_passed = false;\n"
            "                }\n"
            "                p101_error_reset(native_err);\n"
            "            }\n"
        )
    source = f"""/* P101_TEST_CASE({name}) */
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
{portable_tests}\
    {{
        int   native_status = 0;
        pid_t native_pid    = fork();

        EXPECT(native_pid >= 0);
        if(native_pid == 0)
        {{
            bool               native_passed = true;
            struct p101_error *native_err = NULL;
            struct p101_env   *native_env = NULL;

            native_child_process = true;
            failures            = 0;
            (void)alarm(2U);
            if(unsetenv("P101_CALL_LOG") != 0 ||
               unsetenv("P101_RESOURCE_LOG") != 0)
            {{
                fprintf(stderr,
                        "native setup failed: cannot clear p101 logging environment\\n");
                native_child_status = 77;
                goto native_child_done_;
            }}
            native_err = p101_error_create(false);
            if(native_err == NULL)
            {{
                native_child_status = 77;
                goto native_child_done_;
            }}
            native_env = p101_env_create(native_err, NULL);
            if(native_env == NULL)
            {{
                native_child_status = 77;
                goto native_child_done_;
            }}
{native_declarations}\
{native_fixture_text}\
{native_setup_text}\
{native_call}
{native_assertion}\
{native_cleanup_text}\
            native_child_status = native_passed ? EXIT_SUCCESS : EXIT_FAILURE;
native_child_done_:
            p101_env_destroy(native_env);
            p101_error_destroy(native_err);
        }}
        if(native_pid > 0)
        {{
            EXPECT(native_waitpid_nointr(native_pid, &native_status) == native_pid);
            if(WIFSIGNALED(native_status))
            {{
                fprintf(stderr,
                        "native smoke terminated by signal: {name}: %d\\n",
                        WTERMSIG(native_status));
            }}
            EXPECT(WIFEXITED(native_status));
            if(WIFEXITED(native_status))
            {{
                if(WEXITSTATUS(native_status) != EXIT_SUCCESS)
                {{
                    fprintf(stderr,
                            "native smoke exited unsuccessfully: {name}: %d\\n",
                            WEXITSTATUS(native_status));
                }}
                EXPECT(WEXITSTATUS(native_status) == EXIT_SUCCESS);
            }}
        }}
        p101_error_reset(err);
    }}
{va_list_teardown(declaration)}\
}}
"""
    rendered = source.replace(
        "_Exit(77);",
        "native_child_status = 77;\ngoto native_child_done_;",
    ).replace(
        "_Exit(EXIT_SUCCESS);",
        "native_child_status = EXIT_SUCCESS;\ngoto native_child_done_;",
    )
    return rendered


def native_callback_helpers(
    declarations: dict[str, dict[str, Any]],
    function_usrs: dict[str, str],
    names: list[str],
) -> str:
    helpers: set[str] = set()
    for name in names:
        for parameter in declarations[name].get("inner", []):
            if parameter.get("kind") != "ParmVarDecl":
                continue
            qualified = normalized_c_type(
                parameter.get("type", {}).get("qualType", "")
            )
            callback = NATIVE_CALLBACKS.get(qualified)
            if callback is not None:
                helpers.add(callback)
    admitted_usrs = {function_usrs[name] for name in names}
    if {"c:@F@p101_pause", "c:@F@p101_sigsuspend"} & admitted_usrs:
        helpers.add("native_signal_callback")
    if "c:@F@p101_pthread_cond_wait" in admitted_usrs:
        helpers.add("native_condition_signal_thread")
    return "\n".join(
        NATIVE_CALLBACK_DEFINITIONS[helper].strip()
        for helper in sorted(helpers)
    )


def fault_source(
    library: str,
    includes: str,
    declarations: dict[str, dict[str, Any]],
    function_usrs: dict[str, str],
    names: list[str],
    platform_faults: dict[str, dict[str, list[str]]],
    failure_contract: dict[str, dict[str, str]],
    native_smoke_contract: dict[str, dict[str, str]],
    portable_input_contract: dict[str, list[dict[str, Any]]],
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
            function_usrs[name],
            declarations[name],
            platform_faults.get(function_usrs[name], []),
            failure_contract[function_usrs[name]],
            native_smoke_contract.get(function_usrs[name]),
            portable_input_contract.get(function_usrs[name]),
        )
        for name in names
    )
    calls = "\n".join(
        "    if(!native_child_process)\n"
        "    {\n"
        f"        test_{name}(env, err"
        f"{fault_test_call_suffix(declarations[name])});\n"
        "    }"
        for name in names
    )
    native_helpers = native_callback_helpers(
        declarations,
        function_usrs,
        names,
    )
    native_includes = "#include <fcntl.h>\n"
    native_unlink_helper = ""
    native_format_helper = ""
    if "P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT" in tests:
        native_unlink_helper = """static bool native_unlink_if_present(const char *path)
{
    bool        result;
    int         unlink_status;
    int         unlink_error;
    const char *message;
    int         written;

    errno         = 0;
    unlink_status = unlink(path);
    unlink_error  = errno;
    if(unlink_status != 0 && unlink_error != ENOENT)
    {
        message = strerror(unlink_error);
        written = fprintf(stderr,
                          "native cleanup failed: unlink(%s): %s\\n",
                          path,
                          message);
        (void)written;
        result = false;
    }
    else
    {
        result = true;
    }
    return result;
}

"""
    if "P101_NATIVE_FORMAT_PID_PATH_OR_SKIP" in tests:
        native_format_helper = """static bool native_format_pid_path(char *buffer,
                                   size_t buffer_size,
                                   const char *format)
{
    bool result;
    int format_length;
    pid_t process_id;

    process_id = getpid();
    format_length = snprintf(buffer,
                             buffer_size,
                             format,
                             (long)process_id);
    result = format_length >= 0 && (size_t)format_length < buffer_size;
    return result;
}

"""
    return f"""#include <errno.h>
{native_includes}
#include <arpa/inet.h>
#include <dirent.h>
#include <fmtmsg.h>
#include <fnmatch.h>
#include <ftw.h>
{includes}
#include <p101_env/env.h>
#include <p101_error/error.h>
#include <limits.h>
#include <math.h>
#include <netinet/in.h>
#include <pthread.h>
#include <search.h>
#include <signal.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <utmpx.h>

static int failures;
static size_t fault_resource_events;
static FILE *outcome_stream;
static bool native_child_process;
static int native_child_status = EXIT_SUCCESS;
{native_helpers}

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

#define P101_NATIVE_CLEANUP_ERRNO(expression)                                    \\
    do                                                                           \\
    {{                                                                            \\
        if((expression) != 0)                                                    \\
        {{                                                                        \\
            fprintf(stderr,                                                      \\
                    "native cleanup failed: %s: %s\\n",                          \\
                    #expression,                                                 \\
                    strerror(errno));                                            \\
            native_passed = false;                                               \\
        }}                                                                        \\
    }} while(0)

#define P101_NATIVE_CLEANUP_STATUS(expression)                                   \\
    do                                                                           \\
    {{                                                                            \\
        int p101_cleanup_status_ = (expression);                                 \\
        if(p101_cleanup_status_ != 0)                                            \\
        {{                                                                        \\
            fprintf(stderr,                                                      \\
                    "native cleanup failed: %s: status %d\\n",                   \\
                    #expression,                                                 \\
                    p101_cleanup_status_);                                       \\
            native_passed = false;                                               \\
        }}                                                                        \\
    }} while(0)

{native_unlink_helper}\
#define P101_NATIVE_CLEANUP_UNLINK_IF_PRESENT(path)                              \\
    do                                                                           \\
    {{                                                                            \\
        bool p101_cleanup_ok_;                                                    \\
                                                                                 \\
        p101_cleanup_ok_ = native_unlink_if_present(path);                        \\
        if(!p101_cleanup_ok_)                                                     \\
        {{                                                                        \\
            native_passed = false;                                               \\
        }}                                                                        \\
    }} while(0)

{native_format_helper}\
#define P101_NATIVE_FORMAT_PID_PATH_OR_SKIP(buffer, format)                       \\
    do                                                                           \\
    {{                                                                            \\
        bool p101_format_ok_;                                                     \\
                                                                                 \\
        p101_format_ok_ = native_format_pid_path((buffer),                        \\
                                                 sizeof(buffer),                  \\
                                                 (format));                       \\
        if(!p101_format_ok_)                                                      \\
        {{                                                                        \\
            fprintf(stderr, "native setup failed: path formatting\\n");          \\
            native_child_status = 77;                                             \\
            goto native_child_done_;                                              \\
        }}                                                                        \\
    }} while(0)

struct fault_state
{{
    int checks;
    int code;
}};

static pid_t native_waitpid_nointr(pid_t pid, int *status)
    P101_ATTR_SEMANTIC_ROLE("p101:test:eintr-safe-wait-adapter")
{{
    pid_t result;

    do
    {{
        result = waitpid(pid, status, 0);
    }} while(result < 0 && errno == EINTR);
    return result;
}}

static void write_outcome(const char *wrapper,
                          const char *domain,
                          const char *symbol,
                          int code,
                          int passed)
{{
    int written;

    if(outcome_stream != NULL)
    {{
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
    struct p101_error *err = NULL;
    struct p101_env   *env = NULL;
    int                status;

    outcome_path = getenv("P101_WRAPPER_OUTCOME_LOG");
    if(outcome_path != NULL && outcome_path[0] != '\\0')
    {{
        outcome_stream = fopen(outcome_path, "a");
        if(outcome_stream == NULL)
        {{
            fprintf(stderr, "FAIL: cannot open wrapper outcome receipt\\n");
            failures++;
        }}
    }}
    if(failures == 0)
    {{
        err = p101_error_create(false);
    }}
    if(err != NULL)
    {{
        env = p101_env_create(err, NULL);
    }}
    if(env == NULL)
    {{
        failures++;
    }}
    else
    {{
        p101_env_set_fd_observer(env, count_fd_event, NULL);
        p101_env_set_alloc_observer(env, count_alloc_event, NULL);
        p101_env_set_resource_observer(env, count_resource_event, NULL);
{calls}
    }}
    p101_env_destroy(env);
    p101_error_destroy(err);
    if(outcome_stream != NULL && fclose(outcome_stream) != 0)
    {{
        fprintf(stderr, "FAIL: cannot close wrapper outcome receipt\\n");
        failures++;
    }}
    if(native_child_process)
    {{
        status = native_child_status;
        if(status == EXIT_SUCCESS && failures != 0)
        {{
            status = EXIT_FAILURE;
        }}
    }}
    else
    {{
        status = failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
    }}
    return status;
}}
"""


def public_header_includes(repo: Path) -> str:
    headers = sorted((repo / "include").rglob("*.h"))
    return "\n".join(
        f"#include <{header.relative_to(repo / 'include')}>"
        for header in headers
    )


def existing_behavior_source(
    repo: Path,
    wrapper_usr: str,
    calls_by_source: dict[Path, set[str]],
) -> tuple[str, str] | None:
    for source in sorted(calls_by_source):
        try:
            relative = source.relative_to(repo)
        except ValueError:
            continue
        if wrapper_usr not in calls_by_source[source]:
            continue
        kind = (
            "behavior"
            if relative.as_posix() == "test/test_behavior.c"
            else "behavior-existing"
        )
        return (kind, relative.as_posix())
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


def validate_generated_source_semantics(
    library: str,
    source: str,
) -> None:
    """Apply generated-fixture policy to resolved calls, never spellings."""
    with tempfile.TemporaryDirectory(prefix="p101-wrapper-generator-") as raw:
        path = Path(raw) / "test_fault_wrappers.c"
        path.write_text(source, encoding="utf-8")
        try:
            facts = acquire(WORKSPACE, (path,))
        except CFactError as error:
            raise RuntimeError(
                f"{library}: cannot validate generated fixture semantics: "
                f"{error}"
            ) from error

    calls = {
        (int(fact.get("start", -1)), int(fact.get("end", -1))): fact
        for fact in facts
        if fact.get("kind") == "CALL"
    }
    discarded = {
        (int(fact.get("start", -1)), int(fact.get("end", -1)))
        for fact in facts
        if fact.get("kind") == "NOTE"
        and fact.get("value") == "CALL_RESULT_DISCARDED"
    }
    eintr_safe_wait_callers = {
        str(fact.get("caller_usr", ""))
        for fact in facts
        if fact.get("kind") == "NOTE"
        and fact.get("value") == EINTR_SAFE_WAIT_ROLE
    }
    for extent, call in calls.items():
        usr = str(call.get("usr", ""))
        if usr in UNSAFE_NATIVE_CALL_USRS:
            raise RuntimeError(
                f"{library}: generated fixture calls unsafe API identity {usr}"
            )
        if (
            usr in DIRECT_WAIT_USRS
            and str(call.get("caller_usr", "")) not in eintr_safe_wait_callers
        ):
            raise RuntimeError(
                f"{library}: generated fixture calls waitpid directly instead "
                "of the EINTR-safe adapter"
            )
        if usr in PROCESS_TERMINATION_USRS:
            raise RuntimeError(
                f"{library}: generated fixture bypasses its single return "
                f"with termination identity {usr}"
            )
        if extent in discarded and usr in STATUS_BEARING_CLEANUP_USRS:
            raise RuntimeError(
                f"{library}: generated fixture discards the result of "
                f"status-bearing operation {usr}"
            )


def write_outputs(clang: str, clang_format: str, check: bool) -> int:
    libraries = active_libraries()
    include_dirs = sorted(
        path
        for path in LIBRARIES.glob("lib_*/include")
        if path.is_dir()
    )
    semantic_include_dirs = [
        *include_dirs,
        *clang_system_include_dirs(clang),
    ]
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
    if outcome_contract.get("schema") != "p101-wrapper-outcome-contract-v2":
        raise RuntimeError("unsupported wrapper outcome contract")
    outcome_apis = outcome_contract.get("apis", {})
    outcome_apis_by_usr = {
        record.get("function_usr"): record
        for record in outcome_apis.values()
        if isinstance(record, dict) and record.get("function_usr")
    }
    if len(outcome_apis_by_usr) != len(outcome_apis):
        raise RuntimeError(
            "wrapper outcome contract has missing or duplicate identities"
        )
    fault_wrappers = fault_contract["wrappers"]
    fault_wrappers_by_usr = {
        binding.get("function_usr"): binding
        for binding in fault_wrappers.values()
        if isinstance(binding, dict) and binding.get("function_usr")
    }
    if len(fault_wrappers_by_usr) != len(fault_wrappers):
        raise RuntimeError(
            "platform-fault contract has missing or duplicate identities"
        )
    fault_semantics = json.loads(
        FAULT_SEMANTICS_PATH.read_text(encoding="utf-8")
    )
    if fault_semantics.get("schema") != "p101-wrapper-fault-semantics-v3":
        raise RuntimeError("unsupported wrapper fault-semantics contract")
    fault_mechanism = fault_semantics.get("mechanism", {})
    if set(fault_mechanism) != {
        "hard_selector_usr",
        "action_selector_usr",
        "action_recorder_usr",
        "entry_trace_usr",
    }:
        raise RuntimeError("wrapper fault-semantics mechanism is incomplete")
    hard_selector_usr = fault_mechanism["hard_selector_usr"]
    action_selector_usr = fault_mechanism["action_selector_usr"]
    selector_usrs = {hard_selector_usr, action_selector_usr}
    entry_trace_usr = fault_mechanism["entry_trace_usr"]
    native_smoke_contract = json.loads(
        NATIVE_SMOKE_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    if set(native_smoke_contract) != {
        "schema",
        "default_outcome",
        "exceptions",
    }:
        raise RuntimeError(
            "wrapper native-smoke contract has unknown or missing fields"
        )
    if (
        native_smoke_contract.get("schema")
        != "p101-wrapper-native-smoke-contract-v2"
    ):
        raise RuntimeError("unsupported wrapper native-smoke contract")
    if native_smoke_contract.get("default_outcome") != "success":
        raise RuntimeError(
            "wrapper native-smoke contract must default to success"
        )
    native_smoke_exceptions = native_smoke_contract.get("exceptions", {})
    if not isinstance(native_smoke_exceptions, list):
        raise RuntimeError(
            "wrapper native-smoke exceptions must be a list"
        )
    native_smoke_exceptions_by_usr = {
        outcome.get("function_usr"): outcome
        for outcome in native_smoke_exceptions
        if isinstance(outcome, dict) and outcome.get("function_usr")
    }
    if len(native_smoke_exceptions_by_usr) != len(native_smoke_exceptions):
        raise RuntimeError(
            "wrapper native-smoke contract has missing or duplicate identities"
        )
    portable_input_contract = json.loads(
        PORTABLE_INPUT_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    if set(portable_input_contract) != {
        "schema",
        "supported_platforms",
        "default_policy",
        "evidence_contract",
        "policy",
        "rules",
    }:
        raise RuntimeError(
            "wrapper portable-input contract has unknown or missing fields"
        )
    if (
        portable_input_contract.get("schema")
        != "p101-wrapper-portable-input-contract-v2"
        or portable_input_contract.get("supported_platforms")
        != ["linux", "macos", "freebsd"]
        or portable_input_contract.get("default_policy")
        != "defer-to-native-platform"
        or portable_input_contract.get("evidence_contract")
        != "wrapper-platform-faults.json"
    ):
        raise RuntimeError("unsupported wrapper portable-input contract")
    portable_input_rules = portable_input_contract.get("rules", {})
    if not isinstance(portable_input_rules, list):
        raise RuntimeError("wrapper portable-input rules must be a list")
    portable_input_rules_by_usr: dict[str, list[dict[str, Any]]] = {}
    for rule_record in portable_input_rules:
        rule_usr = (
            rule_record.get("function_usr", "?")
            if isinstance(rule_record, dict)
            else "?"
        )
        if (
            not isinstance(rule_record, dict)
            or set(rule_record) != {"function_usr", "constraints"}
            or not isinstance(rule_record.get("function_usr"), str)
            or not rule_record["function_usr"]
            or rule_record["function_usr"] in portable_input_rules_by_usr
        ):
            raise RuntimeError(
                f"{rule_usr}: portable-input rule lacks a unique function identity"
            )
        constraints = rule_record["constraints"]
        if not isinstance(constraints, list):
            raise RuntimeError(
                f"{rule_usr}: portable-input constraints must be a list"
            )
        portable_input_rules_by_usr[rule_record["function_usr"]] = constraints
    for outcome in native_smoke_exceptions:
        if not isinstance(outcome, dict):
            raise RuntimeError(
                "wrapper native-smoke exceptions must contain objects"
            )
        name = str(outcome.get("function_usr", "?"))
        if outcome.get("outcome") not in {"error", "success-or-error"}:
            raise RuntimeError(
                f"{name}: invalid native-smoke exception outcome"
            )
        expected_fields = {
            "function_usr",
            "outcome",
            "rationale",
        }
        if outcome.get("outcome") == "error":
            expected_fields |= {
                "error_code",
                "error_domain",
                "result_kind",
            }
            if outcome.get("result_kind") == "equals":
                expected_fields.add("result_expression")
            elif outcome.get("result_kind") == "text":
                expected_fields.add("result_text")
        else:
            expected_fields |= {
                "allowed_error_codes",
                "error_result_expression",
            }
        if set(outcome) != expected_fields:
            raise RuntimeError(
                f"{name}: native-smoke exception has unknown or missing "
                f"fields: expected {sorted(expected_fields)}, "
                f"got {sorted(outcome)}"
            )
        if (
            outcome.get("outcome") == "error"
            and outcome.get("error_domain")
            not in {"check", "errno", "system", "user"}
        ):
            raise RuntimeError(
                f"{name}: invalid native-smoke error domain"
            )
        if not outcome.get("rationale"):
            raise RuntimeError(
                f"{name}: native-smoke exception lacks rationale"
            )
        if (
            outcome.get("outcome") == "error"
            and not outcome.get("error_code")
        ):
            raise RuntimeError(
                f"{name}: native-smoke error lacks an exact code"
            )
        if (
            outcome.get("outcome") == "success-or-error"
            and (
                not isinstance(outcome.get("allowed_error_codes"), list)
                or not outcome.get("allowed_error_codes")
                or any(
                    not isinstance(code, str) or not code
                    for code in outcome["allowed_error_codes"]
                )
                or len(set(outcome["allowed_error_codes"]))
                != len(outcome["allowed_error_codes"])
                or not outcome.get("error_result_expression")
            )
        ):
            raise RuntimeError(
                f"{name}: conditional native outcome lacks exact errors"
            )
        if outcome.get("result_kind") not in {None, "equals", "text"}:
            raise RuntimeError(
                f"{name}: invalid native-smoke result assertion"
            )
        if (
            outcome.get("result_kind") == "equals"
            and not outcome.get("result_expression")
        ):
            raise RuntimeError(
                f"{name}: equality result assertion lacks an expression"
            )
        if (
            outcome.get("result_kind") == "text"
            and not isinstance(outcome.get("result_text"), str)
        ):
            raise RuntimeError(
                f"{name}: text result assertion lacks exact text"
            )
    valid_outcome_classes = {
        "direct-hard-failure",
        "short-partial-result",
        "delegated-failure",
        "deterministic-rejection",
        "genuinely-infallible",
        "non-returning-cleanup",
    }
    failure_contract: dict[str, Any] = {
        "schema": "p101-wrapper-failure-contract-v2",
        "semantics": {
            "error_object": "exact-injected-code-and-domain",
            "errno": "preserved",
            "fault_boundary": "after-entry-trace-before-native-work",
            "writable_arguments": (
                "unchanged-by-early-return-with-portable-runtime-canaries"
            ),
            "resource_events": "none",
        },
        "wrappers": {},
    }
    wrapper_errors_by_usr: dict[str, dict[str, list[str]]] = {}
    admitted_usrs: set[str] = set()
    for binding in fault_wrappers_by_usr.values():
        wrapper_usr = binding["function_usr"]
        function = binding.get("function")
        if function is None:
            wrapper_errors_by_usr[wrapper_usr] = {
                platform_name: []
                for platform_name in ("linux", "macos", "freebsd", "posix")
            }
            continue
        wrapper_errors_by_usr[wrapper_usr] = {}
        for platform_name in ("linux", "macos", "freebsd"):
            errors, _domain, _selection, _source, _coverage = (
                effective_fault_selection(
                    fault_contract,
                    function,
                    platform_name,
                )
            )
            wrapper_errors_by_usr[wrapper_usr][platform_name] = errors
        errors, _domain, _selection, _source, _coverage = (
            effective_fault_selection(
                fault_contract,
                function,
                None,
            )
        )
        wrapper_errors_by_usr[wrapper_usr]["posix"] = errors

    for library, (repo, library_rows) in sorted(libraries.items()):
        admitted = {row["function"] for row in library_rows}
        function_usrs = {
            row["function"]: row.get("function_usr", "")
            for row in library_rows
        }
        if any(not function_usr for function_usr in function_usrs.values()):
            raise RuntimeError(
                f"{library}: public API manifest lacks function identities"
            )
        if len(set(function_usrs.values())) != len(function_usrs):
            raise RuntimeError(
                f"{library}: public API manifest repeats a function identity"
            )
        for row in library_rows:
            binding = fault_wrappers_by_usr.get(row["function_usr"])
            if binding is None:
                raise RuntimeError(
                    f"{row['function_usr']}: absent from platform-fault contract"
                )
            native_function = binding.get("function") or ""
            manifest_native_function = native_function or "-"
            native_usr = f"c:@F@{native_function}" if native_function else "-"
            if (
                row.get("native_function", "") != manifest_native_function
                or row.get("native_function_usr", "") != native_usr
            ):
                raise RuntimeError(
                    f"{row['function']}: API manifest native identity differs "
                    "from the reviewed platform-fault contract"
                )
        admitted_usrs.update(function_usrs.values())
        behavior_sources = sorted(
            source.resolve()
            for pattern in ("*.c", "*.C", "*.cc", "*.cpp", "*.cxx")
            for source in (repo / "test").glob(pattern)
            if source.name != "test_fault_wrappers.c"
        )
        try:
            implementation_facts = acquire(
                WORKSPACE,
                (repo / "src",),
                additional_include_roots=semantic_include_dirs,
            )
            behavior_facts = (
                acquire(
                    WORKSPACE,
                    behavior_sources,
                    additional_include_roots=semantic_include_dirs,
                )
                if behavior_sources
                else []
            )
        except CFactError as error:
            raise RuntimeError(str(error)) from error
        declarations = function_definitions(
            clang,
            library_rows,
            include_dirs,
            implementation_facts,
        )
        behavior_calls: dict[Path, set[str]] = {}
        for fact in behavior_facts:
            if fact["kind"] == "CALL":
                path = Path(str(fact["path"])).resolve()
                behavior_calls.setdefault(path, set()).add(
                    str(fact.get("usr", ""))
                )
        implementation_calls = [
            fact
            for fact in implementation_facts
            if fact["kind"] == "CALL"
        ]
        calls_by_wrapper_usr: dict[str, set[str]] = defaultdict(set)
        for fact in implementation_calls:
            caller_usr = str(fact.get("caller_usr", ""))
            callee_usr = str(fact.get("usr", ""))
            if caller_usr and callee_usr:
                calls_by_wrapper_usr[caller_usr].add(callee_usr)
        sources = {
            row["function"]: WORKSPACE / row["current_source"]
            for row in library_rows
        }
        source_names = {
            row["function"]: row["current_source"]
            for row in library_rows
        }
        for name in sorted(
            name
            for name in admitted
            if function_usrs[name] in portable_input_rules_by_usr
        ):
            declaration = declarations[name]
            parameters = [
                child
                for child in declaration.get("inner", [])
                if child.get("kind") == "ParmVarDecl"
            ]
            rules = portable_input_rules_by_usr[function_usrs[name]]
            if not isinstance(rules, list) or not rules:
                raise RuntimeError(
                    f"{name}: portable input rules must be a nonempty list"
                )
            for rule in rules:
                if not isinstance(rule, dict):
                    raise RuntimeError(
                        f"{name}: portable input rule must be an object"
                    )
                expected_rule_fields = {
                    "constraint",
                    "error_code",
                    "evidence_platforms",
                    "cases",
                    "parameter_index",
                    "type",
                }
                if set(rule) != expected_rule_fields:
                    raise RuntimeError(
                        f"{name}: portable input rule has unknown or missing "
                        f"fields: expected {sorted(expected_rule_fields)}, "
                        f"got {sorted(rule)}"
                    )
                index = rule.get("parameter_index")
                if (
                    type(index) is not int
                    or index < 0
                    or index >= len(parameters)
                ):
                    raise RuntimeError(
                        f"{name}: portable input rule has invalid parameter "
                        f"index {index!r}"
                    )
                actual_type = normalized_c_type(
                    parameters[index].get("type", {}).get(
                        "qualType",
                        "",
                    )
                )
                if actual_type != normalized_c_type(rule.get("type", "")):
                    raise RuntimeError(
                        f"{name}: portable input rule type "
                        f"{rule.get('type')!r} differs from public parameter "
                        f"{actual_type!r}"
                    )
                if (
                    not isinstance(rule.get("constraint"), str)
                    or not rule["constraint"]
                    or not isinstance(rule.get("error_code"), str)
                    or not rule["error_code"]
                    or not isinstance(rule.get("evidence_platforms"), list)
                    or not rule["evidence_platforms"]
                    or any(
                        not isinstance(platform, str)
                        for platform in rule["evidence_platforms"]
                    )
                    or len(set(rule["evidence_platforms"]))
                    != len(rule["evidence_platforms"])
                    or not set(rule["evidence_platforms"])
                    <= {"linux", "macos", "freebsd"}
                    or not isinstance(rule.get("cases"), list)
                    or not rule["cases"]
                ):
                    raise RuntimeError(
                        f"{name}: incomplete portable input rule"
                    )
                fault_binding = fault_wrappers_by_usr.get(
                    function_usrs[name], {}
                )
                native_function = fault_binding.get("function")
                if not native_function:
                    raise RuntimeError(
                        f"{name}: portable input rule lacks a native function"
                    )
                for evidence_platform in rule["evidence_platforms"]:
                    documented_errors, *_unused = effective_fault_selection(
                        fault_contract,
                        native_function,
                        evidence_platform,
                    )
                    if rule["error_code"] not in documented_errors:
                        raise RuntimeError(
                            f"{name}: portable input rule cites "
                            f"{evidence_platform} without documented "
                            f"{rule['error_code']} evidence"
                        )
                for case in rule["cases"]:
                    if not isinstance(case, dict):
                        raise RuntimeError(
                            f"{name}: portable input case must be an object"
                        )
                    allowed_case_fields = {
                        "companion_arguments",
                        "input_kind",
                        "result_kind",
                        "result_parameter_index",
                    }
                    if not {"input_kind", "result_kind"} <= set(case) or not set(
                        case
                    ) <= allowed_case_fields:
                        raise RuntimeError(
                            f"{name}: portable input case has unknown or "
                            "missing fields"
                        )
                    input_kind = case.get("input_kind")
                    if input_kind not in {
                        "catalog-failure",
                        "catalog-zero",
                        "negative-one",
                        "null",
                        "text-root-only",
                        "text-with-extra-slash",
                        "text-without-leading-slash",
                    }:
                        raise RuntimeError(
                            f"{name}: invalid portable input case "
                            f"{input_kind!r}"
                        )
                    if (
                        input_kind.startswith("catalog-")
                        and actual_type != "nl_catd"
                    ):
                        raise RuntimeError(
                            f"{name}: catalog input case requires nl_catd"
                        )
                    if input_kind == "negative-one" and actual_type != "int":
                        raise RuntimeError(
                            f"{name}: negative input case requires int"
                        )
                    if (
                        input_kind
                        in {
                            "null",
                            "text-root-only",
                            "text-with-extra-slash",
                            "text-without-leading-slash",
                        }
                        and "*" not in actual_type
                    ):
                        raise RuntimeError(
                            f"{name}: pointer input case requires a pointer"
                        )
                    if (
                        input_kind
                        in {
                            "text-root-only",
                            "text-with-extra-slash",
                            "text-without-leading-slash",
                        }
                        and actual_type != "const char *"
                    ):
                        raise RuntimeError(
                            f"{name}: text input case requires const char *"
                        )
                    result_kind = case.get("result_kind")
                    if result_kind not in {
                        "argument",
                        "catalog-failure",
                        "negative-one",
                        "null",
                        "pointer-failure",
                    }:
                        raise RuntimeError(
                            f"{name}: invalid portable result case "
                            f"{result_kind!r}"
                        )
                    result_index = case.get("result_parameter_index")
                    if result_kind == "argument":
                        if (
                            type(result_index) is not int
                            or result_index < 0
                            or result_index >= len(parameters)
                        ):
                            raise RuntimeError(
                                f"{name}: portable argument result has "
                                "an invalid parameter index"
                            )
                    elif result_index is not None:
                        raise RuntimeError(
                            f"{name}: portable result index is only valid "
                            "for argument results"
                        )
                    companions = case.get("companion_arguments", [])
                    if not isinstance(companions, list):
                        raise RuntimeError(
                            f"{name}: portable companion arguments must be "
                            "a list"
                        )
                    companion_indices: set[int] = set()
                    for companion in companions:
                        if not isinstance(companion, dict) or set(companion) != {
                            "parameter_index",
                            "value",
                            "value_kind",
                        }:
                            raise RuntimeError(
                                f"{name}: invalid portable companion argument"
                            )
                        companion_index = companion.get("parameter_index")
                        if (
                            type(companion_index) is not int
                            or companion_index < 0
                            or companion_index >= len(parameters)
                            or companion.get("value_kind") != "text"
                            or not isinstance(companion.get("value"), str)
                        ):
                            raise RuntimeError(
                                f"{name}: invalid portable companion argument"
                            )
                        if companion_index in companion_indices:
                            raise RuntimeError(
                                f"{name}: duplicate portable companion "
                                f"parameter {companion_index}"
                            )
                        companion_indices.add(companion_index)
                        companion_type = normalized_c_type(
                            parameters[companion_index]
                            .get("type", {})
                            .get("qualType", "")
                        )
                        if companion_type != "const char *":
                            raise RuntimeError(
                                f"{name}: portable text companion requires "
                                "const char *"
                            )
        manifest_path = repo / "test" / "unit-test-manifest.tsv"
        faultable = {
            name
            for name in declarations
            if calls_by_wrapper_usr.get(function_usrs[name], set())
            & selector_usrs
        }
        fault_names = sorted(admitted & faultable)
        behavior_names = sorted(admitted - faultable)
        for name in sorted(admitted):
            wrapper_usr = function_usrs[name]
            outcome = outcome_apis_by_usr.get(wrapper_usr)
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
            if outcome.get("function_usr") != function_usrs[name]:
                raise RuntimeError(
                    f"{name}: outcome contract declaration identity differs "
                    "from the public API manifest"
                )
            fault_binding = fault_wrappers_by_usr[wrapper_usr]
            expected_role = fault_binding.get("role")
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
            call_usrs = calls_by_wrapper_usr.get(
                function_usrs[name],
                set(),
            )
            has_hard_boundary = hard_selector_usr in call_usrs
            has_action_boundary = action_selector_usr in call_usrs
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
            native_function = fault_binding.get("function")
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
                outcome_by_usr = {
                    record.get("function_usr"): record
                    for record in outcome_apis.values()
                    if isinstance(record, dict)
                }
                delegated_targets = {
                    target_usr
                    for target_usr in call_usrs
                    if outcome_by_usr.get(target_usr, {}).get("classification")
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
            validate_fault_boundary(
                declarations[name],
                function_usrs[name],
                implementation_calls,
                selector_usrs,
                entry_trace_usr,
            )
            failure = fault_return_contract(
                declarations[name],
                sources[name],
                function_usrs[name],
                implementation_calls,
                selector_usrs,
            )
            expected_domain = fault_domain(
                fault_contract,
                fault_wrappers_by_usr[function_usrs[name]].get("function"),
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
                {
                    "index": index,
                    "type": parameter.get("type", {}).get(
                        "desugaredQualType",
                        parameter.get("type", {}).get("qualType", ""),
                    ),
                }
                for index, parameter in enumerate(parameters)
                if argument_expression(parameter, declarations[name])
                not in {"env", "err"}
                and writable_fixture(
                    parameter,
                    index,
                    declarations[name],
                )[0]
            ]
            library_failures[function_usrs[name]] = failure
            failure_contract["wrappers"][name] = {
                "function_usr": function_usrs[name],
                "library": library,
                "error_domain": expected_domain,
                "return_kind": failure["kind"],
                "return_expression": failure["expression"],
                "errno": "preserved",
                "fault_boundary": "after-entry-trace-before-native-work",
                "fault_modes": (
                    ["error", "short"]
                    if action_selector_usr
                    in calls_by_wrapper_usr.get(function_usrs[name], set())
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
                    function_usrs,
                    fault_names,
                    wrapper_errors_by_usr,
                    library_failures,
                    native_smoke_exceptions_by_usr,
                    portable_input_rules_by_usr,
                ),
            )
            validate_generated_source_semantics(
                library,
                expected_fault_source,
            )
            if check:
                actual_fault_source = (
                    fault_path.read_text(encoding="utf-8")
                    if fault_path.is_file()
                    else None
                )
                normalized_actual = (
                    formatted_source(
                        formatter,
                        fault_path,
                        actual_fault_source,
                    )
                    if actual_fault_source is not None
                    else None
                )
                if normalized_actual != expected_fault_source:
                    if normalized_actual is not None:
                        print(
                            "".join(
                                difflib.unified_diff(
                                    normalized_actual.splitlines(
                                        keepends=True
                                    ),
                                    expected_fault_source.splitlines(
                                        keepends=True
                                    ),
                                    fromfile=f"{fault_path} (checked in)",
                                    tofile=f"{fault_path} (generated)",
                                )
                            ),
                            end="",
                        )
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
        manifest = ["function\tfunction_usr\ttest_kind\ttest_source\n"]
        manifest.extend(
            f"{name}\t{function_usrs[name]}\tfault\t"
            "test/test_fault_wrappers.c\n"
            for name in fault_names
        )
        for name in behavior_names:
            existing = existing_behavior_source(
                repo, function_usrs[name], behavior_calls
            )
            if existing is None:
                manifest.append(
                    f"{name}\t{function_usrs[name]}\tbehavior\t"
                    "test/test_behavior.c\n"
                )
            else:
                kind, source = existing
                manifest.append(
                    f"{name}\t{function_usrs[name]}\t{kind}\t{source}\n"
                )
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
    extra_outcomes = set(outcome_apis_by_usr) - admitted_usrs
    if extra_outcomes:
        raise RuntimeError(
            "wrapper outcome contract contains non-public APIs: "
            + ", ".join(sorted(extra_outcomes))
        )
    extra_native_smoke_outcomes = (
        set(native_smoke_exceptions_by_usr) - admitted_usrs
    )
    if extra_native_smoke_outcomes:
        raise RuntimeError(
            "wrapper native-smoke contract contains non-public APIs: "
            + ", ".join(sorted(extra_native_smoke_outcomes))
        )
    extra_portable_input_rules = (
        set(portable_input_rules_by_usr) - admitted_usrs
    )
    if extra_portable_input_rules:
        raise RuntimeError(
            "wrapper portable-input contract contains non-public APIs: "
            + ", ".join(sorted(extra_portable_input_rules))
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
