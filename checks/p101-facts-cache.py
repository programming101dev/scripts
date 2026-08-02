#!/usr/bin/env python3
"""Store and restore content-addressed p101 C-fact acquisition artifacts.

The cache admits the producer binary, compile database, selected source trees,
and shared header roots. It caches evidence only; each consuming tool still
applies its own policy to the restored artifacts.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = "p101-c-facts-cache-v1"
IGNORED_DIRECTORIES = {".git", ".pytest_cache", "__pycache__"}


class CacheError(RuntimeError):
    """An invalid cache request or artifact."""


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def admitted_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise CacheError(f"admitted path does not exist: {root}")
    files: list[Path] = []
    for directory, names, filenames in os.walk(root):
        names[:] = sorted(
            name
            for name in names
            if name not in IGNORED_DIRECTORIES and not name.startswith("build")
        )
        base = Path(directory)
        files.extend(base / name for name in sorted(filenames) if (base / name).is_file())
    return files


def shared_header_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise CacheError(f"dependency root does not exist: {root}")
    files: list[Path] = []
    for include in sorted(root.glob("*/include")):
        files.extend(admitted_files(include))
    return files


def producer_files(producer: Path) -> list[Path]:
    """Include native binaries selected by a repository launcher."""
    files = [producer]
    repository = producer.parent
    for marker_name in (".last-runtime-build-dir", ".last-build-dir"):
        marker = repository / marker_name
        if not marker.is_file():
            continue
        files.append(marker)
        build_name = marker.read_text(encoding="utf-8").strip()
        if not build_name:
            continue
        for executable_name in (producer.name, "p101-c-facts"):
            executable = repository / build_name / executable_name
            if executable.is_file():
                files.append(executable)
    return files


def cache_key(args: argparse.Namespace) -> tuple[str, list[dict[str, str]]]:
    producer = args.producer.resolve()
    compile_db = args.compile_db.resolve()
    if not producer.is_file():
        raise CacheError(f"producer does not exist: {producer}")
    if not compile_db.is_file():
        raise CacheError(f"compile database does not exist: {compile_db}")

    inputs = [*producer_files(producer), compile_db]
    for path in args.path:
        inputs.extend(admitted_files(path.resolve()))
    for root in args.dependency_root:
        inputs.extend(shared_header_files(root.resolve()))

    unique = sorted({path.resolve() for path in inputs}, key=str)
    records = [{"path": str(path), "sha256": hash_file(path)} for path in unique]
    request = {
        "schema": SCHEMA,
        "namespace": args.namespace,
        "platform": platform.system(),
        "machine": platform.machine(),
        "inputs": records,
    }
    encoded = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), records


def parse_artifacts(values: list[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path or "/" in name or name in {".", ".."}:
            raise CacheError(f"artifact must be NAME=PATH: {value}")
        if name in artifacts:
            raise CacheError(f"duplicate artifact name: {name}")
        artifacts[name] = Path(raw_path).resolve()
    if not artifacts:
        raise CacheError("at least one --artifact is required")
    return artifacts


def entry_path(cache: Path, key: str) -> Path:
    return cache.resolve() / "entries" / key


@contextmanager
def entry_lock(cache: Path, key: str, *, exclusive: bool) -> Iterator[None]:
    locks = cache.resolve() / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f"{key}.lock").open("a+b") as stream:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(stream.fileno(), operation)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def store(args: argparse.Namespace, key: str, inputs: list[dict[str, str]]) -> int:
    artifacts = parse_artifacts(args.artifact)
    for name, path in artifacts.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise CacheError(f"artifact is missing or empty: {name}={path}")

    destination = entry_path(args.cache, key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with entry_lock(args.cache, key, exclusive=True):
        temporary = Path(tempfile.mkdtemp(prefix=f".{key[:12]}.", dir=destination.parent))
        try:
            if destination.is_dir():
                for current in destination.iterdir():
                    if current.is_file():
                        shutil.copy2(current, temporary / current.name)
            for name, source in artifacts.items():
                shutil.copy2(source, temporary / name)
            manifest = {
                "schema": SCHEMA,
                "key": key,
                "namespace": args.namespace,
                "input_count": len(inputs),
                "inputs": inputs,
                "artifacts": {
                    path.name: {"sha256": hash_file(path), "bytes": path.stat().st_size}
                    for path in sorted(temporary.iterdir())
                    if path.is_file() and path.name != "manifest.json"
                },
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    print(f"p101 C-facts cache STORE {args.namespace} {key[:12]}")
    return 0


def restore(args: argparse.Namespace, key: str) -> int:
    artifacts = parse_artifacts(args.artifact)
    source = entry_path(args.cache, key)
    with entry_lock(args.cache, key, exclusive=False):
        manifest_path = source / "manifest.json"
        if not manifest_path.is_file():
            print(f"p101 C-facts cache MISS {args.namespace} {key[:12]}")
            return 1
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CacheError(f"invalid cache manifest: {error}") from error
        if manifest.get("schema") != SCHEMA or manifest.get("key") != key:
            raise CacheError(f"cache manifest identity mismatch: {manifest_path}")

        for name in artifacts:
            cached = source / name
            record = manifest.get("artifacts", {}).get(name)
            if not cached.is_file() or not isinstance(record, dict):
                print(f"p101 C-facts cache MISS {args.namespace} {key[:12]} ({name})")
                return 1
            if hash_file(cached) != record.get("sha256"):
                raise CacheError(f"cached artifact hash mismatch: {cached}")

        for name, output in artifacts.items():
            output.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
            os.close(handle)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(source / name, temporary)
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
    print(f"p101 C-facts cache HIT {args.namespace} {key[:12]}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("operation", choices=("store", "restore"))
    result.add_argument("--cache", type=Path, required=True)
    result.add_argument("--namespace", required=True)
    result.add_argument("--producer", type=Path, required=True)
    result.add_argument("--compile-db", type=Path, required=True)
    result.add_argument("--path", type=Path, action="append", default=[])
    result.add_argument("--dependency-root", type=Path, action="append", default=[])
    result.add_argument("--artifact", action="append", default=[])
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        key, inputs = cache_key(args)
        if args.operation == "store":
            return store(args, key, inputs)
        return restore(args, key)
    except (CacheError, OSError) as error:
        print(f"p101 C-facts cache error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
