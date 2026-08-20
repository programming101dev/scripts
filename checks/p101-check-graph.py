#!/usr/bin/env python3
"""Validate or execute the governed p101 post-update check graph."""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import functools
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

CHECKS_DIR = Path(__file__).resolve().parent
if os.fspath(CHECKS_DIR) not in sys.path:
    sys.path.insert(0, os.fspath(CHECKS_DIR))

from p101_check_plan import (
    GraphError,
    expand_command,
    impact_closure,
    impact_dependency_closure,
    select_nodes,
    validate,
    workspace_changed_paths,
)
from p101_check_reporting import log_result, write_profile, write_summary


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = SCRIPTS_ROOT / "runtime"
if os.fspath(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.fspath(RUNTIME_DIR))

from clang_ast import cache_entry_available as clang_ast_cache_entry_available

DEFAULT_GRAPH = SCRIPTS_ROOT / "contracts" / "p101-check-graph.json"
CACHE_RECEIPT_SCHEMA = "p101-check-evidence-cache-v1"
RUN_RECEIPT_SCHEMA = "p101-check-graph-receipt-v2"
SOURCE_EXCLUDES = {
    ".git",
    ".flags",
    "__pycache__",
    "ci-output",
    "target",
}
SOURCE_EXCLUDE_PREFIXES = ("build-",)
SEMANTIC_ENVIRONMENT = {
    "ASAN_OPTIONS",
    "LANG",
    "LC_ALL",
    "LSAN_OPTIONS",
    "PATH",
    "TSAN_OPTIONS",
    "UBSAN_OPTIONS",
}
COMPILER_ENVIRONMENT = {
    "CC",
    "CFLAGS",
    "CPPFLAGS",
    "CXX",
    "CXXFLAGS",
    "LDFLAGS",
}
ORCHESTRATION_ENVIRONMENT = {
    "P101_ACCEPTANCE_CXX_COMPILER",
    "P101_ACCEPTANCE_NO_CACHE",
    "P101_ACCEPTANCE_OUTPUT_DIR",
    "P101_C_FACTS_CACHE_DIR",
    "P101_CHECK_GRAPH",
    "P101_CONTENT_MANIFEST",
    "P101_FACTS_CACHE",
    "P101_GITHUB_CC",
    "P101_GITHUB_CLANG_FORMAT",
    "P101_GITHUB_CLANG_TIDY",
    "P101_GITHUB_CPPCHECK",
    "P101_GITHUB_CXX",
    "P101_JOBS",
    "P101_QUIET",
    "P101_REPOSITORY_BUILD_CACHE",
    "P101_REPOS_LOCK",
    "P101_SEMANTIC_CACHE_ROOT",
    "P101_SEMANTIC_USAGE_LOG",
    "P101_STACK_CONTRACT",
    "P101_STACK_REPOS_LOCK",
}
TOOL_LOCATOR_ENVIRONMENT = {
    "P101_AUDIT_API",
    "P101_AUDIT_DOCTOR",
    "P101_AUDIT_ERRORS",
    "P101_AUDIT_FACTS",
    "P101_AUDIT_MODULES",
    "P101_AUDIT_WRAPPERS",
    "P101_AUDIT_WORKSPACE",
    "P101_EVENT_MODEL",
    "P101_INSPECT_CAPTURE",
    "P101_INSPECT",
    "P101_TEST_FAULTS",
    "P101_TEST_MUTATION",
    "P101_TEST_REPOSITORY_RECEIPT",
    "P101_TEST_WRAPPER_CONFORMANCE",
    "P101_TEST_COMMAND_CONTRACT",
    "P101_TOOL_RECEIPT",
}


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def normalized_platform(system: str) -> str:
    return {
        "Darwin": "macos",
        "FreeBSD": "freebsd",
        "Linux": "linux",
    }.get(system, system.lower())


def read_json_receipt(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GraphError(f"{path}: unreadable receipt: {error}") from error
    if not isinstance(document, dict):
        raise GraphError(f"{path}: receipt is not a JSON object")
    return document


def verified_graph_receipt(path: Path) -> dict[str, Any]:
    receipt = read_json_receipt(path)
    if receipt.get("schema") != RUN_RECEIPT_SCHEMA:
        raise GraphError(f"{path}: unsupported receipt schema")
    claimed_digest = receipt.get("receipt_digest")
    unsigned = dict(receipt)
    unsigned.pop("receipt_digest", None)
    if claimed_digest != canonical_sha256(unsigned):
        raise GraphError(f"{path}: receipt digest mismatch")
    return receipt


def merge_instrumentation_receipts(
    paths: list[Path], required_platforms: set[str]
) -> None:
    receipts: list[dict[str, Any]] = []
    platforms: dict[str, Path] = {}
    contract_identities: set[str] = set()
    functions: set[str] = set()
    for path in paths:
        receipt = read_json_receipt(path)
        if receipt.get("schema") != "p101-instrumentation-platform-receipt-v1":
            raise GraphError(f"{path}: unsupported instrumentation receipt schema")
        platform_name = receipt.get("platform")
        if not isinstance(platform_name, str) or not platform_name:
            raise GraphError(f"{path}: instrumentation receipt has no platform")
        if platform_name in platforms:
            raise GraphError(
                f"duplicate platform receipt: {platform_name} "
                f"({platforms[platform_name]}, {path})"
            )
        if receipt.get("passed") is not True:
            raise GraphError(
                f"{path}: platform instrumentation audit did not pass"
            )
        contract_identity = receipt.get("contract_sha256")
        if not isinstance(contract_identity, str) or not contract_identity:
            raise GraphError(f"{path}: instrumentation contract has no identity")
        function_rows = receipt.get("functions")
        if not isinstance(function_rows, list) or any(
            not isinstance(row, str) or not row for row in function_rows
        ):
            raise GraphError(f"{path}: instrumentation function inventory is invalid")
        platforms[platform_name] = path
        contract_identities.add(contract_identity)
        functions.update(function_rows)
        receipts.append(receipt)
    missing = required_platforms - set(platforms)
    extra = set(platforms) - required_platforms
    if missing:
        raise GraphError(
            "missing required platforms: " + ", ".join(sorted(missing))
        )
    if extra:
        raise GraphError(
            "unexpected platforms: " + ", ".join(sorted(extra))
        )
    if len(contract_identities) != 1:
        raise GraphError(
            "platform receipts used different instrumentation contracts"
        )
    print(f"platform receipts: {len(receipts)}")
    print(f"platforms covered: {', '.join(sorted(platforms))}")
    print(f"union instrumented functions: {len(functions)}")
    print("cross-platform instrumentation receipts passed")


def merge_quality_receipts(paths: list[Path], required_platforms: set[str]) -> None:
    platforms: dict[str, Path] = {}
    stack_identities: set[str] = set()
    graph_identities: set[str] = set()
    for path in paths:
        receipt = verified_graph_receipt(path)
        host = receipt.get("host")
        system = host.get("system") if isinstance(host, dict) else None
        if not isinstance(system, str) or not system:
            raise GraphError(f"{path}: missing host system")
        platform_name = normalized_platform(system)
        if platform_name in platforms:
            raise GraphError(
                f"duplicate platform receipt: {platform_name} "
                f"({platforms[platform_name]}, {path})"
            )
        platforms[platform_name] = path
        checks = receipt.get("checks")
        if (
            receipt.get("outcome") != "clean"
            or not isinstance(checks, dict)
            or checks.get("attempted") != checks.get("completed")
        ):
            raise GraphError(f"{path}: governed graph did not complete cleanly")
        stack = receipt.get("stack_contract")
        if not isinstance(stack, dict) or stack.get("valid") is not True:
            raise GraphError(f"{path}: stack contract is absent or invalid")
        stack_identity = stack.get("contract_sha256")
        if not isinstance(stack_identity, str) or not stack_identity:
            raise GraphError(f"{path}: stack contract has no identity")
        input_value = receipt.get("input")
        if not isinstance(input_value, dict):
            raise GraphError(f"{path}: input identity is absent")
        input_schema = input_value.get("schema")
        input_identity = input_value.get("identity")
        if not isinstance(input_schema, str) or not isinstance(input_identity, str):
            raise GraphError(f"{path}: graph identity is invalid")
        stack_identities.add(stack_identity)
        graph_identities.add(f"{input_schema}:{input_identity}")
    missing = required_platforms - set(platforms)
    extra = set(platforms) - required_platforms
    if missing:
        raise GraphError(
            "missing required platforms: " + ", ".join(sorted(missing))
        )
    if extra:
        raise GraphError(
            "unexpected platforms: " + ", ".join(sorted(extra))
        )
    if len(stack_identities) != 1:
        raise GraphError("platform receipts used different stack contracts")
    if len(graph_identities) != 1:
        raise GraphError("platform receipts used different check graphs")
    print(
        "p101 platform quality receipts: "
        f"{len(platforms)} receipts, "
        f"platforms={','.join(sorted(platforms))}, "
        f"stack={next(iter(stack_identities))}"
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def source_path_excluded(path: Path) -> bool:
    return any(
        part in SOURCE_EXCLUDES
        or any(part.startswith(prefix) for prefix in SOURCE_EXCLUDE_PREFIXES)
        for part in path.parts
    )


def repository_source_files(repository: Path) -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            os.fspath(repository),
            "ls-files",
            "-co",
            "--exclude-standard",
            "-z",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode == 0:
        relative_paths = [
            Path(os.fsdecode(value))
            for value in completed.stdout.split(b"\0")
            if value
        ]
        return sorted(
            (
                repository / relative
                for relative in relative_paths
                if not source_path_excluded(relative)
                and (
                    (repository / relative).exists()
                    or (repository / relative).is_symlink()
                )
            ),
            key=lambda path: os.fspath(path),
        )
    return sorted(
        (
            path
            for path in repository.rglob("*")
            if (path.is_file() or path.is_symlink())
            and not source_path_excluded(path.relative_to(repository))
        ),
        key=lambda path: os.fspath(path),
    )


def active_repository_roots() -> list[Path]:
    workspace = SCRIPTS_ROOT.parent.resolve()
    roots = [SCRIPTS_ROOT.resolve()]
    manifest = SCRIPTS_ROOT / "repos.txt"
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        fields = text.split("|")
        if len(fields) != 3:
            raise GraphError(f"{manifest}: expected url|path|type")
        root = (SCRIPTS_ROOT / fields[1].strip()).resolve()
        if root != workspace and workspace not in root.parents:
            raise GraphError(f"repository path escapes workspace: {fields[1]!r}")
        if root.is_dir():
            roots.append(root)
    return sorted(set(roots), key=lambda path: os.fspath(path))


class WorkspaceSourceIndex:
    """Hash workspace source bytes once, then derive exact scoped identities."""

    @staticmethod
    def _stable_payload(path: Path) -> tuple[bytes, bytes, os.stat_result]:
        """Read bytes whose identity matches the returned filesystem status."""
        for _attempt in range(3):
            before = path.lstat()
            if path.is_symlink():
                payload = os.readlink(path).encode(
                    "utf-8", errors="surrogateescape"
                )
                kind = b"symlink"
            elif path.is_file():
                payload = path.read_bytes()
                kind = b"file"
            else:
                return b"", b"", before
            after = path.lstat()
            before_identity = (
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            after_identity = (
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if before_identity == after_identity:
                return payload, kind, after
        raise RuntimeError(f"workspace source changed while hashing: {path}")

    def __init__(self) -> None:
        self.workspace = SCRIPTS_ROOT.parent.resolve()
        self.repositories: list[tuple[dict[str, Any], tuple[Path, ...]]] = []
        self.file_records: dict[
            Path, tuple[str, bytes, bytes, int, int, int, int, int]
        ] = {}
        self.pattern_files: dict[str, frozenset[Path]] = {}
        for repository in active_repository_roots():
            metadata: dict[str, Any] = {
                "path": repository.relative_to(self.workspace).as_posix()
            }
            for key, arguments in (
                ("head", ["rev-parse", "--verify", "HEAD"]),
                ("origin", ["remote", "get-url", "origin"]),
                (
                    "upstream",
                    [
                        "rev-parse",
                        "--abbrev-ref",
                        "--symbolic-full-name",
                        "@{u}",
                    ],
                ),
                (
                    "status",
                    ["status", "--porcelain=v1", "--untracked-files=normal"],
                ),
            ):
                completed = subprocess.run(
                    ["git", "-C", os.fspath(repository), *arguments],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    check=False,
                )
                metadata[key] = (
                    completed.stdout.strip() if completed.returncode == 0 else ""
                )
            files: list[Path] = []
            for path in repository_source_files(repository):
                payload, kind, status = self._stable_payload(path)
                if not kind:
                    continue
                relative = path.relative_to(self.workspace).as_posix()
                self.file_records[path] = (
                    relative,
                    kind,
                    hashlib.sha256(payload).digest(),
                    len(payload),
                    status.st_mtime_ns,
                    status.st_ctime_ns,
                    status.st_dev,
                    status.st_ino,
                )
                files.append(path)
            self.repositories.append((metadata, tuple(files)))

    def files_for_pattern(self, pattern: str) -> frozenset[Path]:
        cached = self.pattern_files.get(pattern)
        if cached is not None:
            return cached
        pattern_parts = tuple(part for part in pattern.split("/") if part)

        def matches_parts(path_parts: tuple[str, ...]) -> bool:
            remembered: dict[tuple[int, int], bool] = {}

            def matches(pattern_index: int, path_index: int) -> bool:
                key = (pattern_index, path_index)
                if key in remembered:
                    return remembered[key]
                if pattern_index == len(pattern_parts):
                    result = path_index == len(path_parts)
                elif pattern_parts[pattern_index] == "**":
                    if pattern_index + 1 == len(pattern_parts):
                        result = path_index < len(path_parts)
                    else:
                        result = matches(pattern_index + 1, path_index) or (
                            path_index < len(path_parts)
                            and matches(pattern_index, path_index + 1)
                        )
                else:
                    result = (
                        path_index < len(path_parts)
                        and fnmatch.fnmatchcase(
                            path_parts[path_index], pattern_parts[pattern_index]
                        )
                        and matches(pattern_index + 1, path_index + 1)
                    )
                remembered[key] = result
                return result

            return matches(0, 0)

        matches = {
            path
            for path, record in self.file_records.items()
            if matches_parts(tuple(record[0].split("/")))
        }
        result = frozenset(matches)
        self.pattern_files[pattern] = result
        return result

    def identity(
        self, input_patterns: tuple[str, ...] | None = None
    ) -> dict[str, Any]:
        admitted_files: set[Path] | None = None
        if input_patterns is not None:
            admitted_files = set()
            for pattern in input_patterns:
                admitted_files.update(self.files_for_pattern(pattern))
            if not admitted_files:
                # A stale declaration must invalidate conservatively rather
                # than create a content-free cache key.
                return self.identity()
        digest = hashlib.sha256()
        file_count = 0
        byte_count = 0
        repository_identities = []
        include_repository_state = input_patterns is None or "**" in input_patterns
        for metadata, repository_files in self.repositories:
            selected = repository_files
            if admitted_files is not None:
                selected = tuple(
                    path for path in repository_files if path in admitted_files
                )
                if not selected:
                    continue
            repository_identities.append(
                metadata if include_repository_state else {"path": metadata["path"]}
            )
            for path in selected:
                (
                    relative,
                    kind,
                    payload_digest,
                    payload_size,
                    _modified_ns,
                    _changed_ns,
                    _device,
                    _inode,
                ) = self.file_records[path]
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                digest.update(kind)
                digest.update(b"\0")
                digest.update(payload_digest)
                digest.update(b"\0")
                file_count += 1
                byte_count += payload_size
        digest.update(
            json.dumps(
                repository_identities,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return {
            "algorithm": "sha256",
            "digest": "sha256:" + digest.hexdigest(),
            "files": file_count,
            "bytes": byte_count,
            "repositories": repository_identities,
            "inputs": list(input_patterns) if input_patterns is not None else ["**"],
        }

    def write_manifest(self, path: Path) -> None:
        files = []
        for record in sorted(self.file_records.values(), key=lambda value: value[0]):
            (
                relative,
                kind,
                payload_digest,
                payload_size,
                modified_ns,
                changed_ns,
                device,
                inode,
            ) = record
            files.append(
                {
                    "path": relative,
                    "kind": kind.decode("ascii"),
                    "sha256": payload_digest.hex(),
                    "bytes": payload_size,
                    "modified_ns": modified_ns,
                    "changed_ns": changed_ns,
                    "device": device,
                    "inode": inode,
                }
            )
        document = {
            "schema": "p101-workspace-content-manifest-v1",
            "workspace": os.fspath(self.workspace),
            "files": files,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


def workspace_source_identity(
    input_patterns: tuple[str, ...] | None = None,
    source_indexes: dict[str, WorkspaceSourceIndex] | None = None,
) -> dict[str, Any]:
    if source_indexes is None:
        index = WorkspaceSourceIndex()
    else:
        index = source_indexes.get("workspace")
        if index is None:
            index = WorkspaceSourceIndex()
            source_indexes["workspace"] = index
    return index.identity(input_patterns)


@functools.lru_cache(maxsize=None)
def tool_identity(value: str) -> dict[str, Any]:
    candidate = Path(value)
    if "/" in value:
        path = (
            candidate.resolve()
            if candidate.is_absolute()
            else (SCRIPTS_ROOT / candidate).resolve()
        )
    else:
        found = shutil.which(value)
        path = Path(found).resolve() if found else candidate
    result: dict[str, Any] = {"requested": value, "resolved": os.fspath(path)}
    try:
        workspace = SCRIPTS_ROOT.parent.resolve()
        if path.is_file() and (path == workspace or workspace in path.parents):
            # Qualified tools are built below content-addressed candidate
            # directories.  Their location is orchestration state; their
            # executable bytes are the semantic input.
            result = {
                "executable": path.name,
                "sha256": file_sha256(path),
            }
        elif path.exists():
            stat = path.stat()
            result["bytes"] = stat.st_size
            result["modified_ns"] = stat.st_mtime_ns
            completed = subprocess.run(
                [os.fspath(path), "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=5,
            )
            result["version"] = (
                completed.stdout.splitlines()[0] if completed.stdout else ""
            )
        else:
            result["missing"] = True
    except (OSError, subprocess.SubprocessError):
        result["unavailable"] = True
    return result


def semantic_environment(*, compiler_sensitive: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        admitted = (
            key in SEMANTIC_ENVIRONMENT
            or (compiler_sensitive and key in COMPILER_ENVIRONMENT)
            or key.startswith("P101_")
        )
        if not admitted or key in ORCHESTRATION_ENVIRONMENT:
            continue
        if key in TOOL_LOCATOR_ENVIRONMENT:
            result[key] = canonical_sha256(tool_identity(value))
        else:
            result[key] = value
    return result


def environment_file_identity(name: str) -> dict[str, Any]:
    value = os.environ.get(name, "")
    identity: dict[str, Any] = {"name": name, "set": bool(value)}
    if not value:
        return identity
    path = Path(value)
    try:
        if path.is_file():
            identity["bytes"] = path.stat().st_size
            identity["sha256"] = file_sha256(path)
        else:
            identity["missing"] = True
    except OSError:
        identity["unavailable"] = True
    return identity


def normalize_for_identity(
    value: str, output: Path, semantic_cache: str = ""
) -> str:
    normalized = value.replace(os.fspath(output), "{out}")
    if semantic_cache:
        normalized = normalized.replace(
            semantic_cache, "{semantic_cache}"
        )
    return normalized


def node_input_identity(
    node: dict[str, Any],
    command: list[str],
    variables: dict[str, str],
    output: Path,
    workspace: dict[str, Any],
    dependency_identities: dict[str, str] | None = None,
) -> str:
    variable_names = set(
        re.findall(
            r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
            json.dumps(node, ensure_ascii=False, sort_keys=True),
        )
    )
    compiler_sensitive = bool(
        {"cc", "cxx"} & variable_names
        or "compiler" in node.get("resources", {}).get("units", {})
    )
    tools = [command[0]]
    if compiler_sensitive:
        for name in ("cc", "cxx"):
            value = variables.get(name)
            if value:
                tools.append(value)
    if node.get("receipts"):
        tools.append(variables.get("receipt_verifier", "p101-tool-receipt"))
    normalized_node = dict(node)
    semantic_cache = variables.get("semantic_cache", "")
    normalized_node["command"] = [
        normalize_for_identity(value, output, semantic_cache)
        for value in command
    ]
    payload = {
        "schema": "p101-check-node-input-v1",
        "node": normalized_node,
        "command": [
            normalize_for_identity(value, output, semantic_cache)
            for value in command
        ],
        "variables": {
            key: normalize_for_identity(value, output, semantic_cache)
            for key, value in sorted(variables.items())
            if key in variable_names
            if key not in {"out", "semantic_cache"}
            and (key != "receipt_verifier" or bool(node.get("receipts")))
        },
        "workspace": workspace,
        "dependencies": dependency_identities or {},
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "environment": semantic_environment(
            compiler_sensitive=compiler_sensitive
        ),
        "environment_files": [
            environment_file_identity(name)
            for name in node.get("environment_files", [])
        ],
        "tools": [tool_identity(value) for value in sorted(set(tools))],
    }
    return canonical_sha256(payload)


def output_manifest(path: Path) -> dict[str, Any]:
    if not path.exists() and not path.is_symlink():
        return {"kind": "missing"}
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path)}
    if path.is_file():
        return {
            "kind": "file",
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
    rows: list[dict[str, Any]] = []
    for candidate in sorted(path.rglob("*"), key=lambda item: os.fspath(item)):
        relative = candidate.relative_to(path).as_posix()
        if candidate.is_symlink():
            rows.append(
                {"path": relative, "kind": "symlink", "target": os.readlink(candidate)}
            )
        elif candidate.is_file():
            rows.append(
                {
                    "path": relative,
                    "kind": "file",
                    "bytes": candidate.stat().st_size,
                    "sha256": file_sha256(candidate),
                }
            )
    return {
        "kind": "directory",
        "files": len(rows),
        "sha256": canonical_sha256(rows),
    }


def remove_owned_output(path: Path, output: Path) -> None:
    resolved = path.resolve(strict=False)
    if resolved != output and output not in resolved.parents:
        raise GraphError(f"cached output escapes run directory: {path}")
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def copy_output(source: Path, destination: Path) -> None:
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source))
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    elif source.is_dir():
        shutil.copytree(source, destination)


def prune_obsolete_usage_logs(output: Path, nodes: list[dict[str, Any]]) -> None:
    usage_directory = output / "semantic-usage"
    if not usage_directory.is_dir():
        return
    admitted = {node["id"] for node in nodes}
    for path in usage_directory.glob("*.jsonl"):
        if path.stem not in admitted:
            path.unlink()


def semantic_usage_available(path: Path, semantic_cache: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return False
        kind = record.get("kind")
        key = record.get("key")
        if not isinstance(key, str):
            return False
        if kind == "runtime-facts":
            entry = semantic_cache / f"{key}.json.gz"
        elif kind == "compile-database-facts":
            entry = semantic_cache / "entries" / key
        elif kind == "clang-ast":
            if not clang_ast_cache_entry_available(semantic_cache, key):
                return False
            continue
        else:
            return False
        if not entry.exists():
            return False
    return True


def expanded_writes(
    node: dict[str, Any], variables: dict[str, str], output: Path
) -> list[Path]:
    paths: list[Path] = []
    for value in node.get("resources", {}).get("writes", []):
        if value == "{temporary_root}":
            continue
        expanded = value.format_map(variables)
        if not expanded:
            continue
        path = Path(expanded)
        if not path.is_absolute():
            path = SCRIPTS_ROOT / path
        resolved = path.resolve(strict=False)
        if resolved != output and output not in resolved.parents:
            raise GraphError(
                f"node {node['id']} cache output is outside the run directory: {path}"
            )
        paths.append(path)
    paths.append(output / "semantic-usage" / f"{node['id']}.jsonl")
    return paths


def expanded_receipts(
    node: dict[str, Any], variables: dict[str, str], output: Path
) -> list[Path]:
    paths: list[Path] = []
    declared_outputs = expanded_writes(node, variables, output)
    for value in node.get("receipts", []):
        expanded = value.format_map(variables)
        path = Path(expanded)
        if not path.is_absolute():
            path = SCRIPTS_ROOT / path
        resolved = path.resolve(strict=False)
        if resolved != output and output not in resolved.parents:
            raise GraphError(
                f"node {node['id']} receipt is outside the run directory: {path}"
            )
        if not any(
            resolved == declared or declared in resolved.parents
            for declared in declared_outputs
        ):
            raise GraphError(
                f"node {node['id']} receipt is not covered by a declared output: {path}"
            )
        paths.append(path)
    return paths


def declared_output_records(
    node: dict[str, Any], variables: dict[str, str], output: Path
) -> list[dict[str, Any]]:
    return [
        {
            "destination": normalize_for_identity(os.fspath(path), output),
            "manifest": output_manifest(path),
        }
        for path in expanded_writes(node, variables, output)
    ]


def declared_outputs_match(
    record: dict[str, Any],
    node: dict[str, Any],
    variables: dict[str, str],
    output: Path,
) -> bool:
    expected = record.get("outputs")
    return isinstance(expected, list) and expected == declared_output_records(
        node, variables, output
    )


def cache_entry_paths(cache_directory: Path, key: str) -> tuple[Path, Path, Path]:
    digest = key.removeprefix("sha256:")
    entry = cache_directory / digest[:2] / digest
    return entry, entry / "receipt.json", entry / "log.txt"


def restore_cache_entry(
    cache_directory: Path,
    key: str,
    node: dict[str, Any],
    variables: dict[str, str],
    output: Path,
    log_path: Path,
    semantic_cache: Path,
) -> dict[str, Any] | None:
    entry, receipt_path, cached_log = cache_entry_paths(cache_directory, key)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("schema") != CACHE_RECEIPT_SCHEMA
            or receipt.get("node") != node["id"]
            or receipt.get("input_identity") != key
            or receipt.get("log_sha256") != file_sha256(cached_log)
        ):
            return None
        writes = expanded_writes(node, variables, output)
        cached_outputs = receipt.get("outputs")
        if not isinstance(cached_outputs, list) or len(cached_outputs) != len(writes):
            return None
        pairs = []
        usage_destination = output / "semantic-usage" / f"{node['id']}.jsonl"
        for index, (destination, expected) in enumerate(zip(writes, cached_outputs)):
            stored = entry / "outputs" / str(index)
            if expected.get("manifest") != output_manifest(stored):
                return None
            if destination == usage_destination and not semantic_usage_available(
                stored, semantic_cache
            ):
                return None
            pairs.append((destination, expected, stored))
        for destination, expected, stored in pairs:
            remove_owned_output(destination, output)
            if stored.exists() or stored.is_symlink():
                copy_output(stored, destination)
            if expected.get("manifest") != output_manifest(destination):
                return None
        log_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached_log, log_path)
        return receipt
    except (OSError, TypeError, ValueError, json.JSONDecodeError, GraphError):
        return None


def publish_cache_entry(
    cache_directory: Path,
    key: str,
    node: dict[str, Any],
    variables: dict[str, str],
    output: Path,
    log_path: Path,
) -> None:
    entry, receipt_path, _ = cache_entry_paths(cache_directory, key)
    if receipt_path.is_file():
        return
    temporary = entry.with_name(
        f"{entry.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    )
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        shutil.copy2(log_path, temporary / "log.txt")
        outputs = []
        for index, source in enumerate(expanded_writes(node, variables, output)):
            stored = temporary / "outputs" / str(index)
            if source.exists() or source.is_symlink():
                copy_output(source, stored)
            outputs.append(
                {
                    "destination": normalize_for_identity(os.fspath(source), output),
                    "manifest": output_manifest(stored),
                }
            )
        receipt = {
            "schema": CACHE_RECEIPT_SCHEMA,
            "node": node["id"],
            "input_identity": key,
            "log_sha256": file_sha256(temporary / "log.txt"),
            "outputs": outputs,
            "does_not_prove": (
                "A cache hit proves only that this retained verdict and its declared "
                "outputs match the exact recorded source, command, tool, host, and "
                "environment identity; it does not prove undeclared inputs."
            ),
        }
        (temporary / "receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        entry.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.rename(entry)
        except OSError:
            # Cache publication is opportunistic. A concurrent publisher,
            # read-only cache, or full cache filesystem must not change the
            # verdict of the check whose evidence was already produced.
            return
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def print_failure_log(log_path: Path) -> None:
    """Print the complete failure receipt to the calling terminal."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        print(f"    unable to read failure log: {error}")
        return

    print("    --- failure log ---")
    for line in lines:
        print(f"    | {line}")
    print("    --- end failure log ---")


def first_log_diagnostic(log_path: Path, fallback: str) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return fallback
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("$ ") and not stripped.startswith("# retry "):
            return stripped
    return fallback


def workspace_lock_identity(output: Path) -> dict[str, Any] | None:
    path = output / "workspace-lock-receipt.json"
    if not path.is_file():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"receipt": path.name, "valid": False}
    return {
        "receipt": path.name,
        "valid": receipt.get("schema") == "p101-repository-lock-receipt-v1"
        and receipt.get("passed") is True,
        "lock_sha256": receipt.get("lock_sha256", ""),
        "manifest_sha256": receipt.get("manifest_sha256", ""),
        "repository_count": receipt.get("repository_count", 0),
    }


def stack_contract_identity(output: Path) -> dict[str, Any] | None:
    path = output / "stack-contract-receipt.json"
    if not path.is_file():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"receipt": path.name, "valid": False}
    claimed_digest = receipt.get("receipt_digest")
    unsigned = dict(receipt)
    unsigned.pop("receipt_digest", None)
    digest_valid = (
        isinstance(claimed_digest, str)
        and claimed_digest == canonical_sha256(unsigned)
    )
    return {
        "receipt": path.name,
        "valid": receipt.get("schema") == "p101-stack-contract-receipt-v1"
        and receipt.get("passed") is True
        and digest_valid,
        "contract_sha256": receipt.get("contract_sha256", ""),
        "receipt_digest": claimed_digest if isinstance(claimed_digest, str) else "",
        "artifact_count": receipt.get("artifact_count", 0),
    }


def paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    return (
        left_resolved == right_resolved
        or left_resolved in right_resolved.parents
        or right_resolved in left_resolved.parents
    )


def execute_node(
    node: dict[str, Any],
    command: list[str],
    output: Path,
    input_identity: str,
    interactive: bool,
    order: int,
    console_lock: threading.Lock,
    declared_outputs: list[Path],
    declared_receipts: list[Path],
    receipt_verifier: str,
    environment: dict[str, str],
) -> dict[str, Any]:
    log_path = output / "logs" / f"{node['id']}.log"
    attempts = 0
    started = time.time_ns()
    result: subprocess.CompletedProcess[Any] | None = None
    outcome = "tool-error"
    with console_lock:
        print(f"==> {node['title']}", flush=True)
    while True:
        attempts += 1
        cleanup_error: OSError | GraphError | None = None
        if node.get("replace_outputs", False):
            try:
                for path in declared_outputs:
                    remove_owned_output(path, output)
            except (OSError, GraphError) as error:
                cleanup_error = error
        usage_log = output / "semantic-usage" / f"{node['id']}.jsonl"
        usage_log.parent.mkdir(parents=True, exist_ok=True)
        usage_log.write_text("", encoding="utf-8")
        node_environment = environment.copy()
        node_environment["P101_SEMANTIC_USAGE_LOG"] = os.fspath(usage_log)
        with log_path.open("a" if attempts > 1 else "w", encoding="utf-8") as log:
            if attempts > 1:
                log.write(f"\n# retry {attempts}\n")
            log.write("$ " + " ".join(command) + "\n\n")
            if cleanup_error is not None:
                log.write(
                    "declared output cleanup failed: "
                    f"{cleanup_error}\n"
                )
                result = subprocess.CompletedProcess(command, 2)
            else:
                timeout_seconds = int(node.get("timeout_seconds", 3600))
                try:
                    result = subprocess.run(
                        command,
                        cwd=SCRIPTS_ROOT,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                        env=node_environment,
                        timeout=timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    log.write(
                        "\ncheck timed out after "
                        f"{timeout_seconds} seconds\n"
                    )
                    result = subprocess.CompletedProcess(command, 2)
        if result.returncode == 0 and declared_receipts:
            with log_path.open("a", encoding="utf-8") as log:
                if not receipt_verifier:
                    log.write(
                        "\nreceipt verification refused: "
                        "p101-tool-receipt is not available\n"
                    )
                    result = subprocess.CompletedProcess(command, 2)
                else:
                    for receipt_path in declared_receipts:
                        verify_command = [
                            receipt_verifier,
                            "require-clean",
                            os.fspath(receipt_path),
                        ]
                        log.write("\n$ " + " ".join(verify_command) + "\n\n")
                        verified = subprocess.run(
                            verify_command,
                            cwd=SCRIPTS_ROOT,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            check=False,
                            env=node_environment,
                        )
                        if verified.returncode != 0:
                            result = subprocess.CompletedProcess(
                                command, verified.returncode
                            )
                            break
        if result.returncode == 0:
            outcome = "clean"
            with console_lock:
                print(f"    PASS {node['id']}", flush=True)
            break
        with console_lock:
            print(
                f"    FAIL {node['id']} (exit {result.returncode}; see {log_path})",
                flush=True,
            )
            print_failure_log(log_path)
        if not interactive:
            break
        prompt = "[r]etry"
        if not node.get("required", True):
            prompt += ", [s]kip"
        prompt += ", [q]uit: "
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            with console_lock:
                print(
                    "    interactive input is unavailable; stopping this "
                    "check without a traceback",
                    flush=True,
                )
            answer = "q"
        if answer in {"", "r", "retry"}:
            continue
        if answer in {"s", "skip"} and not node.get("required", True):
            outcome = "unsupported"
        break

    finished = time.time_ns()
    assert result is not None
    record = {
        "id": node["id"],
        "title": node["title"],
        "outcome": outcome,
        "required": node.get("required", True),
        "return_code": result.returncode,
        "attempts": attempts,
        "started_unix_ns": started,
        "finished_unix_ns": finished,
        "duration_ns": finished - started,
        "command": command,
        "guarantee": node["guarantee"],
        "log": f"logs/{node['id']}.log",
        "input_identity": input_identity,
        "evidence": {"source": "executed"},
        "verified_receipts": [
            normalize_for_identity(os.fspath(path), output)
            for path in declared_receipts
        ],
        "outputs": [
            {
                "destination": normalize_for_identity(os.fspath(path), output),
                "manifest": output_manifest(path),
            }
            for path in declared_outputs
        ],
        "order": order,
    }
    record["result"], record["log_bytes"], record["log_lines"] = log_result(
        log_path, f"exit {result.returncode}"
    )
    return record


def reused_record(
    node: dict[str, Any],
    command: list[str],
    input_identity: str,
    order: int,
    output: Path,
    variables: dict[str, str],
    source: str,
    cache_key: str = "",
) -> dict[str, Any]:
    log_path = output / "logs" / f"{node['id']}.log"
    record = {
        "id": node["id"],
        "title": node["title"],
        "outcome": "reused",
        "required": node.get("required", True),
        "return_code": 0,
        "attempts": 0,
        "started_unix_ns": time.time_ns(),
        "finished_unix_ns": time.time_ns(),
        "duration_ns": 0,
        "command": command,
        "guarantee": node["guarantee"],
        "log": f"logs/{node['id']}.log",
        "input_identity": input_identity,
        "evidence": {"source": source, "cache_key": cache_key},
        "outputs": declared_output_records(node, variables, output),
        "order": order,
    }
    record["result"], record["log_bytes"], record["log_lines"] = log_result(
        log_path, f"reused {source} evidence"
    )
    return record


def blocked_record(
    node: dict[str, Any],
    command: list[str],
    input_identity: str,
    order: int,
    output: Path,
    dependencies: list[str],
) -> dict[str, Any]:
    log_path = output / "logs" / f"{node['id']}.log"
    detail = "blocked by failed dependencies: " + ", ".join(dependencies)
    log_path.write_text(detail + "\n", encoding="utf-8")
    now = time.time_ns()
    return {
        "id": node["id"],
        "title": node["title"],
        "outcome": "blocked",
        "required": node.get("required", True),
        "return_code": 1,
        "attempts": 0,
        "started_unix_ns": now,
        "finished_unix_ns": now,
        "duration_ns": 0,
        "command": command,
        "guarantee": node["guarantee"],
        "log": f"logs/{node['id']}.log",
        "input_identity": input_identity,
        "evidence": {"source": "dependency", "dependencies": dependencies},
        "order": order,
        "result": detail,
        "log_bytes": log_path.stat().st_size,
        "log_lines": 1,
    }


def read_previous_receipt(path: Path | None) -> tuple[dict[str, Any], Path] | None:
    if path is None:
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GraphError(f"cannot read resume receipt {path}: {error}") from error
    if document.get("schema") != RUN_RECEIPT_SCHEMA:
        raise GraphError(f"{path}: unsupported resume receipt schema")
    claimed_digest = document.get("receipt_digest")
    unsigned = dict(document)
    unsigned.pop("receipt_digest", None)
    if (
        not isinstance(claimed_digest, str)
        or claimed_digest != canonical_sha256(unsigned)
    ):
        raise GraphError(f"{path}: resume receipt digest does not match its contents")
    records = document.get("records")
    if not isinstance(records, list):
        raise GraphError(f"{path}: resume receipt has no records")
    return document, path.parent.resolve()


def write_run_receipt(
    output: Path,
    document: dict[str, Any],
    variables: dict[str, str],
    records: list[dict[str, Any]],
    workspace: dict[str, Any],
    *,
    mode: str,
    elapsed_ns: int,
    jobs: int,
    cache_directory: Path | None,
) -> None:
    ordered_records = sorted(records, key=lambda item: item.get("order", 0))
    failed_record = next(
        (
            record
            for record in ordered_records
            if record["outcome"] in {"tool-error", "blocked"}
            and record.get("required", True)
        ),
        None,
    )
    failure = {"reason": "none", "stage": "", "first_diagnostic": ""}
    if failed_record is not None:
        failure = {
            "reason": failed_record["outcome"],
            "stage": failed_record["id"],
            "first_diagnostic": first_log_diagnostic(
                output / failed_record["log"], failed_record["result"]
            ),
        }
    receipt = {
        "schema": RUN_RECEIPT_SCHEMA,
        "tool": {"name": "p101-check-graph", "version": "2"},
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "input": {
            "schema": document["schema"],
            "identity": variables.get("graph_identity", str(DEFAULT_GRAPH.resolve())),
            "workspace": workspace,
        },
        "mode": mode,
        "jobs": jobs,
        "cache": {
            "enabled": cache_directory is not None and mode != "measurement",
            "directory": os.fspath(cache_directory) if cache_directory else "",
            "reused": sum(record["outcome"] == "reused" for record in ordered_records),
        },
        "outcome": "tool-error" if failed_record is not None else "clean",
        "failure": failure,
        "checks": {
            "attempted": len(ordered_records),
            "completed": sum(
                record["outcome"] in {"clean", "reused"} for record in ordered_records
            ),
        },
        "elapsed_ns": elapsed_ns,
        "workspace_lock": workspace_lock_identity(output),
        "stack_contract": stack_contract_identity(output),
        "records": ordered_records,
        "does_not_prove": document["does_not_prove"],
    }
    receipt["receipt_digest"] = canonical_sha256(receipt)
    (output / "receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


def run_graph(
    document: dict[str, Any],
    selected: list[dict[str, Any]],
    output: Path,
    variables: dict[str, str],
    interactive: bool,
    *,
    jobs: int | None = None,
    cache_directory: Path | None = None,
    use_cache: bool = True,
    measurement: bool = False,
    previous: tuple[dict[str, Any], Path] | None = None,
    all_nodes: list[dict[str, Any]] | None = None,
    required_previous: set[str] | None = None,
) -> int:
    run_started = time.time_ns()
    output = output.resolve()
    variables = dict(variables)
    variables["out"] = os.fspath(output)
    variables["temporary_root"] = tempfile.gettempdir()
    variables["receipt_verifier"] = os.environ.get(
        "P101_TOOL_RECEIPT", shutil.which("p101-tool-receipt") or ""
    )
    semantic_cache = (
        output / "semantic-facts-cold"
        if measurement
        else (
            cache_directory.resolve() / "semantic-facts"
            if cache_directory is not None
            else SCRIPTS_ROOT / "target" / "semantic-facts"
        )
    )
    semantic_cache.mkdir(parents=True, exist_ok=True)
    variables["semantic_cache"] = os.fspath(semantic_cache)
    execution_environment = os.environ.copy()
    execution_environment["P101_FACTS_CACHE"] = os.fspath(semantic_cache)
    execution_environment["P101_C_FACTS_CACHE_DIR"] = os.fspath(
        semantic_cache
    )
    execution_environment["P101_SEMANTIC_CACHE_ROOT"] = os.fspath(
        semantic_cache
    )
    log_directory = output / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    all_nodes = selected if all_nodes is None else all_nodes
    required_previous = set() if required_previous is None else required_previous
    order_by_id = {node["id"]: index for index, node in enumerate(all_nodes)}
    by_id = {node["id"]: node for node in all_nodes}
    identity_ids = {node["id"] for node in selected} | required_previous
    selected_ids = {node["id"] for node in selected}
    commands = {
        identifier: expand_command(by_id[identifier]["command"], variables)
        for identifier in identity_ids
    }
    workspace_identities: dict[tuple[str, ...] | None, dict[str, Any]] = {}
    source_indexes: dict[str, WorkspaceSourceIndex] = {}

    def identity_scope(node: dict[str, Any]) -> dict[str, Any]:
        patterns = node.get("inputs")
        key = (
            tuple(patterns)
            if node.get("inputs_complete") is True
            and isinstance(patterns, list)
            else None
        )
        if key not in workspace_identities:
            workspace_identities[key] = workspace_source_identity(
                key, source_indexes
            )
        return workspace_identities[key]

    identities: dict[str, str] = {}

    def effective_dependencies(node: dict[str, Any]) -> list[str]:
        dependencies = list(node.get("depends_on", []))
        if node.get("wait_for_selected", False):
            dependencies.extend(
                identifier
                for identifier in selected_ids
                if identifier != node["id"]
                and identifier not in dependencies
            )
        return dependencies

    def identity_for(identifier: str) -> str:
        if identifier not in identities:
            node = by_id[identifier]
            identity_dependencies = list(
                node.get("impact_dependencies", [])
            )
            if node.get("wait_for_selected", False):
                identity_dependencies.extend(
                    dependency
                    for dependency in selected_ids
                    if dependency != identifier
                    and dependency not in identity_dependencies
                )
            identities[identifier] = node_input_identity(
                node,
                commands[identifier],
                variables,
                output,
                identity_scope(node),
                {
                    dependency: identity_for(dependency)
                    for dependency in identity_dependencies
                    if dependency in identity_ids
                },
            )
        return identities[identifier]

    workspace = workspace_source_identity(None, source_indexes)
    content_manifest = output / "workspace-content-manifest.json"
    source_index = source_indexes.get("workspace")
    if source_index is not None:
        source_index.write_manifest(content_manifest)
        execution_environment["P101_CONTENT_MANIFEST"] = os.fspath(
            content_manifest
        )
    previous_records: dict[str, dict[str, Any]] = {}
    previous_directory: Path | None = None
    if previous is not None:
        previous_document, previous_directory = previous
        previous_records = {
            record["id"]: record
            for record in previous_document["records"]
            if isinstance(record, dict) and isinstance(record.get("id"), str)
        }
    if required_previous:
        if previous_directory != output:
            raise GraphError("--from requires a resume receipt in the selected output directory")
        for identifier in sorted(required_previous):
            record = previous_records.get(identifier)
            if (
                record is None
                or record.get("outcome") not in {"clean", "reused"}
                or record.get("input_identity") != identity_for(identifier)
                or not declared_outputs_match(
                    record, by_id[identifier], variables, output
                )
            ):
                raise GraphError(
                    f"--from prerequisite {identifier!r} has no current clean receipt"
                )

    run_jobs = jobs if jobs is not None else document.get("default_jobs", 1)
    if run_jobs <= 0:
        raise GraphError("jobs must be positive")
    if interactive or measurement:
        run_jobs = 1
    active_cache = (
        cache_directory.resolve()
        if cache_directory is not None and use_cache and not measurement
        else None
    )
    if active_cache is not None:
        active_cache.mkdir(parents=True, exist_ok=True)

    statuses: dict[str, str] = {}
    for identifier in required_previous:
        statuses[identifier] = "reused"
        records.append(
            reused_record(
                by_id[identifier],
                commands[identifier],
                identity_for(identifier),
                order_by_id[identifier],
                output,
                variables,
                "resume",
            )
        )

    selected_by_id = {node["id"]: node for node in selected}
    for node in selected:
        for dependency in node.get("depends_on", []):
            if dependency not in selected_by_id and dependency not in required_previous:
                statuses[dependency] = "skipped"
    pending = dict(selected_by_id)
    running: dict[
        concurrent.futures.Future[dict[str, Any]],
        tuple[dict[str, Any], dict[str, int], list[Path], str],
    ] = {}
    capacities = document.get("resource_capacities", {})
    used_units = {name: 0 for name in capacities}
    active_writes: dict[str, list[Path]] = {}
    console_lock = threading.Lock()
    def node_units(node: dict[str, Any]) -> dict[str, int]:
        return node.get("resources", {}).get("units", {})

    def node_writes(node: dict[str, Any]) -> list[Path]:
        paths = []
        for raw in node.get("resources", {}).get("writes", []):
            if raw == "{temporary_root}":
                continue
            value = raw.format_map(variables)
            if not value:
                continue
            path = Path(value)
            paths.append(
                path.resolve(strict=False)
                if path.is_absolute()
                else (SCRIPTS_ROOT / path).resolve(strict=False)
            )
        return paths

    def resources_available(node: dict[str, Any], writes: list[Path]) -> bool:
        for resource, amount in node_units(node).items():
            if used_units.get(resource, 0) + amount > capacities[resource]:
                return False
        return not any(
            paths_overlap(candidate, active)
            for candidate in writes
            for active_paths in active_writes.values()
            for active in active_paths
        )

    def reserve(node: dict[str, Any], writes: list[Path]) -> None:
        for resource, amount in node_units(node).items():
            used_units[resource] += amount
        active_writes[node["id"]] = writes

    def release(node: dict[str, Any]) -> None:
        for resource, amount in node_units(node).items():
            used_units[resource] -= amount
        active_writes.pop(node["id"], None)

    def persist() -> None:
        elapsed = time.time_ns() - run_started
        write_summary(output, records, document)
        write_profile(
            output,
            records,
            mode="measurement" if measurement else "functional",
            elapsed_ns=elapsed,
        )
        write_run_receipt(
            output,
            document,
            variables,
            records,
            workspace,
            mode="measurement" if measurement else "functional",
            elapsed_ns=elapsed,
            jobs=run_jobs,
            cache_directory=active_cache,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=run_jobs) as executor:
        while pending or running:
            progress = False
            for identifier, node in list(pending.items()):
                dependencies = effective_dependencies(node)
                if any(dependency not in statuses for dependency in dependencies):
                    continue
                failed_dependencies = [
                    dependency
                    for dependency in dependencies
                    if statuses[dependency] not in {"clean", "reused", "skipped"}
                ]
                if failed_dependencies:
                    record = blocked_record(
                        node,
                        commands[identifier],
                        identity_for(identifier),
                        order_by_id[identifier],
                        output,
                        failed_dependencies,
                    )
                    records.append(record)
                    statuses[identifier] = "blocked"
                    del pending[identifier]
                    persist()
                    progress = True
                    continue

                previous_record = previous_records.get(identifier)
                if (
                    previous_directory == output
                    and previous_record is not None
                    and previous_record.get("outcome") in {"clean", "reused"}
                    and previous_record.get("input_identity")
                    == identity_for(identifier)
                    and declared_outputs_match(
                        previous_record, node, variables, output
                    )
                    and semantic_usage_available(
                        output / "semantic-usage" / f"{identifier}.jsonl",
                        semantic_cache,
                    )
                ):
                    record = reused_record(
                        node,
                        commands[identifier],
                        identity_for(identifier),
                        order_by_id[identifier],
                        output,
                        variables,
                        "resume",
                    )
                    records.append(record)
                    statuses[identifier] = "reused"
                    del pending[identifier]
                    with console_lock:
                        print(f"    REUSE {identifier} (resume)", flush=True)
                    persist()
                    progress = True
                    continue

                cacheable = node.get("cacheable", True)
                log_path = log_directory / f"{identifier}.log"
                if (
                    active_cache is not None
                    and cacheable
                    and restore_cache_entry(
                        active_cache,
                        identity_for(identifier),
                        node,
                        variables,
                        output,
                        log_path,
                        semantic_cache,
                    )
                    is not None
                ):
                    record = reused_record(
                        node,
                        commands[identifier],
                        identity_for(identifier),
                        order_by_id[identifier],
                        output,
                        variables,
                        "cache",
                        identity_for(identifier),
                    )
                    records.append(record)
                    statuses[identifier] = "reused"
                    del pending[identifier]
                    with console_lock:
                        print(f"    REUSE {identifier} (exact cache)", flush=True)
                    persist()
                    progress = True
                    continue

                writes = node_writes(node)
                if len(running) >= run_jobs or not resources_available(node, writes):
                    continue
                reserve(node, writes)
                input_identity = identity_for(identifier)
                future = executor.submit(
                    execute_node,
                    node,
                    commands[identifier],
                    output,
                    input_identity,
                    interactive,
                    order_by_id[identifier],
                    console_lock,
                    expanded_writes(node, variables, output),
                    expanded_receipts(node, variables, output),
                    variables["receipt_verifier"],
                    execution_environment,
                )
                running[future] = (
                    node,
                    node_units(node),
                    writes,
                    input_identity,
                )
                del pending[identifier]
                progress = True

            if running:
                done, _ = concurrent.futures.wait(
                    running,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    node, _, _, input_identity = running.pop(future)
                    release(node)
                    record = future.result()
                    records.append(record)
                    statuses[node["id"]] = record["outcome"]
                    if (
                        record["outcome"] == "clean"
                        and node.get("invalidates_source_identity", False)
                    ):
                        workspace_identities.clear()
                        source_indexes.clear()
                        identities.clear()
                        workspace = workspace_source_identity(
                            None, source_indexes
                        )
                    if (
                        record["outcome"] == "clean"
                        and active_cache is not None
                        and node.get("cacheable", True)
                    ):
                        publish_cache_entry(
                            active_cache,
                            input_identity,
                            node,
                            variables,
                            output,
                            output / record["log"],
                        )
                    persist()
                progress = True

            if not progress and pending:
                raise GraphError(
                    "scheduler cannot make progress; check dependencies and resource declarations"
                )

    persist()
    return int(
        any(
            record["outcome"] in {"tool-error", "blocked"}
            and record.get("required", True)
            for record in records
        )
    )


def parse_variables(values: list[str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise GraphError(f"--var must be KEY=VALUE: {value}")
        variables[key] = item
    return variables


def omitted_required_dependencies(
    selected: list[dict[str, Any]],
    base_selected: list[dict[str, Any]],
) -> set[str]:
    base_by_id = {node["id"]: node for node in base_selected}
    selected_ids = {node["id"] for node in selected}
    required: set[str] = set()
    pending = [
        dependency
        for node in selected
        for dependency in node.get("depends_on", [])
        if dependency in base_by_id and dependency not in selected_ids
    ]
    while pending:
        identifier = pending.pop()
        if identifier in required or identifier in selected_ids:
            continue
        required.add(identifier)
        pending.extend(
            dependency
            for dependency in base_by_id[identifier].get("depends_on", [])
            if dependency in base_by_id
        )
    return required


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("check")
    subparsers.add_parser("list")
    merge_parser = subparsers.add_parser("merge-platform-evidence")
    merge_parser.add_argument(
        "--kind", choices=("instrumentation", "quality"), required=True
    )
    merge_parser.add_argument("--receipt", action="append", type=Path, required=True)
    merge_parser.add_argument("--require-platform", action="append", default=[])
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--var", action="append", default=[])
    run_parser.add_argument("--skip-group", action="append", default=[])
    run_parser.add_argument("--only", action="append", default=[])
    run_parser.add_argument("--from", dest="start")
    run_parser.add_argument("--interactive", action="store_true")
    run_parser.add_argument("--jobs", type=int)
    run_parser.add_argument("--cache-dir", type=Path)
    run_parser.add_argument("--no-cache", action="store_true")
    run_parser.add_argument("--resume", action="store_true")
    run_parser.add_argument("--resume-receipt", type=Path)
    run_parser.add_argument("--measure", action="store_true")
    run_parser.add_argument(
        "--changed",
        action="append",
        default=[],
        help="run the conservative impact closure for a workspace-relative path",
    )
    run_parser.add_argument(
        "--affected",
        action="store_true",
        help=(
            "discover committed-ahead, staged, unstaged, and untracked "
            "workspace paths and run their conservative impact closure"
        ),
    )
    arguments = parser.parse_args()

    if arguments.operation == "merge-platform-evidence":
        if arguments.kind == "instrumentation":
            required_platforms = set(arguments.require_platform) or {
                "Darwin",
                "FreeBSD",
                "Linux",
            }
            merge_instrumentation_receipts(arguments.receipt, required_platforms)
        else:
            required_platforms = set(arguments.require_platform) or {
                "freebsd",
                "linux",
                "macos",
            }
            merge_quality_receipts(arguments.receipt, required_platforms)
        return 0

    document = json.loads(arguments.graph.read_text(encoding="utf-8"))
    ordered = validate(document)
    if arguments.operation == "check":
        print(f"p101 check graph: {len(ordered)} governed nodes")
        return 0
    if arguments.operation == "list":
        for node in ordered:
            print(f"{node['id']}\t{node['group']}\t{node['title']}")
        return 0

    if arguments.start is not None and not (
        arguments.resume or arguments.resume_receipt is not None
    ):
        raise GraphError("--from requires --resume or --resume-receipt")
    if arguments.measure and (
        arguments.resume or arguments.resume_receipt is not None
    ):
        raise GraphError("--measure requires fresh execution and cannot resume")

    variables = parse_variables(arguments.var)
    if (arguments.changed or arguments.affected) and (
        arguments.only or arguments.start
    ):
        raise GraphError(
            "--changed/--affected cannot be combined with --only or --from"
        )
    if arguments.changed and arguments.affected:
        raise GraphError("--changed and --affected are mutually exclusive")
    discovered_changes = (
        workspace_changed_paths(
            SCRIPTS_ROOT.parent.resolve(), active_repository_roots()
        )
        if arguments.affected
        else set(arguments.changed)
    )
    if arguments.affected:
        if not discovered_changes:
            print("p101 affected checks: no local workspace changes")
            return 0
        print(
            f"p101 affected checks: {len(discovered_changes)} changed path(s)",
            flush=True,
        )
        for path in sorted(discovered_changes):
            print(f"  {path}", flush=True)
    impacted = (
        impact_closure(
            discovered_changes,
            ordered,
            propagate_dependencies=not document.get(
                "require_complete_inputs", False
            ),
        )
        if discovered_changes
        else set()
    )
    requested = set(arguments.only) | impacted
    if discovered_changes and document.get("require_complete_inputs", False):
        by_id = {node["id"]: node for node in ordered}
        selected_ids = impact_dependency_closure(requested, by_id)
        base_selected = [
            node
            for node in ordered
            if node["id"] in selected_ids
            and node["group"] not in set(arguments.skip_group)
        ]
        selected = base_selected
    else:
        base_selected = select_nodes(
            ordered, requested, set(arguments.skip_group), None
        )
        selected = select_nodes(
            ordered, requested, set(arguments.skip_group), arguments.start
        )
    if not selected:
        raise GraphError("selection contains no check nodes")
    output = arguments.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prune_obsolete_usage_logs(output, ordered)
    variables.setdefault("out", str(output))
    variables.setdefault(
        "graph_identity", "sha256:" + file_sha256(arguments.graph.resolve())
    )
    resume_path = arguments.resume_receipt
    if arguments.resume and resume_path is None:
        resume_path = output / "receipt.json"
    previous = read_previous_receipt(resume_path.resolve() if resume_path else None)
    cache_directory = (
        arguments.cache_dir.resolve()
        if arguments.cache_dir is not None
        else SCRIPTS_ROOT / "target" / "check-evidence-cache"
    )
    return run_graph(
        document,
        selected,
        output,
        variables,
        arguments.interactive,
        jobs=arguments.jobs,
        cache_directory=cache_directory,
        use_cache=not arguments.no_cache,
        measurement=arguments.measure,
        previous=previous,
        all_nodes=ordered,
        required_previous=omitted_required_dependencies(selected, base_selected),
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GraphError, json.JSONDecodeError, OSError) as error:
        print(f"p101-check-graph: {error}", file=sys.stderr)
        raise SystemExit(2) from error
