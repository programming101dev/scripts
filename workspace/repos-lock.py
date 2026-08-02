#!/usr/bin/env python3
"""Create, validate, inspect, and receipt the p101 repository lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = "p101-repository-lock-v1"
RECEIPT_SCHEMA = "p101-repository-lock-receipt-v1"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SUPPORTED_TYPES = {"c", "cxx", "python", "c-bootstrap"}


class LockError(ValueError):
    """The repository manifest, lock, or workspace does not satisfy its contract."""


@dataclass(frozen=True)
class Repository:
    url: str
    path: str
    kind: str
    commit: str = ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(path: Path) -> list[Repository]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise LockError(f"cannot read repository manifest {path}: {error}") from error
    repositories: list[Repository] = []
    seen_paths: set[str] = set()
    seen_urls: set[str] = set()
    for line_number, raw in enumerate(lines, 1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        fields = [field.strip() for field in text.split("|")]
        if len(fields) != 3 or not all(fields):
            raise LockError(f"{path}:{line_number}: expected url|path|type")
        url, repository_path, kind = fields
        if kind not in SUPPORTED_TYPES:
            raise LockError(f"{path}:{line_number}: unsupported type {kind!r}")
        if repository_path in seen_paths:
            raise LockError(f"{path}:{line_number}: duplicate path {repository_path!r}")
        if url in seen_urls:
            raise LockError(f"{path}:{line_number}: duplicate URL {url!r}")
        seen_paths.add(repository_path)
        seen_urls.add(url)
        repositories.append(Repository(url, repository_path, kind))
    if not repositories:
        raise LockError(f"{path}: manifest contains no repositories")
    return repositories


def read_lock(path: Path, manifest_path: Path) -> list[Repository]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LockError(f"cannot read repository lock {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise LockError(f"{path}: expected schema {SCHEMA}")
    expected_manifest_hash = sha256(manifest_path)
    if document.get("manifest_sha256") != expected_manifest_hash:
        raise LockError(
            f"{path}: manifest digest does not match {manifest_path}; refresh the lock"
        )
    rows = document.get("repositories")
    if not isinstance(rows, list) or not rows:
        raise LockError(f"{path}: repositories must be a non-empty array")
    locked: list[Repository] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"url", "path", "type", "commit"}:
            raise LockError(f"{path}: repository row {index} has invalid fields")
        values = [row.get(key) for key in ("url", "path", "type", "commit")]
        if any(not isinstance(value, str) for value in values):
            raise LockError(f"{path}: repository row {index} has a non-string field")
        url, repository_path, kind, commit = values
        if not url or not repository_path or not kind:
            raise LockError(f"{path}: repository row {index} has an empty field")
        if kind not in SUPPORTED_TYPES:
            raise LockError(f"{path}: repository row {index} has unsupported type")
        if not (kind == "c-bootstrap" and commit == "") and (
            COMMIT_PATTERN.fullmatch(commit) is None
        ):
            raise LockError(f"{path}: repository row {index} has invalid commit")
        locked.append(Repository(url, repository_path, kind, commit))

    manifest = read_manifest(manifest_path)
    unlocked_shape = [(item.url, item.path, item.kind) for item in manifest]
    locked_shape = [(item.url, item.path, item.kind) for item in locked]
    if locked_shape != unlocked_shape:
        raise LockError(
            f"{path}: repository entries or order differ from {manifest_path}; "
            "refresh the lock"
        )
    return locked


def git(repository: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", os.fspath(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise LockError(
            f"{repository}: git {' '.join(arguments)} failed"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout.strip()


def repository_root(scripts_root: Path, entry: Repository) -> Path:
    configured = Path(entry.path)
    workspace_root = scripts_root.parent.resolve()
    if configured.is_absolute():
        raise LockError(f"{entry.path}: repository path must be relative")
    result = (scripts_root / configured).resolve()
    if result == workspace_root or workspace_root not in result.parents:
        raise LockError(
            f"{entry.path}: repository path escapes workspace {workspace_root}"
        )
    return result


def validate_repository_paths(
    scripts_root: Path, repositories: list[Repository]
) -> None:
    seen: dict[Path, str] = {}
    for entry in repositories:
        resolved = repository_root(scripts_root, entry)
        if resolved in seen:
            raise LockError(
                f"{entry.path}: resolves to the same repository path as {seen[resolved]}"
            )
        seen[resolved] = entry.path


def inspect_repository(
    scripts_root: Path,
    entry: Repository,
    *,
    expected_commit: str | None,
    require_clean: bool,
    require_upstream: bool,
) -> dict[str, Any]:
    path = repository_root(scripts_root, entry)
    actual_root = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
    if actual_root != path:
        raise LockError(f"{path}: configured path is not the repository root")
    origin = git(path, "remote", "get-url", "origin")
    if origin != entry.url:
        raise LockError(f"{path}: origin is {origin!r}, expected {entry.url!r}")
    head = git(path, "rev-parse", "--verify", "HEAD", check=False)
    if not head:
        if entry.kind != "c-bootstrap":
            raise LockError(f"{path}: active repository has no commit")
        dirty = bool(git(path, "status", "--porcelain=v1", "--untracked-files=normal"))
        if require_clean and dirty:
            raise LockError(f"{path}: worktree is not clean")
        if expected_commit not in (None, ""):
            raise LockError(
                f"{path}: unborn repository does not match locked "
                f"{expected_commit[:12]}"
            )
        return {
            "url": entry.url,
            "path": entry.path,
            "type": entry.kind,
            "commit": "",
            "dirty": dirty,
            "upstream": "",
        }
    if COMMIT_PATTERN.fullmatch(head) is None:
        raise LockError(f"{path}: HEAD is not a full commit identifier")
    if expected_commit is not None and head != expected_commit:
        raise LockError(
            f"{path}: HEAD {head[:12]} does not match locked {expected_commit[:12]}"
        )
    dirty = bool(git(path, "status", "--porcelain=v1", "--untracked-files=normal"))
    if require_clean and dirty:
        raise LockError(f"{path}: worktree is not clean")
    upstream = ""
    if require_upstream:
        branch = git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
        if not branch:
            raise LockError(f"{path}: cannot refresh a lock from detached HEAD")
        upstream = git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        upstream_commit = git(path, "rev-parse", "@{u}")
        if head != upstream_commit:
            raise LockError(
                f"{path}: HEAD {head[:12]} is not the configured upstream "
                f"{upstream} at {upstream_commit[:12]}"
            )
    return {
        "url": entry.url,
        "path": entry.path,
        "type": entry.kind,
        "commit": head,
        "dirty": dirty,
        "upstream": upstream,
    }


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def refresh(args: argparse.Namespace) -> int:
    manifest = read_manifest(args.manifest)
    validate_repository_paths(args.scripts_root, manifest)
    rows = [
        inspect_repository(
            args.scripts_root,
            entry,
            expected_commit=None,
            require_clean=args.require_clean,
            require_upstream=True,
        )
        for entry in manifest
    ]
    for row in rows:
        row.pop("dirty")
        row.pop("upstream")
    write_json(
        args.lock,
        {
            "schema": SCHEMA,
            "manifest_sha256": sha256(args.manifest),
            "repositories": rows,
        },
    )
    print(f"repository lock: {len(rows)} repositories")
    print(f"lock_sha256={sha256(args.lock)}")
    print(f"wrote: {args.lock}")
    return 0


def verify(args: argparse.Namespace) -> int:
    failures: list[str] = []
    inspected: list[dict[str, Any]] = []
    lock_digest = ""
    manifest_digest = ""
    try:
        locked = read_lock(args.lock, args.manifest)
        validate_repository_paths(args.scripts_root, locked)
        lock_digest = sha256(args.lock)
        manifest_digest = sha256(args.manifest)
    except LockError as error:
        locked = []
        failures.append(str(error))
    for entry in locked:
        try:
            inspected.append(
                inspect_repository(
                    args.scripts_root,
                    entry,
                    expected_commit=entry.commit,
                    require_clean=args.require_clean,
                    require_upstream=False,
                )
            )
        except LockError as error:
            failures.append(str(error))
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "lock_schema": SCHEMA,
        "lock_sha256": lock_digest,
        "manifest_sha256": manifest_digest,
        "repository_count": len(locked),
        "dirty_repository_count": sum(bool(row["dirty"]) for row in inspected),
        "passed": not failures,
        "failures": failures,
        "repositories": inspected,
        "does_not_prove": (
            "remote availability, uncommitted-content reproducibility, dependency "
            "behavior, or correctness beyond the locked revisions"
        ),
    }
    if args.receipt is not None:
        write_json(args.receipt, receipt)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(
        f"repository lock verified: {len(locked)} repositories, "
        f"{receipt['dirty_repository_count']} dirty worktree(s)"
    )
    print(f"lock_sha256={lock_digest}")
    if args.receipt is not None:
        print(f"receipt: {args.receipt}")
    return 0


def entries(args: argparse.Namespace) -> int:
    locked = read_lock(args.lock, args.manifest)
    validate_repository_paths(args.scripts_root, locked)
    for entry in locked:
        print(f"{entry.url}|{entry.path}|{entry.kind}|{entry.commit}")
    return 0


def parser() -> argparse.ArgumentParser:
    scripts_root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(
        description="Manage the exact-revision contract for the p101 workspace."
    )
    result.add_argument("--scripts-root", type=Path, default=scripts_root)
    result.add_argument("--manifest", type=Path, default=scripts_root / "repos.txt")
    result.add_argument("--lock", type=Path, default=scripts_root / "repos.lock")
    subparsers = result.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser(
        "refresh", help="write a deterministic lock from upstream-synchronized HEADs"
    )
    refresh_parser.add_argument(
        "--require-clean",
        action="store_true",
        help="also refuse generated or other worktree changes",
    )
    refresh_parser.set_defaults(function=refresh)

    verify_parser = subparsers.add_parser(
        "verify", help="verify local repositories against the lock"
    )
    verify_parser.add_argument("--receipt", type=Path)
    verify_parser.add_argument(
        "--require-clean",
        action="store_true",
        help="also require every worktree to be clean",
    )
    verify_parser.set_defaults(function=verify)

    entries_parser = subparsers.add_parser(
        "entries", help="emit validated url|path|type|commit rows"
    )
    entries_parser.set_defaults(function=entries)
    return result


def main() -> int:
    args = parser().parse_args()
    args.scripts_root = args.scripts_root.resolve()
    args.manifest = args.manifest.resolve()
    args.lock = args.lock.resolve()
    try:
        return int(args.function(args))
    except LockError as error:
        print(f"repository lock: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
