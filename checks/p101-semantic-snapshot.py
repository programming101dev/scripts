#!/usr/bin/env python3
"""Verify and receipt the shared immutable semantic-fact store.

The store contains lazily materialized, content-addressed analysis units. It is
one evidence boundary, not one invalidation unit: a source edit replaces only
the entries whose admitted inputs changed. Consumers still own their policy.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


RECEIPT_SCHEMA = "p101-semantic-snapshot-receipt-v1"
RUNTIME_SCHEMA = "p101-facts-snapshot-v2"
RAW_SCHEMA = "p101-facts-cache-v1"
USAGE_SCHEMA = "p101-semantic-usage-v1"
DOES_NOT_PROVE = (
    "This receipt proves the identity and integrity of materialized semantic "
    "fact entries. It does not prove that the admitted source scopes are "
    "complete or that any consumer policy is correct."
)


class SnapshotError(ValueError):
    """The semantic store contains malformed or mutable evidence."""


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def runtime_entry(path: Path) -> dict[str, Any]:
    key = path.name.removesuffix(".json.gz")
    if len(key) != 64 or any(
        character not in "0123456789abcdef" for character in key
    ):
        raise SnapshotError(f"invalid runtime entry name: {path}")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SnapshotError(
            f"cannot decode runtime entry {path}: {error}"
        ) from error
    if (
        not isinstance(document, dict)
        or document.get("schema") != RUNTIME_SCHEMA
        or document.get("key") != key
        or not isinstance(document.get("facts"), list)
    ):
        raise SnapshotError(f"runtime entry identity mismatch: {path}")
    count = len(document["facts"])
    if document.get("fact_count") != count:
        raise SnapshotError(f"runtime entry fact count mismatch: {path}")
    return {
        "kind": "runtime-facts",
        "key": key,
        "sha256": hash_file(path),
        "bytes": path.stat().st_size,
        "facts": count,
    }


def raw_entry(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SnapshotError(f"cannot decode raw entry {path}: {error}") from error
    if manifest.get("schema") != RAW_SCHEMA or manifest.get("key") != path.name:
        raise SnapshotError(f"raw entry identity mismatch: {path}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise SnapshotError(f"raw entry has no artifacts: {path}")
    artifact_records: list[dict[str, Any]] = []
    for name, expected in sorted(artifacts.items()):
        artifact = path / name
        if (
            not isinstance(expected, dict)
            or not artifact.is_file()
            or artifact.stat().st_size != expected.get("bytes")
            or hash_file(artifact) != expected.get("sha256")
        ):
            raise SnapshotError(f"raw artifact integrity mismatch: {artifact}")
        artifact_records.append(
            {
                "name": name,
                "sha256": expected["sha256"],
                "bytes": expected["bytes"],
            }
        )
    return {
        "kind": "compile-database-facts",
        "key": path.name,
        "namespace": manifest.get("namespace", ""),
        "manifest_sha256": hash_file(manifest_path),
        "artifacts": artifact_records,
    }


def usage_keys(directory: Path) -> dict[tuple[str, str], set[str]]:
    if not directory.is_dir():
        raise SnapshotError(
            f"semantic usage directory does not exist: {directory}"
        )
    used: dict[tuple[str, str], set[str]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines if line]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SnapshotError(
                f"cannot decode semantic usage {path}: {error}"
            ) from error
        for record in records:
            kind = record.get("kind")
            key = record.get("key")
            if (
                record.get("schema") != USAGE_SCHEMA
                or kind
                not in {"runtime-facts", "compile-database-facts"}
                or not isinstance(key, str)
                or len(key) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in key
                )
            ):
                raise SnapshotError(f"invalid semantic usage record: {path}")
            used.setdefault((kind, key), set()).add(path.stem)
    return used


def inspect(
    cache: Path, usage_directory: Path
) -> list[dict[str, Any]]:
    if not cache.is_dir():
        raise SnapshotError(f"semantic snapshot store does not exist: {cache}")
    entries: list[dict[str, Any]] = []
    used = usage_keys(usage_directory)
    for kind, key in sorted(used):
        path = (
            cache / f"{key}.json.gz"
            if kind == "runtime-facts"
            else cache / "entries" / key
        )
        if not path.exists():
            raise SnapshotError(
                f"semantic usage references a missing entry: {path}"
            )
        entry = runtime_entry(path) if kind == "runtime-facts" else raw_entry(path)
        entry["consumers"] = sorted(used[(kind, key)])
        entries.append(entry)
    return entries


def prune_cache(
    cache: Path, entries: list[dict[str, Any]]
) -> dict[str, int]:
    retained_runtime = {
        entry["key"]
        for entry in entries
        if entry["kind"] == "runtime-facts"
    }
    retained_raw = {
        entry["key"]
        for entry in entries
        if entry["kind"] == "compile-database-facts"
    }
    removed_entries = 0
    removed_bytes = 0

    def remove_file(path: Path) -> None:
        nonlocal removed_entries, removed_bytes
        try:
            removed_bytes += path.stat().st_size
            path.unlink()
            removed_entries += 1
        except FileNotFoundError:
            pass

    for path in sorted(cache.glob("*.json.gz")):
        key = path.name.removesuffix(".json.gz")
        if key not in retained_runtime:
            remove_file(path)
    # v1 runtime entries and abandoned atomic writes are never valid v2
    # evidence. The snapshot is terminal, so no producer remains active.
    for pattern in ("*.jsonl.gz", ".*.tmp"):
        for path in sorted(cache.glob(pattern)):
            remove_file(path)
    raw_root = cache / "entries"
    if raw_root.is_dir():
        for path in sorted(raw_root.iterdir()):
            if path.is_dir() and path.name not in retained_raw:
                removed_bytes += sum(
                    child.stat().st_size
                    for child in path.rglob("*")
                    if child.is_file()
                )
                shutil.rmtree(path)
                removed_entries += 1
    locks = cache / ".locks"
    if locks.is_dir():
        removed_bytes += sum(
            child.stat().st_size
            for child in locks.rglob("*")
            if child.is_file()
        )
        shutil.rmtree(locks)
    return {
        "entries_removed": removed_entries,
        "bytes_removed": removed_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--usage-directory", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="remove cache entries not referenced by this completed run",
    )
    arguments = parser.parse_args()
    try:
        entries = inspect(
            arguments.cache.resolve(), arguments.usage_directory.resolve()
        )
    except (OSError, SnapshotError) as error:
        print(f"p101 semantic snapshot: {error}")
        return 1
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "snapshot_digest": canonical_digest(entries),
        "entry_count": len(entries),
        "runtime_entry_count": sum(
            entry["kind"] == "runtime-facts" for entry in entries
        ),
        "compile_database_entry_count": sum(
            entry["kind"] == "compile-database-facts" for entry in entries
        ),
        "entries": entries,
        "does_not_prove": DOES_NOT_PROVE,
    }
    receipt["pruning"] = (
        prune_cache(arguments.cache.resolve(), entries)
        if arguments.prune
        else {"entries_removed": 0, "bytes_removed": 0}
    )
    receipt["receipt_digest"] = canonical_digest(receipt)
    arguments.receipt.parent.mkdir(parents=True, exist_ok=True)
    arguments.receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "p101 semantic snapshot: "
        f"{receipt['entry_count']} immutable entries, "
        f"{receipt['snapshot_digest']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
