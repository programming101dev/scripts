"""Acquire and decode the native P101FACT stream for policy checkers.

This module owns transport only.  Callers decide policy from resolved
declaration identities, source extents, and typed fact kinds.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from semantic_usage import record_usage


class CFactError(RuntimeError):
    """The semantic fact producer or its versioned output was invalid."""


SOURCE_DIRECTORY_NAMES = ("src", "include", "test", "fuzz")
SOURCE_SUFFIXES = {".c", ".h"}


def _libclang_include_roots() -> set[Path]:
    """Discover public libclang headers from the selected LLVM toolchain."""
    candidates: set[Path] = set()
    configured = os.environ.get("P101_LIBCLANG_INCLUDE_DIR", "")
    if configured:
        candidates.add(Path(configured))
    for name in ("llvm-config",):
        executable = shutil.which(name)
        if executable is None:
            continue
        result = subprocess.run(
            [executable, "--includedir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidates.add(Path(result.stdout.strip()))
    return {
        path.resolve()
        for path in candidates
        if (path / "clang-c" / "Index.h").is_file()
    }


def _unescape(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            escaped = value[index + 1]
            output.append(
                {"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}.get(
                    escaped, escaped
                )
            )
            index += 2
        else:
            output.append(value[index])
            index += 1
    return "".join(output)


def _decode(line: str, number: int) -> dict[str, object] | None:
    if not line.startswith("P101FACT\t"):
        return None
    fields = [_unescape(value) for value in line.split("\t")]
    if len(fields) < 7 or fields[1] != "7":
        raise CFactError(f"malformed P101FACT v7 record at output line {number}")
    base: dict[str, object] = {
        "kind": fields[2],
        "path": fields[3],
        "module": fields[4],
        "is_header": fields[5] == "1",
        "line": int(fields[6]),
    }
    if fields[2] == "FILE" and len(fields) == 7:
        pass
    elif fields[2] == "FUNCTION" and len(fields) == 13:
        base.update(
            value=fields[7],
            is_static=fields[8] == "1",
            is_declaration=fields[9] == "1",
            usr=fields[10],
            start=int(fields[11]),
            end=int(fields[12]),
        )
    elif fields[2] == "CALL" and len(fields) == 16:
        base.update(
            value=fields[7],
            has_env_parameter=fields[8] == "1",
            has_error_parameter=fields[9] == "1",
            is_indirect=fields[10] == "1",
            caller=fields[11],
            usr=fields[12],
            caller_usr=fields[13],
            start=int(fields[14]),
            end=int(fields[15]),
        )
    elif fields[2] == "INCLUDE" and len(fields) == 10:
        base.update(
            value=fields[7],
            is_local=fields[8] == "1",
            resolved=fields[9],
        )
    elif fields[2] in {"TYPE", "ENUM"} and len(fields) == 9:
        base.update(value=fields[7], usr=fields[8])
    elif fields[2] == "ENUMERATOR" and len(fields) == 11:
        base.update(
            value=fields[7],
            type=fields[8],
            usr=fields[9],
            parent_usr=fields[10],
        )
    elif fields[2] == "NOTE" and len(fields) == 13:
        base.update(
            value=fields[7],
            caller=fields[8],
            column=int(fields[9]),
            caller_usr=fields[10],
            start=int(fields[11]),
            end=int(fields[12]),
        )
    elif fields[2] == "MACRO" and len(fields) == 12:
        base.update(
            value=fields[7],
            is_definition=fields[8] == "1",
            caller_usr=fields[9],
            start=int(fields[10]),
            end=int(fields[11]),
        )
    else:
        raise CFactError(
            f"malformed {fields[2]} P101FACT v7 record at output line {number}"
        )
    return base


def decode_lines(lines: Iterable[str]) -> list[dict[str, object]]:
    """Decode a complete P101FACT stream, ignoring non-fact diagnostics."""
    facts: list[dict[str, object]] = []
    for number, line in enumerate(lines, 1):
        fact = _decode(line, number)
        if fact is not None:
            facts.append(fact)
    return facts


def _repository_root(workspace: Path, path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    workspace = workspace.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
        if candidate == workspace:
            break
    return None


def _repository_scan_paths(repository: Path) -> tuple[Path, ...]:
    paths = [
        repository / name
        for name in SOURCE_DIRECTORY_NAMES
        if (repository / name).is_dir()
    ]
    paths.extend(
        path
        for path in repository.iterdir()
        if path.is_file() and path.suffix in SOURCE_SUFFIXES
    )
    return tuple(sorted(paths)) or (repository,)


def _analysis_units(
    workspace: Path, admitted_paths: Iterable[Path]
) -> tuple[tuple[Path | None, tuple[Path, ...]], ...]:
    """Partition an aggregate source scope into repository-local parses.

    Local headers such as ``cli.h`` are meaningful only within their owning
    repository. A single parse of every program with every program include
    root would silently bind duplicate header names to whichever ``-I`` path
    sorted first.
    """
    workspace = workspace.resolve()
    grouped: dict[Path | None, set[Path]] = defaultdict(set)
    for admitted_path in admitted_paths:
        admitted_path = admitted_path.resolve()
        repository = _repository_root(workspace, admitted_path)
        if repository is not None:
            if admitted_path == repository:
                grouped[repository].update(
                    _repository_scan_paths(repository)
                )
            else:
                grouped[repository].add(admitted_path)
            continue
        child_repositories = (
            [
                child
                for child in admitted_path.iterdir()
                if child.is_dir() and (child / ".git").exists()
            ]
            if admitted_path.is_dir()
            else []
        )
        if child_repositories:
            for child in child_repositories:
                grouped[child.resolve()].update(_repository_scan_paths(child))
        else:
            grouped[None].add(admitted_path)
    return tuple(
        (repository, tuple(sorted(paths)))
        for repository, paths in sorted(
            grouped.items(),
            key=lambda item: "" if item[0] is None else str(item[0]),
        )
    )


def _compile_database_include_roots(repository: Path) -> set[Path]:
    """Read include search roots without executing compilation commands."""
    database = repository / "compile_commands.json"
    if not database.is_file():
        return set()
    try:
        records = json.loads(database.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CFactError(
            f"cannot read compilation database include roots: {database}: "
            f"{error}"
        ) from error
    if not isinstance(records, list):
        raise CFactError(f"compilation database is not an array: {database}")

    roots: set[Path] = set()
    separate_flags = {"-I", "-isystem", "-iquote", "-idirafter"}
    attached_flags = ("-isystem", "-iquote", "-idirafter", "-I")
    for record in records:
        if not isinstance(record, dict):
            raise CFactError(f"compilation database has a non-object row: {database}")
        directory_value = record.get("directory")
        if not isinstance(directory_value, str):
            raise CFactError(
                f"compilation database row has no directory: {database}"
            )
        directory = Path(directory_value)
        raw_arguments = record.get("arguments")
        if isinstance(raw_arguments, list) and all(
            isinstance(argument, str) for argument in raw_arguments
        ):
            arguments = list(raw_arguments)
        else:
            command = record.get("command")
            if not isinstance(command, str):
                raise CFactError(
                    f"compilation database row has no command arguments: "
                    f"{database}"
                )
            try:
                arguments = shlex.split(command)
            except ValueError as error:
                raise CFactError(
                    f"cannot decode compilation database command: {database}: "
                    f"{error}"
                ) from error

        index = 0
        while index < len(arguments):
            argument = arguments[index]
            include_value: str | None = None
            if argument in separate_flags:
                index += 1
                if index >= len(arguments):
                    raise CFactError(
                        f"compilation database has an incomplete "
                        f"{argument} option: {database}"
                    )
                include_value = arguments[index]
            else:
                for prefix in attached_flags:
                    if argument.startswith(prefix) and argument != prefix:
                        include_value = argument[len(prefix) :].removeprefix("=")
                        break
            if include_value:
                include_path = Path(include_value)
                if not include_path.is_absolute():
                    include_path = directory / include_path
                include_path = include_path.resolve()
                if include_path.is_dir():
                    roots.add(include_path)
            index += 1
    return roots



SNAPSHOT_SCHEMA = "p101-facts-snapshot-v1"
_SNAPSHOT_SKIP_DIRECTORIES = {".git", "__pycache__", ".pytest_cache", ".facts-cache", "_to_delete"}


def _resolve_snapshot_cache(cache: Path | str | None) -> Path | None:
    """Resolve the snapshot cache directory.

    "auto" honours P101_FACTS_CACHE: unset selects scripts/.facts-cache,
    "off" (or "0") disables restoration, any other value names the directory.
    """
    if cache is None:
        return None
    if isinstance(cache, Path):
        return cache
    configured = os.environ.get("P101_FACTS_CACHE", "")
    if configured.lower() in {"0", "off", "disabled"}:
        return None
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / ".facts-cache"


def _hash_file_into(digest: "hashlib._Hash", path: Path) -> None:
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)


def _tree_digest(root: Path, memo: dict[Path, str]) -> str:
    cached = memo.get(root)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    if root.is_file():
        _hash_file_into(digest, root)
    else:
        entries = []
        for current, directories, files in os.walk(root):
            directories[:] = sorted(
                name
                for name in directories
                if name not in _SNAPSHOT_SKIP_DIRECTORIES
                and not name.startswith("build")
            )
            for name in files:
                entries.append(Path(current) / name)
        for path in sorted(entries):
            digest.update(str(path.relative_to(root)).encode())
            _hash_file_into(digest, path)
    memo[root] = value = digest.hexdigest()
    return value


def _producer_identity(producer: Path) -> bytes:
    digest = hashlib.sha256()
    digest.update(SNAPSHOT_SCHEMA.encode())
    digest.update(platform.system().encode())
    producer_repository = producer.parent
    producer_paths = [producer]
    for marker_name in (".last-build-dir", ".last-runtime-build-dir"):
        marker = producer_repository / marker_name
        if marker.is_file():
            producer_paths.append(marker)
            build_name = marker.read_text(encoding="utf-8").strip()
            candidate = producer_repository / build_name / "p101-c-facts"
            if build_name and candidate.is_file():
                producer_paths.append(candidate)
    for path in producer_paths:
        digest.update(str(path).encode())
        _hash_file_into(digest, path)
    return digest.digest()


def _unit_snapshot_key(
    workspace_root: Path,
    producer_identity: bytes,
    command: list[str],
    memo: dict[Path, str],
) -> str:
    """Content-address one analysis unit: producer, argv, and the trees it names.

    Keys are per unit so an edit invalidates only the repository it touches;
    the other units restore. Workspace paths named by any argv token — unit
    paths, -I roots, the compile database — contribute their file contents.
    Toolchain roots outside the workspace contribute their path and
    libclang's Index.h, which changes when the toolchain does. Shared -I
    roots appear in every unit's argv, so a header edit still invalidates
    every unit that can see it.
    """
    digest = hashlib.sha256()
    digest.update(producer_identity)
    for token in command[1:]:
        digest.update(token.encode())
        text = token[len("--cflag=-I"):] if token.startswith("--cflag=-I") else token
        if not text.startswith("/"):
            continue
        path = Path(text)
        if not path.exists():
            continue
        try:
            path.relative_to(workspace_root)
        except ValueError:
            index = path / "clang-c" / "Index.h"
            if index.is_file():
                digest.update(_tree_digest(index, memo).encode())
            continue
        digest.update(_tree_digest(path, memo).encode())
    return digest.hexdigest()


def _snapshot_restore(cache_directory: Path, key: str) -> list[dict[str, object]] | None:
    path = cache_directory / f"{key}.jsonl.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(stream.readline())
            if header.get("schema") != SNAPSHOT_SCHEMA or header.get("key") != key:
                return None
            facts = [json.loads(line) for line in stream]
    except (OSError, ValueError):
        return None
    if len(facts) != header.get("fact_count"):
        return None
    return facts


def _snapshot_store(cache_directory: Path, key: str, facts: list[dict[str, object]]) -> None:
    try:
        cache_directory.mkdir(parents=True, exist_ok=True)
        temporary = cache_directory / f".{key}.{os.getpid()}.tmp"
        with gzip.open(temporary, "wt", encoding="utf-8") as stream:
            stream.write(json.dumps({"schema": SNAPSHOT_SCHEMA, "key": key, "fact_count": len(facts)}) + "\n")
            for fact in facts:
                stream.write(json.dumps(fact) + "\n")
        os.replace(temporary, cache_directory / f"{key}.jsonl.gz")
    except OSError:
        return


def acquire(
    workspace: Path,
    paths: Iterable[Path],
    *,
    compile_database: Path | None = None,
    additional_include_roots: Iterable[Path] = (),
    cache: Path | str | None = "auto",
) -> list[dict[str, object]]:
    producer = workspace / "programs" / "p101-wrapper-audit" / "p101-c-facts"
    if not producer.is_file():
        raise CFactError(f"semantic fact producer is absent: {producer}")
    admitted_paths = [path.resolve() for path in paths]
    shared_include_roots = {
        path.resolve()
        for path in (workspace / "libraries").glob("lib_*/include")
        if path.is_dir()
    }
    shared_include_roots.update(_libclang_include_roots())
    shared_include_roots.update(
        path.resolve()
        for path in additional_include_roots
        if path.is_dir()
    )
    workspace_root = workspace.resolve()
    units = (
        ((None, tuple(admitted_paths)),)
        if compile_database is not None
        else _analysis_units(workspace_root, admitted_paths)
    )
    facts: list[dict[str, object]] = []
    commands: list[tuple[list[str], Path | None]] = []
    for repository, unit_paths in units:
        include_roots = set(shared_include_roots)
        if repository is not None:
            include_roots.update(
                _compile_database_include_roots(repository)
            )
            local_include = repository / "include"
            unity = repository / "test" / "unity"
            if local_include.is_dir():
                include_roots.add(local_include.resolve())
            if unity.is_dir():
                include_roots.add(unity.resolve())
        else:
            for admitted_path in unit_paths:
                for parent in admitted_path.parents:
                    unity = parent / "test" / "unity"
                    if unity.is_dir():
                        include_roots.add(unity.resolve())
                    if parent == workspace_root:
                        break
        command = [str(producer)]
        system = platform.system()
        if system == "Darwin":
            command.append("--cflag=-D_DARWIN_C_SOURCE")
        elif system == "Linux":
            command.append("--cflag=-D_GNU_SOURCE")
        elif system == "FreeBSD":
            command.extend(
                ("--cflag=-D_BSD_SOURCE", "--cflag=-D__BSD_VISIBLE")
            )
        command.extend(f"--cflag=-I{path}" for path in sorted(include_roots))
        if compile_database is not None:
            command.extend(("--compile-db", str(compile_database.resolve())))
        command.extend(str(path) for path in unit_paths)
        commands.append((command, repository))

    cache_directory = _resolve_snapshot_cache(cache)
    unit_keys: list[str | None] = [None] * len(commands)
    unit_facts: list[list[dict[str, object]] | None] = [None] * len(commands)
    if cache_directory is not None:
        try:
            producer_identity = _producer_identity(producer)
        except OSError:
            producer_identity = None
        if producer_identity is not None:
            memo: dict[Path, str] = {}
            for index, (command, _repository) in enumerate(commands):
                try:
                    key = _unit_snapshot_key(
                        workspace_root, producer_identity, command, memo
                    )
                except OSError:
                    continue
                unit_keys[index] = key
                unit_facts[index] = _snapshot_restore(cache_directory, key)

    pending = [index for index in range(len(commands)) if unit_facts[index] is None]
    if cache_directory is not None:
        restored_count = len(commands) - len(pending)
        if restored_count or pending:
            print(
                f"p101 facts snapshot: restored {restored_count} of "
                f"{len(commands)} units, parsing {len(pending)}",
                file=sys.stderr,
            )

    def run_unit(
        command: list[str],
    ) -> tuple[subprocess.CompletedProcess[str], float]:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise CFactError(
                f"cannot acquire semantic C facts: {error}"
            ) from error
        return completed, time.monotonic() - started

    # Units are independent producer invocations over disjoint trees; run
    # the ones without a snapshot across cores and keep the facts in unit
    # order so callers see the exact stream the sequential loop produced.
    if pending:
        workers = max(1, min(len(pending), os.cpu_count() or 1))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(run_unit, [commands[index][0] for index in pending])
            )
        durations: list[tuple[float, int]] = []
        for index, (result, seconds) in zip(pending, results):
            durations.append((seconds, index))
            repository = commands[index][1]
            if result.returncode != 0:
                context = (
                    str(repository.relative_to(workspace_root))
                    if repository is not None
                    else "shared scope"
                )
                raise CFactError(
                    f"semantic C-fact acquisition failed for {context}: "
                    + (result.stderr.strip() or f"exit {result.returncode}")
                )
            decoded = decode_lines(result.stdout.splitlines())
            unit_facts[index] = decoded
            key = unit_keys[index]
            if cache_directory is not None and key is not None:
                _snapshot_store(cache_directory, key, decoded)
        for seconds, index in sorted(durations, reverse=True)[:5]:
            repository = commands[index][1]
            name = (
                str(repository.relative_to(workspace_root))
                if repository is not None
                else "shared scope"
            )
            scanned = " ".join(
                Path(argument).name
                for argument in commands[index][0][1:]
                if argument.startswith("/")
            )
            print(
                f"p101 facts unit: {seconds:5.1f}s {name} [{scanned}]",
                file=sys.stderr,
            )
    for unit in unit_facts:
        facts.extend(unit or [])
    if cache_directory is not None:
        for key in unit_keys:
            if key is not None:
                try:
                    record_usage(cache_directory, "runtime-facts", key)
                except OSError as error:
                    raise CFactError(
                        f"cannot record semantic fact usage: {error}"
                    ) from error
    return facts
