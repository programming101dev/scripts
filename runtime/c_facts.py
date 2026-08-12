"""Acquire and decode the native P101FACT stream for policy checkers.

This module owns transport only.  Callers decide policy from resolved
declaration identities, source extents, and typed fact kinds.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import fcntl
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
from content_manifest import manifest_digest
from content_manifest import hash_tree as manifest_hash_tree


class CFactError(RuntimeError):
    """The semantic fact producer or its versioned output was invalid."""


SOURCE_DIRECTORY_NAMES = ("src", "include", "test", "fuzz")
SOURCE_SUFFIXES = {".c", ".h"}
MAXIMUM_DEFAULT_FACT_WORKERS = 4


def _fact_worker_count(pending_count: int) -> int:
    """Return a bounded parser count for process-heavy libclang producers.

    Each worker launches a separate audit-facts process. Using every logical
    CPU oversubscribes libclang and the filesystem, so the measured default is
    deliberately conservative. P101_FACTS_JOBS remains available for
    controlled profiling and unusually capable hosts.
    """
    configured = os.environ.get("P101_FACTS_JOBS", "")
    if configured:
        if not configured.isdecimal() or int(configured) == 0:
            raise CFactError(
                "P101_FACTS_JOBS must be a positive decimal integer"
            )
        requested = int(configured)
    else:
        requested = min(MAXIMUM_DEFAULT_FACT_WORKERS, os.cpu_count() or 1)
    return max(1, min(pending_count, requested))


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
    if len(fields) < 7 or fields[1] != "8":
        raise CFactError(f"malformed P101FACT v8 record at output line {number}")
    base: dict[str, object] = {
        "kind": fields[2],
        "path": fields[3],
        "module": fields[4],
        "is_header": fields[5] == "1",
        "line": int(fields[6]),
    }
    if fields[2] == "FILE" and len(fields) == 7:
        pass
    elif fields[2] == "FUNCTION" and len(fields) == 16:
        base.update(
            value=fields[7],
            is_static=fields[8] == "1",
            is_declaration=fields[9] == "1",
            usr=fields[10],
            start=int(fields[11]),
            end=int(fields[12]),
            type=fields[13],
            return_type=fields[14],
            is_variadic=fields[15] == "1",
        )
    elif fields[2] == "PARAMETER" and len(fields) == 14:
        base.update(
            value=fields[7],
            type=fields[8],
            canonical_type=fields[9],
            caller_usr=fields[10],
            parameter_index=int(fields[11]),
            start=int(fields[12]),
            end=int(fields[13]),
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
            f"malformed {fields[2]} P101FACT v8 record at output line {number}"
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
    components = repository / "components"
    if components.is_dir():
        paths.extend(
            path
            for component in components.iterdir()
            if component.is_dir()
            for name in SOURCE_DIRECTORY_NAMES
            for path in (component / name,)
            if path.is_dir()
        )
    return tuple(sorted(paths)) or (repository,)


def _analysis_scope(repository: Path, path: Path) -> Path:
    """Keep consolidated components in independent include-name scopes."""
    components = repository / "components"
    try:
        relative = path.resolve().relative_to(components.resolve())
    except ValueError:
        return repository
    if not relative.parts:
        return repository
    return components / relative.parts[0]


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _canonical_scan_paths(
    repository: Path, admitted_paths: tuple[Path, ...]
) -> tuple[Path, ...] | None:
    """Return a stable, scope-equivalent repository scan.

    Production policy must not inherit the very large generated test corpus.
    Test and fuzz callers similarly receive stable keys for their own trees;
    only a deliberately mixed request uses the complete repository scope.
    """
    all_scan_paths = tuple(
        path
        for path in _repository_scan_paths(repository)
        if _analysis_scope(repository, path) == repository
    )
    if all(
        any(_path_is_within(path, root) for root in all_scan_paths)
        for path in admitted_paths
    ):
        categories: set[str] = set()
        for path in admitted_paths:
            matching = next(
                root
                for root in all_scan_paths
                if _path_is_within(path, root)
            )
            categories.add(
                matching.name
                if matching.is_dir()
                else "production"
            )
        if categories <= {"src", "include", "production"}:
            selected = tuple(
                path
                for path in all_scan_paths
                if not path.is_dir() or path.name in {"src", "include"}
            )
        elif categories == {"test"}:
            selected = tuple(
                path
                for path in all_scan_paths
                if path.is_dir() and path.name == "test"
            )
        elif categories == {"fuzz"}:
            selected = tuple(
                path
                for path in all_scan_paths
                if path.is_dir() and path.name == "fuzz"
            )
        else:
            selected = all_scan_paths
        return selected
    return None


def _facts_for_admitted_paths(
    facts: list[dict[str, object]], admitted_paths: tuple[Path, ...]
) -> list[dict[str, object]]:
    """Project canonical repository facts back onto one caller's scope."""
    admitted_files = {
        path.resolve() for path in admitted_paths if not path.is_dir()
    }
    admitted_directories = tuple(
        path.resolve() for path in admitted_paths if path.is_dir()
    )
    selected: list[dict[str, object]] = []
    for fact in facts:
        value = fact.get("path")
        if not isinstance(value, str) or not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            continue
        resolved = path.resolve()
        if resolved in admitted_files or any(
            resolved == directory or directory in resolved.parents
            for directory in admitted_directories
        ):
            selected.append(fact)
    return selected


def _is_standalone_header_input(path: Path) -> bool:
    """Return whether a path needs parsing outside a compile database.

    Compilation databases describe translation units, not public headers.
    Passing a header beside a source while selecting a database can therefore
    silently omit declarations that are not reached by that source's selected
    command. Keep explicit headers and conventional include trees as their own
    semantic analysis unit.
    """
    return path.suffix == ".h" or (path.is_dir() and path.name == "include")


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
    grouped: dict[tuple[Path | None, str], set[Path]] = defaultdict(set)

    def add(repository: Path | None, path: Path) -> None:
        scope = (
            _analysis_scope(repository, path)
            if repository is not None
            else None
        )
        if scope is None:
            category = "shared"
        else:
            try:
                relative = path.resolve().relative_to(scope.resolve())
            except ValueError:
                category = "shared"
            else:
                first = relative.parts[0] if relative.parts else ""
                category = first if first in {"test", "fuzz"} else "production"
        grouped[(scope, category)].add(path)

    for admitted_path in admitted_paths:
        admitted_path = admitted_path.resolve()
        repository = _repository_root(workspace, admitted_path)
        if repository is not None:
            if admitted_path == repository:
                for scan_path in _repository_scan_paths(repository):
                    add(repository, scan_path)
            else:
                add(repository, admitted_path)
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
                child = child.resolve()
                for scan_path in _repository_scan_paths(child):
                    add(child, scan_path)
        else:
            add(None, admitted_path)
    return tuple(
        (repository, tuple(sorted(paths)))
        for (repository, _category), paths in sorted(
            grouped.items(),
            key=lambda item: (
                "" if item[0][0] is None else str(item[0][0]),
                item[0][1],
            ),
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



SNAPSHOT_SCHEMA = "p101-facts-snapshot-v3"
_SNAPSHOT_SKIP_DIRECTORIES = {".git", "__pycache__", ".pytest_cache", ".facts-cache", "_to_delete"}
_FILE_CONTENT_DIGESTS: dict[tuple[str, int, int, int, int, int], bytes] = {}
_MATERIALIZED_SNAPSHOTS: dict[
    tuple[str, str], list[dict[str, object]]
] = {}
_PRODUCER_LIBRARY_SOURCES = (
    "lib_c_facts",
    "lib_filesystem",
    "lib_io",
    "lib_c",
    "lib_env",
    "lib_tool_event",
    "lib_error",
)


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


def _file_signature(path: Path) -> tuple[str, int, int, int, int, int]:
    status = path.stat()
    return (
        str(path),
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _file_content_digest(path: Path) -> bytes:
    admitted_digest = manifest_digest(path)
    if admitted_digest is not None:
        return admitted_digest
    while True:
        before = _file_signature(path)
        cached = _FILE_CONTENT_DIGESTS.get(before)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        after = _file_signature(path)
        if before == after:
            value = digest.digest()
            _FILE_CONTENT_DIGESTS[before] = value
            return value


def _hash_file_into(digest: "hashlib._Hash", path: Path) -> None:
    digest.update(_file_content_digest(path))


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
        entries.sort()
        manifest_digest = manifest_hash_tree(root, entries)
        if manifest_digest is not None:
            memo[root] = manifest_digest
            return manifest_digest
        for path in entries:
            digest.update(str(path.relative_to(root)).encode())
            _hash_file_into(digest, path)
    memo[root] = value = digest.hexdigest()
    return value


def _producer_identity(
    producer: Path, workspace: Path | None = None
) -> bytes:
    digest = hashlib.sha256()
    digest.update(SNAPSHOT_SCHEMA.encode())
    digest.update(platform.system().encode())
    producer_repository = producer.parent
    digest.update(b"launcher\0")
    _hash_file_into(digest, producer)
    expected_launcher = (
        workspace.resolve() / "programs" / "p101-audit" / "audit-facts"
        if workspace is not None
        else producer
    )
    if producer.resolve() == expected_launcher.resolve():
        candidates: list[Path] = []
        for marker_name in (".last-build-dir", ".last-runtime-build-dir"):
            marker = producer_repository / marker_name
            if marker.is_file():
                build_name = marker.read_text(encoding="utf-8").strip()
                candidate = producer_repository / build_name / "audit-facts"
                if build_name:
                    candidates.append(candidate)
        candidates.extend(
            producer_repository / directory / "audit-facts"
            for directory in ("build-clang", "build-clang-22", "build")
        )
        selected = next((path for path in candidates if path.is_file()), None)
        if selected is None:
            raise FileNotFoundError("native semantic fact producer is absent")
        # Build-lane and transaction paths are orchestration state. Bind the
        # launcher and selected native producer by bytes so identical tools
        # share facts across qualified candidate directories.
        digest.update(b"native\0")
        _hash_file_into(digest, selected)
    # audit-facts is dynamically linked. Its executable bytes do not change
    # when an already-linked p101 dylib/DSO is rebuilt, so bind the admitted
    # source of every local runtime dependency as well. Build markers and lane
    # paths remain excluded, preserving reuse across equivalent candidates.
    if workspace is None:
        workspace = producer_repository.parent.parent
    workspace = workspace.resolve()
    memo: dict[Path, str] = {}
    for library in _PRODUCER_LIBRARY_SOURCES:
        repository = workspace / "libraries" / library
        for relative in ("include", "src"):
            root = repository / relative
            if root.is_dir():
                digest.update(f"{library}/{relative}\0".encode())
                digest.update(_tree_digest(root, memo).encode())
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
    memo_key = (str(cache_directory.resolve()), key)
    cached = _MATERIALIZED_SNAPSHOTS.get(memo_key)
    if cached is not None:
        return cached
    path = cache_directory / f"{key}.json.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError):
        return None
    if (
        not isinstance(document, dict)
        or document.get("schema") != SNAPSHOT_SCHEMA
        or document.get("key") != key
        or not isinstance(document.get("facts"), list)
    ):
        return None
    facts = document["facts"]
    if len(facts) != document.get("fact_count"):
        return None
    _MATERIALIZED_SNAPSHOTS[memo_key] = facts
    return facts


def _snapshot_metadata_path(cache_directory: Path, key: str) -> Path:
    return cache_directory / f"{key}.meta.json"


def _snapshot_store_metadata(
    cache_directory: Path, key: str, fact_count: int
) -> None:
    payload = cache_directory / f"{key}.json.gz"
    try:
        payload_digest = _file_content_digest(payload).hex()
        destination = _snapshot_metadata_path(cache_directory, key)
        temporary = cache_directory / f".{key}.{os.getpid()}.meta.tmp"
        temporary.write_text(
            json.dumps(
                {
                    "schema": "p101-facts-snapshot-metadata-v1",
                    "key": key,
                    "fact_count": fact_count,
                    "payload_sha256": payload_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except OSError:
        return


def _snapshot_restore_count(cache_directory: Path, key: str) -> int | None:
    """Validate a snapshot without decoding its complete fact array."""
    payload = cache_directory / f"{key}.json.gz"
    metadata_path = _snapshot_metadata_path(cache_directory, key)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        fact_count = metadata.get("fact_count")
        if (
            metadata.get("schema") != "p101-facts-snapshot-metadata-v1"
            or metadata.get("key") != key
            or not isinstance(fact_count, int)
            or fact_count < 0
            or metadata.get("payload_sha256")
            != _file_content_digest(payload).hex()
        ):
            return None
        return fact_count
    except (OSError, ValueError, AttributeError):
        # Migrate an older valid cache entry once. Subsequent prime operations
        # validate its small sidecar and compressed-payload digest without
        # allocating and decoding every fact object.
        facts = _snapshot_restore(cache_directory, key)
        if facts is None:
            return None
        fact_count = len(facts)
        _snapshot_store_metadata(cache_directory, key, fact_count)
        return fact_count


def _snapshot_store(cache_directory: Path, key: str, facts: list[dict[str, object]]) -> None:
    try:
        cache_directory.mkdir(parents=True, exist_ok=True)
        temporary = cache_directory / f".{key}.{os.getpid()}.tmp"
        with gzip.open(
            temporary, "wt", encoding="utf-8", compresslevel=6
        ) as stream:
            json.dump(
                {
                    "schema": SNAPSHOT_SCHEMA,
                    "key": key,
                    "fact_count": len(facts),
                    "facts": facts,
                },
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        os.replace(temporary, cache_directory / f"{key}.json.gz")
        _MATERIALIZED_SNAPSHOTS[(str(cache_directory.resolve()), key)] = facts
        _snapshot_store_metadata(cache_directory, key, len(facts))
    except OSError:
        return


@contextlib.contextmanager
def _snapshot_key_lock(cache_directory: Path, key: str) -> Iterable[bool]:
    """Serialize one cache miss across concurrent checker processes."""
    stream = None
    locked = False
    try:
        locks = cache_directory / ".locks"
        locks.mkdir(parents=True, exist_ok=True)
        stream = (locks / f"{key}.lock").open("a+b")
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        locked = True
    except OSError:
        if stream is not None:
            stream.close()
            stream = None
    try:
        yield locked
    finally:
        if stream is not None:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()


def _acquire(
    workspace: Path,
    paths: Iterable[Path],
    *,
    compile_database: Path | None = None,
    additional_include_roots: Iterable[Path] = (),
    cache: Path | str | None = "auto",
    materialize: bool,
) -> tuple[list[dict[str, object]], int]:
    configured_producer = os.environ.get("P101_AUDIT_FACTS", "")
    producer = (
        Path(configured_producer).resolve()
        if configured_producer
        else workspace / "programs" / "p101-audit" / "audit-facts"
    )
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
    commands: list[
        tuple[list[str], Path | None, tuple[Path, ...], bool]
    ] = []
    for repository, unit_paths in units:
        # Workspace policy scans admit the paths named by the caller, not
        # merely the translation units selected by a repository's latest
        # build. A compilation database commonly omits public headers and
        # separate test trees. Use its include roots below, but only let an
        # explicit compile_database argument narrow analysis to its units.
        unit_compile_database = compile_database

        partitions: list[tuple[tuple[Path, ...], Path | None]] = [
            (unit_paths, unit_compile_database)
        ]
        if unit_compile_database is not None:
            header_paths = tuple(
                path for path in unit_paths if _is_standalone_header_input(path)
            )
            source_paths = tuple(
                path for path in unit_paths if not _is_standalone_header_input(path)
            )
            partitions = []
            if source_paths:
                partitions.append((source_paths, unit_compile_database))
            if header_paths:
                partitions.append((header_paths, None))

        for partition_paths, partition_database in partitions:
            command_paths = partition_paths
            canonicalized = False
            if repository is not None and partition_database is None:
                canonical_paths = _canonical_scan_paths(
                    repository, partition_paths
                )
                if canonical_paths is not None:
                    command_paths = canonical_paths
                    canonicalized = True
            include_roots = set(shared_include_roots)
            if repository is not None:
                if partition_database is None:
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
                for admitted_path in partition_paths:
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
            if partition_database is not None:
                command.extend(
                    ("--compile-db", str(partition_database.resolve()))
                )
            command.extend(str(path) for path in command_paths)
            commands.append(
                (
                    command,
                    repository,
                    partition_paths,
                    canonicalized,
                )
            )

    cache_directory = _resolve_snapshot_cache(cache)
    unit_keys: list[str | None] = [None] * len(commands)
    unit_facts: list[list[dict[str, object]] | None] = [None] * len(commands)
    unit_fact_counts: list[int | None] = [None] * len(commands)
    if cache_directory is not None:
        try:
            producer_identity = _producer_identity(producer, workspace_root)
        except OSError:
            producer_identity = None
        if producer_identity is not None:
            memo: dict[Path, str] = {}
            for index, (command, _repository, _paths, _canonical) in enumerate(
                commands
            ):
                try:
                    key = _unit_snapshot_key(
                        workspace_root, producer_identity, command, memo
                    )
                except OSError:
                    continue
                unit_keys[index] = key
                if materialize:
                    unit_facts[index] = _snapshot_restore(cache_directory, key)
                    if unit_facts[index] is not None:
                        unit_fact_counts[index] = len(unit_facts[index] or [])
                else:
                    unit_fact_counts[index] = _snapshot_restore_count(
                        cache_directory, key
                    )

    pending = [
        index
        for index in range(len(commands))
        if (
            unit_facts[index] is None
            if materialize
            else unit_fact_counts[index] is None
        )
    ]
    if cache_directory is not None:
        restored_count = len(commands) - len(pending)
        if restored_count or pending:
            print(
                f"p101 facts snapshot: restored {restored_count} of "
                f"{len(commands)} units, parsing {len(pending)}",
                file=sys.stderr,
            )

    def invoke_unit(
        command: list[str],
    ) -> tuple[subprocess.CompletedProcess[str], float]:
        started = time.monotonic()
        if os.environ.get("P101_FACTS_VERBOSE") == "1":
            print("p101 facts command: " + shlex.join(command), file=sys.stderr)
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

    def run_unit(
        index: int,
    ) -> tuple[
        subprocess.CompletedProcess[str] | None,
        float,
        list[dict[str, object]] | None,
        bool,
        int | None,
    ]:
        command = commands[index][0]
        key = unit_keys[index]

        def produce() -> tuple[
            subprocess.CompletedProcess[str],
            float,
            list[dict[str, object]] | None,
            bool,
            int | None,
        ]:
            completed, seconds = invoke_unit(command)
            decoded = (
                decode_lines(completed.stdout.splitlines())
                if completed.returncode == 0
                else None
            )
            if (
                decoded is not None
                and cache_directory is not None
                and key is not None
            ):
                _snapshot_store(cache_directory, key, decoded)
            return (
                completed,
                seconds,
                decoded if materialize else None,
                False,
                len(decoded) if decoded is not None else None,
            )

        if cache_directory is None or key is None:
            return produce()
        with _snapshot_key_lock(cache_directory, key):
            if materialize:
                restored = _snapshot_restore(cache_directory, key)
                if restored is not None:
                    return None, 0.0, restored, True, len(restored)
            else:
                restored_count = _snapshot_restore_count(cache_directory, key)
                if restored_count is not None:
                    return None, 0.0, None, True, restored_count
            return produce()

    # Units are independent producer invocations over disjoint trees; run
    # the ones without a snapshot across cores and keep the facts in unit
    # order so callers see the exact stream the sequential loop produced.
    if pending:
        workers = _fact_worker_count(len(pending))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run_unit, pending))
        durations: list[tuple[float, int]] = []
        coalesced = 0
        for index, (
            result,
            seconds,
            decoded,
            restored_after_wait,
            fact_count,
        ) in zip(
            pending, results
        ):
            if restored_after_wait:
                unit_facts[index] = decoded
                unit_fact_counts[index] = fact_count
                coalesced += 1
                continue
            durations.append((seconds, index))
            repository = commands[index][1]
            if result is None:
                raise CFactError("semantic C-fact producer returned no result")
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
            unit_facts[index] = decoded
            unit_fact_counts[index] = fact_count
        if coalesced:
            print(
                "p101 facts snapshot: coalesced "
                f"{coalesced} concurrent unit(s)",
                file=sys.stderr,
            )
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
    fact_count = 0
    if materialize:
        for unit, command_record in zip(unit_facts, commands):
            admitted = command_record[2]
            canonicalized = command_record[3]
            selected = (
                _facts_for_admitted_paths(unit or [], admitted)
                if canonicalized
                else (unit or [])
            )
            fact_count += len(selected)
            facts.extend(selected)
    else:
        fact_count = sum(count or 0 for count in unit_fact_counts)
    if cache_directory is not None:
        for key in unit_keys:
            if key is not None:
                try:
                    record_usage(cache_directory, "runtime-facts", key)
                except OSError as error:
                    raise CFactError(
                        f"cannot record semantic fact usage: {error}"
                    ) from error
    return facts, fact_count


def acquire(
    workspace: Path,
    paths: Iterable[Path],
    *,
    compile_database: Path | None = None,
    additional_include_roots: Iterable[Path] = (),
    cache: Path | str | None = "auto",
) -> list[dict[str, object]]:
    """Acquire and materialize semantic facts for a policy consumer."""
    facts, _fact_count = _acquire(
        workspace,
        paths,
        compile_database=compile_database,
        additional_include_roots=additional_include_roots,
        cache=cache,
        materialize=True,
    )
    return facts


def prime(
    workspace: Path,
    paths: Iterable[Path],
    *,
    compile_database: Path | None = None,
    additional_include_roots: Iterable[Path] = (),
    cache: Path | str | None = "auto",
) -> int:
    """Materialize valid snapshots without retaining their fact objects."""
    _facts, fact_count = _acquire(
        workspace,
        paths,
        compile_database=compile_database,
        additional_include_roots=additional_include_roots,
        cache=cache,
        materialize=False,
    )
    return fact_count
