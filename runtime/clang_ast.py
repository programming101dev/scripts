"""Acquire one content-addressed Clang JSON syntax model.

The wrapper generator needs a small amount of statement structure that is not
part of P101FACT.  This module keeps that exceptional parse shareable and
receiptable: a warm consumer restores the exact model while every dependency
still has the size and modification time recorded by the producing parse.

This is a performance cache, not a proof boundary.  Callers remain responsible
for selecting complete compiler arguments and for applying policy to the AST.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from typing import Any, Iterable, Iterator

from content_manifest import hash_file
from semantic_usage import record_usage


SCHEMA = "p101-clang-ast-cache-v2"
_MATERIALIZED: dict[
    tuple[str, str], tuple[dict[str, Any], list[dict[str, object]]]
] = {}


class ClangASTError(RuntimeError):
    """Clang failed or a cached syntax model was malformed."""


def _cache_root() -> Path | None:
    configured = os.environ.get("P101_FACTS_CACHE", "")
    if configured.lower() in {"0", "off", "disabled"}:
        return None
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parent.parent / ".facts-cache"


@cache
def _compiler_identity(clang: str) -> dict[str, object]:
    resolved_text = shutil.which(clang) if os.sep not in clang else clang
    if resolved_text is None:
        raise ClangASTError(f"Clang executable is unavailable: {clang}")
    resolved = Path(resolved_text).resolve()
    try:
        version = subprocess.run(
            [os.fspath(resolved), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ClangASTError(f"cannot identify {resolved}: {error}") from error
    return {
        "path": os.fspath(resolved),
        "sha256": hash_file(resolved).hex(),
        "version": version,
    }


def _key(
    clang: str,
    source: Path,
    arguments: tuple[str, ...],
    retained_extents: tuple[tuple[int, int], ...],
) -> str:
    request = {
        "schema": SCHEMA,
        "compiler": _compiler_identity(clang),
        "source": os.fspath(source.resolve()),
        "source_sha256": hash_file(source.resolve()).hex(),
        "arguments": arguments,
        "retained_function_extents": retained_extents,
    }
    encoded = json.dumps(
        request, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dependency_record(path: Path) -> dict[str, object]:
    status = path.stat()
    return {
        "path": os.fspath(path.resolve()),
        "bytes": status.st_size,
        "modified_ns": status.st_mtime_ns,
        "changed_ns": status.st_ctime_ns,
        "device": status.st_dev,
        "inode": status.st_ino,
    }


def _dependencies_valid(records: object) -> bool:
    if not isinstance(records, list) or not records:
        return False
    for record in records:
        if not isinstance(record, dict):
            return False
        path_text = record.get("path")
        size = record.get("bytes")
        modified_ns = record.get("modified_ns")
        changed_ns = record.get("changed_ns")
        device = record.get("device")
        inode = record.get("inode")
        if (
            not isinstance(path_text, str)
            or not isinstance(size, int)
            or not isinstance(modified_ns, int)
            or not isinstance(changed_ns, int)
            or not isinstance(device, int)
            or not isinstance(inode, int)
        ):
            return False
        try:
            status = Path(path_text).stat()
        except OSError:
            return False
        if (
            status.st_size != size
            or status.st_mtime_ns != modified_ns
            or status.st_ctime_ns != changed_ns
            or status.st_dev != device
            or status.st_ino != inode
        ):
            return False
    return True


def _decode_entry(entry: Path, key: str) -> dict[str, Any] | None:
    memo_key = (os.fspath(entry.parent.parent.resolve()), key)
    cached = _MATERIALIZED.get(memo_key)
    if cached is not None and _dependencies_valid(cached[1]):
        return cached[0]
    manifest_path = entry / "manifest.json"
    payload = entry / "ast.json.gz"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("schema") != SCHEMA
            or manifest.get("key") != key
            or payload.stat().st_size != manifest.get("payload_bytes")
            or not _dependencies_valid(manifest.get("dependencies"))
        ):
            return None
        with gzip.open(payload, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(document, dict):
        return None
    dependencies = manifest["dependencies"]
    _MATERIALIZED[memo_key] = (document, dependencies)
    return document


def _depfile_paths(path: Path, source: Path) -> tuple[Path, ...]:
    try:
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        _target, separator, values = text.replace("\\\n", " ").partition(":")
        if not separator:
            raise ValueError("missing dependency separator")
        # Clang escapes spaces in make depfiles. shlex handles those escapes
        # without interpreting shell expansions.
        candidates = [Path(value) for value in shlex.split(values)]
    except (OSError, ValueError) as error:
        raise ClangASTError(f"cannot decode Clang dependency file: {error}") from error
    dependencies = {
        candidate.resolve()
        for candidate in candidates
        if candidate.is_file()
    }
    dependencies.add(source.resolve())
    return tuple(sorted(dependencies, key=os.fspath))


@contextmanager
def _entry_lock(root: Path, key: str) -> Iterator[None]:
    locks = root / "ast-locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f"{key}.lock").open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _invoke(
    clang: str,
    source: Path,
    arguments: tuple[str, ...],
    workspace: Path,
    dependency_path: Path,
) -> dict[str, Any]:
    command = [
        clang,
        *arguments,
        "-fsyntax-only",
        "-Xclang",
        "-ast-dump=json",
        "-MD",
        "-MF",
        os.fspath(dependency_path),
        os.fspath(source),
    ]
    result = subprocess.run(
        command,
        cwd=workspace,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ClangASTError(
            f"Clang could not parse {source}:\n{result.stderr}"
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ClangASTError(f"Clang returned malformed JSON for {source}") from error
    if not isinstance(document, dict):
        raise ClangASTError(f"Clang returned a non-object AST for {source}")
    return document


def _node_extent(node: dict[str, Any]) -> tuple[int, int] | None:
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


def _project_function_definitions(
    document: dict[str, Any], retained_extents: tuple[tuple[int, int], ...]
) -> dict[str, Any]:
    admitted = set(retained_extents)
    retained: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = [document]
    while pending:
        node = pending.pop()
        if node.get("kind") == "FunctionDecl" and _node_extent(node) in admitted:
            retained.append(node)
            continue
        pending.extend(
            child
            for child in node.get("inner", [])
            if isinstance(child, dict)
        )
    return {"kind": "TranslationUnitDecl", "inner": retained}


def acquire(
    clang: str,
    source: Path,
    arguments: Iterable[str],
    workspace: Path,
    retained_function_extents: Iterable[tuple[int, int]] = (),
) -> dict[str, Any]:
    """Return a dependency-validated, content-addressed Clang JSON AST."""
    source = source.resolve()
    argument_tuple = tuple(arguments)
    retained_extents = tuple(sorted(set(retained_function_extents)))
    root = _cache_root()
    key = _key(clang, source, argument_tuple, retained_extents)
    if root is None:
        with tempfile.TemporaryDirectory(prefix="p101-clang-ast-") as directory:
            dependency_path = Path(directory) / "dependencies.d"
            document = _invoke(
                clang, source, argument_tuple, workspace, dependency_path
            )
            if retained_extents:
                document = _project_function_definitions(
                    document, retained_extents
                )
            return document

    entry = root / "ast" / key
    with _entry_lock(root, key):
        document = _decode_entry(entry, key)
        if document is None:
            entry.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(prefix=f".{key[:12]}.", dir=entry.parent)
            )
            try:
                dependency_path = temporary / "dependencies.d"
                document = _invoke(
                    clang,
                    source,
                    argument_tuple,
                    workspace,
                    dependency_path,
                )
                if retained_extents:
                    document = _project_function_definitions(
                        document, retained_extents
                    )
                dependencies = _depfile_paths(dependency_path, source)
                dependency_path.unlink()
                payload = temporary / "ast.json.gz"
                with gzip.open(
                    payload, "wt", encoding="utf-8", compresslevel=3
                ) as stream:
                    json.dump(document, stream, separators=(",", ":"))
                dependency_records = [
                    _dependency_record(path) for path in dependencies
                ]
                manifest = {
                    "schema": SCHEMA,
                    "key": key,
                    "payload_bytes": payload.stat().st_size,
                    "payload_sha256": hash_file(payload).hex(),
                    "dependencies": dependency_records,
                }
                (temporary / "manifest.json").write_text(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                    + "\n",
                    encoding="utf-8",
                )
                if entry.exists():
                    shutil.rmtree(entry)
                os.replace(temporary, entry)
                _MATERIALIZED[(os.fspath(entry.parent.parent.resolve()), key)] = (
                    document,
                    dependency_records,
                )
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
    record_usage(root, "clang-ast", key)
    return document
