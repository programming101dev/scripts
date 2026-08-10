#!/usr/bin/env python3
"""Manage exact repository locks and evidence-bound workspace candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = "p101-repository-lock-v1"
RECEIPT_SCHEMA = "p101-repository-lock-receipt-v1"
CANDIDATE_SCHEMA = "p101-workspace-candidate-v1"
CANDIDATE_COMPLETION_SCHEMA = "p101-workspace-candidate-completion-v1"
PLATFORM_QUALIFICATION_SCHEMA = "p101-workspace-platform-qualification-v1"
QUALIFICATION_SCHEMA = "p101-workspace-qualification-v1"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SUPPORTED_TYPES = {"c", "cxx", "python", "c-bootstrap"}
SUPPORTED_QUALIFICATION_PLATFORMS = {"linux", "macos", "freebsd"}


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


def document_sha256(document: dict[str, Any]) -> str:
    admitted = dict(document)
    admitted.pop("candidate_id", None)
    encoded = json.dumps(
        admitted, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def receipt_digest(document: dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("receipt_digest", None)
    encoded = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def require_receipt_digest(document: dict[str, Any], path: Path | str) -> None:
    if document.get("receipt_digest") != receipt_digest(document):
        raise LockError(f"{path}: receipt digest does not match its admitted bytes")


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


def git_succeeds(repository: Path, *arguments: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", os.fspath(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


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
    allow_ahead: bool = False,
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
        branch_result = subprocess.run(
            ["git", "-C", str(path), "symbolic-ref", "--quiet", "--short", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        branch = branch_result.stdout.strip()
        if not branch:
            raise LockError(f"{path}: cannot refresh a lock from detached HEAD")
        if allow_ahead and not git_succeeds(path, "rev-parse", "--verify", "@{u}"):
            upstream = ""
        else:
            upstream = git(
                path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
            )
            upstream_commit = git(path, "rev-parse", "@{u}")
            if head != upstream_commit and not (
                allow_ahead
                and git_succeeds(
                    path,
                    "merge-base",
                    "--is-ancestor",
                    upstream_commit,
                    head,
                )
            ):
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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def relative_evidence_path(receipt: Path, evidence: Path) -> str:
    try:
        return os.fspath(evidence.resolve().relative_to(receipt.parent.resolve()))
    except ValueError as error:
        raise LockError(
            f"candidate evidence {evidence} must be inside {receipt.parent}"
        ) from error


def publish_state(
    repository: Path,
    *,
    expected_url: str,
    expected_commit: str,
    logical_path: str,
    kind: str,
) -> dict[str, Any]:
    if not expected_commit:
        return {
            "url": expected_url,
            "path": logical_path,
            "type": kind,
            "commit": "",
            "branch": "",
            "remote_ref": "",
            "upstream_commit": "",
            "publish": False,
        }
    head = git(repository, "rev-parse", "--verify", "HEAD")
    if head != expected_commit:
        raise LockError(
            f"{repository}: HEAD {head[:12]} does not match candidate "
            f"{expected_commit[:12]}"
        )
    if git(repository, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise LockError(f"{repository}: worktree is not clean")
    origin = git(repository, "remote", "get-url", "origin")
    if origin != expected_url:
        raise LockError(f"{repository}: origin is {origin!r}, expected {expected_url!r}")
    branch = git(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
    configured_remote = git(
        repository, "config", "--get", f"branch.{branch}.remote", check=False
    )
    remote_ref = git(
        repository, "config", "--get", f"branch.{branch}.merge", check=False
    )
    if configured_remote != "origin" or not remote_ref.startswith("refs/heads/"):
        raise LockError(
            f"{repository}: branch {branch!r} must publish to an origin branch"
        )
    remote_branch = remote_ref.removeprefix("refs/heads/")
    tracking_ref = f"refs/remotes/origin/{remote_branch}"
    upstream_commit = git(
        repository, "rev-parse", "--verify", tracking_ref, check=False
    )
    publish = not upstream_commit or upstream_commit != head
    if upstream_commit:
        counts = git(
            repository,
            "rev-list",
            "--left-right",
            "--count",
            f"{tracking_ref}...HEAD",
        ).split()
        if len(counts) != 2:
            raise LockError(f"{repository}: cannot classify upstream divergence")
        behind, _ahead = (int(value) for value in counts)
        if behind != 0:
            raise LockError(
                f"{repository}: candidate is behind {tracking_ref} by {behind} commit(s)"
            )
    return {
        "url": expected_url,
        "path": logical_path,
        "type": kind,
        "commit": head,
        "branch": branch,
        "remote_ref": remote_ref,
        "upstream_commit": upstream_commit,
        "publish": publish,
    }


def read_candidate(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LockError(f"cannot read workspace candidate {path}: {error}") from error
    required = {
        "schema",
        "candidate_id",
        "manifest_sha256",
        "candidate_lock",
        "candidate_lock_sha256",
        "candidate_stack_contract",
        "candidate_stack_contract_sha256",
        "scripts",
        "repositories",
        "validation",
        "does_not_prove",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise LockError(f"{path}: invalid workspace candidate fields")
    if document.get("schema") != CANDIDATE_SCHEMA:
        raise LockError(f"{path}: expected schema {CANDIDATE_SCHEMA}")
    candidate_id = document.get("candidate_id")
    if candidate_id != f"sha256:{document_sha256(document)}":
        raise LockError(f"{path}: candidate identity does not match its admitted bytes")
    if SHA256_PATTERN.fullmatch(
        str(document.get("candidate_stack_contract_sha256", ""))
    ) is None:
        raise LockError(f"{path}: candidate stack contract digest is invalid")
    repositories = document.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise LockError(f"{path}: repositories must be a non-empty array")
    scripts = document.get("scripts")
    row_fields = {
        "url",
        "path",
        "type",
        "commit",
        "branch",
        "remote_ref",
        "upstream_commit",
        "publish",
    }
    rows = [scripts, *repositories]
    seen_paths: set[str] = set()
    for index, row in enumerate(rows):
        label = "scripts" if index == 0 else f"repository row {index - 1}"
        if not isinstance(row, dict) or set(row) != row_fields:
            raise LockError(f"{path}: {label} has invalid fields")
        string_fields = row_fields - {"publish"}
        if any(not isinstance(row.get(key), str) for key in string_fields):
            raise LockError(f"{path}: {label} has a non-string identity field")
        if not isinstance(row.get("publish"), bool):
            raise LockError(f"{path}: {label} publish must be boolean")
        commit = row["commit"]
        if commit and COMMIT_PATTERN.fullmatch(commit) is None:
            raise LockError(f"{path}: {label} commit is invalid")
        upstream_commit = row["upstream_commit"]
        if upstream_commit and COMMIT_PATTERN.fullmatch(upstream_commit) is None:
            raise LockError(f"{path}: {label} upstream commit is invalid")
        if row["branch"] and not row["remote_ref"].startswith("refs/heads/"):
            raise LockError(f"{path}: {label} remote ref is invalid")
        if row["path"] in seen_paths:
            raise LockError(f"{path}: duplicate candidate path {row['path']!r}")
        seen_paths.add(row["path"])
    if scripts["path"] != "." or scripts["type"] != "scripts":
        raise LockError(f"{path}: scripts row identity is invalid")
    validation = document.get("validation")
    if (
        not isinstance(validation, dict)
        or set(validation) != {"status", "evidence"}
        or validation.get("status") not in {
        "passed",
        "bypassed",
        }
    ):
        raise LockError(f"{path}: validation status is invalid")
    return document


def require_clean_acceptance_receipt(path: Path, expected_lock_sha256: str) -> None:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LockError(f"acceptance evidence is unreadable: {path}: {error}") from error
    workspace_lock = receipt.get("workspace_lock") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "p101-check-graph-receipt-v2"
        or receipt.get("outcome") != "clean"
        or not isinstance(workspace_lock, dict)
        or workspace_lock.get("valid") is not True
        or workspace_lock.get("lock_sha256") != expected_lock_sha256
    ):
        raise LockError(
            f"acceptance evidence is not a clean, digest-valid receipt for the "
            f"candidate lock: {path}"
        )
    require_receipt_digest(receipt, path)


def candidate_lock_path(candidate_path: Path, document: dict[str, Any]) -> Path:
    relative = document.get("candidate_lock")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise LockError(f"{candidate_path}: candidate_lock must be relative")
    result = (candidate_path.parent / relative).resolve()
    root = candidate_path.parent.resolve()
    if result != root and root not in result.parents:
        raise LockError(f"{candidate_path}: candidate_lock escapes its evidence directory")
    if not result.is_file() or sha256(result) != document.get("candidate_lock_sha256"):
        raise LockError(f"{candidate_path}: candidate lock is missing or changed")
    return result


def candidate_stack_contract_path(
    candidate_path: Path, document: dict[str, Any]
) -> Path:
    relative = document.get("candidate_stack_contract")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise LockError(f"{candidate_path}: candidate_stack_contract must be relative")
    result = (candidate_path.parent / relative).resolve()
    root = candidate_path.parent.resolve()
    if result != root and root not in result.parents:
        raise LockError(
            f"{candidate_path}: candidate_stack_contract escapes its evidence directory"
        )
    if (
        not result.is_file()
        or sha256(result) != document.get("candidate_stack_contract_sha256")
    ):
        raise LockError(f"{candidate_path}: candidate stack contract is missing or changed")
    return result


def verify_candidate_evidence(candidate_path: Path, document: dict[str, Any]) -> None:
    validation = document["validation"]
    evidence = validation.get("evidence", [])
    if not isinstance(evidence, list):
        raise LockError(f"{candidate_path}: validation evidence must be an array")
    acceptance_count = sum(
        isinstance(row, dict) and row.get("role") == "acceptance"
        for row in evidence
    )
    if validation["status"] == "passed" and acceptance_count != 1:
        raise LockError(
            f"{candidate_path}: passed candidate requires exactly one acceptance receipt"
        )
    if validation["status"] == "bypassed" and evidence:
        raise LockError(f"{candidate_path}: bypassed candidate cannot claim evidence")
    for index, row in enumerate(evidence):
        if not isinstance(row, dict) or set(row) != {"role", "path", "sha256"}:
            raise LockError(f"{candidate_path}: invalid evidence row {index}")
        relative = row.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise LockError(f"{candidate_path}: evidence path {index} must be relative")
        evidence_path = (candidate_path.parent / relative).resolve()
        root = candidate_path.parent.resolve()
        if evidence_path != root and root not in evidence_path.parents:
            raise LockError(f"{candidate_path}: evidence path {index} escapes its directory")
        if not evidence_path.is_file() or sha256(evidence_path) != row.get("sha256"):
            raise LockError(
                f"{candidate_path}: validation evidence {relative} is missing or changed"
            )
        if row.get("role") == "acceptance":
            require_clean_acceptance_receipt(
                evidence_path, str(document["candidate_lock_sha256"])
            )


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
            allow_ahead=args.allow_ahead,
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


def create_candidate(args: argparse.Namespace) -> int:
    locked = read_lock(args.lock, args.manifest)
    validate_repository_paths(args.scripts_root, locked)
    if args.receipt.parent.resolve() != args.lock.parent.resolve():
        raise LockError(
            "candidate receipt and candidate lock must share an evidence directory"
        )
    if args.candidate_stack_contract is None:
        raise LockError("candidate creation requires --candidate-stack-contract")
    candidate_stack_contract = args.candidate_stack_contract.resolve()
    if not candidate_stack_contract.is_file():
        raise LockError(
            f"candidate stack contract does not exist: {candidate_stack_contract}"
        )
    repositories: list[dict[str, Any]] = []
    for entry in locked:
        repository = repository_root(args.scripts_root, entry)
        repositories.append(
            publish_state(
                repository,
                expected_url=entry.url,
                expected_commit=entry.commit,
                logical_path=entry.path,
                kind=entry.kind,
            )
        )

    scripts_url = git(args.scripts_root, "remote", "get-url", "origin")
    scripts_commit = git(args.scripts_root, "rev-parse", "--verify", "HEAD")
    scripts = publish_state(
        args.scripts_root,
        expected_url=scripts_url,
        expected_commit=scripts_commit,
        logical_path=".",
        kind="scripts",
    )
    evidence_rows: list[dict[str, str]] = []
    if args.acceptance_receipt is not None:
        acceptance_receipt = args.acceptance_receipt.resolve()
        if not acceptance_receipt.is_file():
            raise LockError(
                f"candidate acceptance receipt does not exist: {acceptance_receipt}"
            )
        require_clean_acceptance_receipt(acceptance_receipt, sha256(args.lock))
        evidence_rows.append(
            {
                "role": "acceptance",
                "path": relative_evidence_path(args.receipt, acceptance_receipt),
                "sha256": sha256(acceptance_receipt),
            }
        )
    for evidence in args.evidence:
        evidence = evidence.resolve()
        if not evidence.is_file():
            raise LockError(f"candidate evidence does not exist: {evidence}")
        evidence_rows.append(
            {
                "role": "supporting",
                "path": relative_evidence_path(args.receipt, evidence),
                "sha256": sha256(evidence),
            }
        )
    if args.bypass_validation and evidence_rows:
        raise LockError("a bypassed candidate cannot claim validation evidence")
    if not args.bypass_validation and args.acceptance_receipt is None:
        raise LockError("a validated candidate requires an acceptance receipt")
    document: dict[str, Any] = {
        "schema": CANDIDATE_SCHEMA,
        "candidate_id": "",
        "manifest_sha256": sha256(args.manifest),
        "candidate_lock": relative_evidence_path(args.receipt, args.lock),
        "candidate_lock_sha256": sha256(args.lock),
        "candidate_stack_contract": relative_evidence_path(
            args.receipt, candidate_stack_contract
        ),
        "candidate_stack_contract_sha256": sha256(candidate_stack_contract),
        "scripts": scripts,
        "repositories": repositories,
        "validation": {
            "status": "bypassed" if args.bypass_validation else "passed",
            "evidence": evidence_rows,
        },
        "does_not_prove": (
            "Cross-repository rollback or atomic remote mutation. Publication pushes "
            "the admitted commits exactly and is resumable, but Git hosting does not "
            "provide one transaction spanning independent repositories."
        ),
    }
    document["candidate_id"] = f"sha256:{document_sha256(document)}"
    write_json(args.receipt, document)
    print(f"workspace candidate: {document['candidate_id']}")
    print(
        "repositories selected for publication: "
        f"{sum(bool(row['publish']) for row in repositories)}"
    )
    print(f"validation: {document['validation']['status']}")
    print(f"receipt: {args.receipt}")
    return 0


def verify_candidate(args: argparse.Namespace) -> int:
    document = read_candidate(args.candidate)
    lock_path = candidate_lock_path(args.candidate, document)
    candidate_stack_contract_path(args.candidate, document)
    verify_candidate_evidence(args.candidate, document)
    if document["manifest_sha256"] != sha256(args.manifest):
        raise LockError(f"{args.candidate}: repository manifest changed")
    locked = read_lock(lock_path, args.manifest)
    locked_by_path = {entry.path: entry for entry in locked}
    rows = document["repositories"]
    if len(rows) != len(locked):
        raise LockError(f"{args.candidate}: repository count differs from its lock")
    for row in rows:
        if not isinstance(row, dict):
            raise LockError(f"{args.candidate}: repository row is not an object")
        path = row.get("path")
        entry = locked_by_path.get(path)
        if entry is None or row.get("commit") != entry.commit:
            raise LockError(f"{args.candidate}: repository row {path!r} differs from its lock")
        observed = publish_state(
            repository_root(args.scripts_root, entry),
            expected_url=entry.url,
            expected_commit=entry.commit,
            logical_path=entry.path,
            kind=entry.kind,
        )
        stable_keys = ("url", "path", "type", "commit", "branch", "remote_ref")
        stable_matches = all(observed.get(key) == row.get(key) for key in stable_keys)
        publication_matches = (
            observed.get("upstream_commit") == row.get("upstream_commit")
            and observed.get("publish") == row.get("publish")
        ) or (
            bool(row.get("publish"))
            and observed.get("upstream_commit") == row.get("commit")
            and observed.get("publish") is False
        )
        if not stable_matches or not publication_matches:
            raise LockError(
                f"{args.candidate}: publication state changed for {entry.path}"
            )
    scripts = document["scripts"]
    current_scripts_commit = git(args.scripts_root, "rev-parse", "--verify", "HEAD")
    scripts_descendant_verified = (
        args.allow_scripts_descendant
        and current_scripts_commit != scripts.get("commit")
    )
    if scripts_descendant_verified:
        if git(
            args.scripts_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=normal",
        ):
            raise LockError("scripts worktree is not clean while resuming candidate")
        if not git_succeeds(
            args.scripts_root,
            "merge-base",
            "--is-ancestor",
            str(scripts.get("commit", "")),
            current_scripts_commit,
        ):
            raise LockError("scripts no longer descends from the admitted candidate")
        changed_paths = set(
            git(
                args.scripts_root,
                "diff",
                "--name-only",
                f"{scripts['commit']}..{current_scripts_commit}",
            ).splitlines()
        )
        admitted_completion_paths = {
            "repos.lock",
            "contracts/p101-stack-contract.json",
        }
        if not changed_paths.issubset(admitted_completion_paths):
            raise LockError(
                "scripts descendant contains changes outside the transaction "
                "completion artifacts"
            )
    else:
        observed_scripts = publish_state(
            args.scripts_root,
            expected_url=str(scripts.get("url", "")),
            expected_commit=str(scripts.get("commit", "")),
            logical_path=".",
            kind="scripts",
        )
        stable_keys = ("url", "path", "type", "commit", "branch", "remote_ref")
        stable_matches = all(
            observed_scripts.get(key) == scripts.get(key) for key in stable_keys
        )
        publication_matches = (
            observed_scripts.get("upstream_commit") == scripts.get("upstream_commit")
            and observed_scripts.get("publish") == scripts.get("publish")
        ) or (
            bool(scripts.get("publish"))
            and observed_scripts.get("upstream_commit") == scripts.get("commit")
            and observed_scripts.get("publish") is False
        )
        if not stable_matches or not publication_matches:
            raise LockError(f"{args.candidate}: scripts publication state changed")
    print(f"workspace candidate verified: {document['candidate_id']}")
    print(f"validation: {document['validation']['status']}")
    return 0


def candidate_entries(args: argparse.Namespace) -> int:
    document = read_candidate(args.candidate)
    candidate_lock_path(args.candidate, document)
    candidate_stack_contract_path(args.candidate, document)
    verify_candidate_evidence(args.candidate, document)
    for row in document["repositories"]:
        if not row.get("publish"):
            continue
        print(
            "|".join(
                str(row[key])
                for key in (
                    "path",
                    "commit",
                    "branch",
                    "remote_ref",
                    "upstream_commit",
                )
            )
        )
    return 0


def candidate_status(args: argparse.Namespace) -> int:
    document = read_candidate(args.candidate)
    candidate_lock_path(args.candidate, document)
    candidate_stack_contract_path(args.candidate, document)
    verify_candidate_evidence(args.candidate, document)
    print(document["validation"]["status"])
    return 0


def candidate_scripts(args: argparse.Namespace) -> int:
    document = read_candidate(args.candidate)
    candidate_lock_path(args.candidate, document)
    candidate_stack_contract_path(args.candidate, document)
    verify_candidate_evidence(args.candidate, document)
    scripts = document["scripts"]
    print(
        "|".join(
            str(scripts[key])
            for key in ("commit", "branch", "remote_ref", "upstream_commit")
        )
    )
    return 0


def qualification_ref_for(candidate_id: str) -> str:
    prefix = "sha256:"
    digest = candidate_id.removeprefix(prefix)
    if not candidate_id.startswith(prefix) or SHA256_PATTERN.fullmatch(digest) is None:
        raise LockError("candidate identity is not a canonical SHA-256 identity")
    return f"refs/heads/p101-candidate/{digest}"


def candidate_qualification(args: argparse.Namespace) -> int:
    document = read_candidate(args.candidate)
    candidate_lock_path(args.candidate, document)
    candidate_stack_contract_path(args.candidate, document)
    verify_candidate_evidence(args.candidate, document)
    print(
        "|".join(
            (
                str(document["candidate_id"]),
                str(document["candidate_lock_sha256"]),
                str(document["candidate_stack_contract_sha256"]),
                qualification_ref_for(str(document["candidate_id"])),
            )
        )
    )
    return 0


def read_json_document(path: Path, description: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LockError(f"cannot read {description} {path}: {error}") from error
    if not isinstance(document, dict):
        raise LockError(f"{path}: {description} must be a JSON object")
    return document


def validate_qualification_coordinates(
    *,
    candidate_id: str,
    candidate_lock_sha256: str,
    candidate_stack_contract_sha256: str,
    qualification_ref: str,
    scripts_commit: str,
) -> None:
    expected_ref = qualification_ref_for(candidate_id)
    if candidate_lock_sha256.startswith("sha256:"):
        raise LockError("candidate lock digest must not include a sha256: prefix")
    if SHA256_PATTERN.fullmatch(candidate_lock_sha256) is None:
        raise LockError("candidate lock digest is not a canonical SHA-256 digest")
    if SHA256_PATTERN.fullmatch(candidate_stack_contract_sha256) is None:
        raise LockError(
            "candidate stack contract digest is not a canonical SHA-256 digest"
        )
    if qualification_ref != expected_ref:
        raise LockError(
            f"qualification ref {qualification_ref!r} does not match {expected_ref!r}"
        )
    if COMMIT_PATTERN.fullmatch(scripts_commit) is None:
        raise LockError("qualification scripts commit is not a canonical Git commit")


def write_platform_qualification(args: argparse.Namespace) -> int:
    validate_qualification_coordinates(
        candidate_id=args.candidate_id,
        candidate_lock_sha256=args.candidate_lock_sha256,
        candidate_stack_contract_sha256=args.candidate_stack_contract_sha256,
        qualification_ref=args.qualification_ref,
        scripts_commit=args.scripts_commit,
    )
    if args.platform not in SUPPORTED_QUALIFICATION_PLATFORMS:
        raise LockError(f"unsupported qualification platform: {args.platform}")
    if not args.github_run_id.isdigit() or not args.github_run_attempt.isdigit():
        raise LockError("GitHub run identity must contain decimal digits")
    if not args.github_repository or "/" not in args.github_repository:
        raise LockError("GitHub repository identity must be owner/name")
    require_clean_acceptance_receipt(
        args.acceptance_receipt, args.candidate_lock_sha256
    )
    acceptance = read_json_document(args.acceptance_receipt, "acceptance receipt")
    host = acceptance.get("host")
    host_system = host.get("system") if isinstance(host, dict) else None
    platform_for_system = {
        "Linux": "linux",
        "Darwin": "macos",
        "FreeBSD": "freebsd",
    }
    if platform_for_system.get(str(host_system)) != args.platform:
        raise LockError(
            f"acceptance host {host_system!r} does not match platform {args.platform!r}"
        )
    acceptance_stack = acceptance.get("stack_contract")
    if (
        not isinstance(acceptance_stack, dict)
        or acceptance_stack.get("valid") is not True
        or acceptance_stack.get("contract_sha256")
        != args.candidate_stack_contract_sha256
    ):
        raise LockError(
            "acceptance receipt did not validate the candidate stack contract"
        )
    document: dict[str, Any] = {
        "schema": PLATFORM_QUALIFICATION_SCHEMA,
        "candidate_id": args.candidate_id,
        "candidate_lock_sha256": args.candidate_lock_sha256,
        "candidate_stack_contract_sha256": args.candidate_stack_contract_sha256,
        "qualification_ref": args.qualification_ref,
        "scripts_qualification_commit": args.scripts_commit,
        "platform": args.platform,
        "github_repository": args.github_repository,
        "github_run_id": args.github_run_id,
        "github_run_attempt": args.github_run_attempt,
        "acceptance_receipt_sha256": sha256(args.acceptance_receipt),
        "acceptance_receipt_digest": acceptance["receipt_digest"],
        "outcome": "clean",
        "does_not_prove": (
            "Behavior outside the governed graph, undeclared platform inputs, "
            "or atomic publication across independent repositories."
        ),
    }
    document["receipt_digest"] = receipt_digest(document)
    write_json(args.receipt, document)
    print(
        f"workspace candidate platform qualification: {args.platform} clean"
    )
    print(f"receipt: {args.receipt}")
    return 0


def validate_platform_qualification_document(
    document: dict[str, Any], description: Path | str
) -> None:
    expected_fields = {
        "schema",
        "candidate_id",
        "candidate_lock_sha256",
        "candidate_stack_contract_sha256",
        "qualification_ref",
        "scripts_qualification_commit",
        "platform",
        "github_repository",
        "github_run_id",
        "github_run_attempt",
        "acceptance_receipt_sha256",
        "acceptance_receipt_digest",
        "outcome",
        "does_not_prove",
        "receipt_digest",
    }
    if set(document) != expected_fields:
        raise LockError(f"{description}: invalid platform qualification fields")
    if document.get("schema") != PLATFORM_QUALIFICATION_SCHEMA:
        raise LockError(f"{description}: unsupported platform qualification schema")
    if document.get("outcome") != "clean":
        raise LockError(f"{description}: platform qualification is not clean")
    platform = document.get("platform")
    if platform not in SUPPORTED_QUALIFICATION_PLATFORMS:
        raise LockError(f"{description}: unsupported qualification platform")
    if (
        not str(document.get("github_run_id", "")).isdigit()
        or not str(document.get("github_run_attempt", "")).isdigit()
        or "/" not in str(document.get("github_repository", ""))
    ):
        raise LockError(f"{description}: invalid GitHub run identity")
    for key in ("acceptance_receipt_sha256",):
        if SHA256_PATTERN.fullmatch(str(document.get(key, ""))) is None:
            raise LockError(f"{description}: invalid {key}")
    acceptance_digest = str(document.get("acceptance_receipt_digest", ""))
    if not acceptance_digest.startswith("sha256:") or SHA256_PATTERN.fullmatch(
        acceptance_digest.removeprefix("sha256:")
    ) is None:
        raise LockError(f"{description}: invalid acceptance receipt digest")
    validate_qualification_coordinates(
        candidate_id=str(document.get("candidate_id", "")),
        candidate_lock_sha256=str(document.get("candidate_lock_sha256", "")),
        candidate_stack_contract_sha256=str(
            document.get("candidate_stack_contract_sha256", "")
        ),
        qualification_ref=str(document.get("qualification_ref", "")),
        scripts_commit=str(document.get("scripts_qualification_commit", "")),
    )
    require_receipt_digest(document, description)


def read_platform_qualification(path: Path) -> dict[str, Any]:
    document = read_json_document(path, "platform qualification receipt")
    validate_platform_qualification_document(document, path)
    return document


def aggregate_qualification(args: argparse.Namespace) -> int:
    validate_qualification_coordinates(
        candidate_id=args.candidate_id,
        candidate_lock_sha256=args.candidate_lock_sha256,
        candidate_stack_contract_sha256=args.candidate_stack_contract_sha256,
        qualification_ref=args.qualification_ref,
        scripts_commit=args.scripts_commit,
    )
    required_platforms = set(args.require_platform)
    if not required_platforms:
        required_platforms = set(SUPPORTED_QUALIFICATION_PLATFORMS)
    invalid_platforms = required_platforms - SUPPORTED_QUALIFICATION_PLATFORMS
    if invalid_platforms:
        raise LockError(
            "unsupported required qualification platform(s): "
            + ", ".join(sorted(invalid_platforms))
        )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    github_run_identity: tuple[str, str, str] | None = None
    for path in args.platform_receipt:
        document = read_platform_qualification(path)
        coordinates = (
            document["candidate_id"],
            document["candidate_lock_sha256"],
            document["candidate_stack_contract_sha256"],
            document["qualification_ref"],
            document["scripts_qualification_commit"],
        )
        expected = (
            args.candidate_id,
            args.candidate_lock_sha256,
            args.candidate_stack_contract_sha256,
            args.qualification_ref,
            args.scripts_commit,
        )
        if coordinates != expected:
            raise LockError(f"{path}: platform receipt belongs to another candidate")
        observed_run_identity = (
            str(document["github_repository"]),
            str(document["github_run_id"]),
            str(document["github_run_attempt"]),
        )
        if github_run_identity is None:
            github_run_identity = observed_run_identity
        elif github_run_identity != observed_run_identity:
            raise LockError(f"{path}: platform receipt belongs to another workflow run")
        platform = str(document["platform"])
        if platform in seen:
            raise LockError(f"duplicate platform qualification: {platform}")
        seen.add(platform)
        rows.append({"platform": platform, "receipt": document})
    if seen != required_platforms:
        missing = sorted(required_platforms - seen)
        extra = sorted(seen - required_platforms)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise LockError("platform qualification set is incomplete: " + "; ".join(details))
    rows.sort(key=lambda row: row["platform"])
    if github_run_identity is None:
        raise LockError("platform qualification set is empty")
    aggregate: dict[str, Any] = {
        "schema": QUALIFICATION_SCHEMA,
        "candidate_id": args.candidate_id,
        "candidate_lock_sha256": args.candidate_lock_sha256,
        "candidate_stack_contract_sha256": args.candidate_stack_contract_sha256,
        "qualification_ref": args.qualification_ref,
        "scripts_qualification_commit": args.scripts_commit,
        "github_repository": github_run_identity[0],
        "github_run_id": github_run_identity[1],
        "github_run_attempt": github_run_identity[2],
        "platforms": sorted(required_platforms),
        "platform_receipts": rows,
        "outcome": "clean",
        "does_not_prove": (
            "Atomic publication across repositories or behavior outside the "
            "three governed platform runs."
        ),
    }
    aggregate["receipt_digest"] = receipt_digest(aggregate)
    write_json(args.receipt, aggregate)
    print(
        "workspace candidate qualification: clean on "
        + ", ".join(sorted(required_platforms))
    )
    print(f"receipt: {args.receipt}")
    return 0


def read_qualification(path: Path) -> dict[str, Any]:
    document = read_json_document(path, "workspace qualification receipt")
    expected_fields = {
        "schema",
        "candidate_id",
        "candidate_lock_sha256",
        "candidate_stack_contract_sha256",
        "qualification_ref",
        "scripts_qualification_commit",
        "github_repository",
        "github_run_id",
        "github_run_attempt",
        "platforms",
        "platform_receipts",
        "outcome",
        "does_not_prove",
        "receipt_digest",
    }
    if set(document) != expected_fields:
        raise LockError(f"{path}: invalid workspace qualification fields")
    if document.get("schema") != QUALIFICATION_SCHEMA:
        raise LockError(f"{path}: unsupported workspace qualification schema")
    if document.get("outcome") != "clean":
        raise LockError(f"{path}: workspace qualification is not clean")
    if (
        not str(document.get("github_run_id", "")).isdigit()
        or not str(document.get("github_run_attempt", "")).isdigit()
        or "/" not in str(document.get("github_repository", ""))
    ):
        raise LockError(f"{path}: invalid GitHub run identity")
    validate_qualification_coordinates(
        candidate_id=str(document.get("candidate_id", "")),
        candidate_lock_sha256=str(document.get("candidate_lock_sha256", "")),
        candidate_stack_contract_sha256=str(
            document.get("candidate_stack_contract_sha256", "")
        ),
        qualification_ref=str(document.get("qualification_ref", "")),
        scripts_commit=str(document.get("scripts_qualification_commit", "")),
    )
    platforms = document.get("platforms")
    rows = document.get("platform_receipts")
    if platforms != sorted(SUPPORTED_QUALIFICATION_PLATFORMS):
        raise LockError(f"{path}: qualification does not cover all supported platforms")
    if not isinstance(rows, list) or len(rows) != len(platforms):
        raise LockError(f"{path}: invalid platform qualification rows")
    row_platforms: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"platform", "receipt"}:
            raise LockError(f"{path}: invalid platform qualification row")
        platform = str(row.get("platform", ""))
        row_platforms.append(platform)
        embedded = row.get("receipt")
        if not isinstance(embedded, dict):
            raise LockError(f"{path}: embedded platform receipt is not an object")
        validate_platform_qualification_document(
            embedded, f"{path}: embedded {platform} receipt"
        )
        if embedded.get("platform") != platform:
            raise LockError(f"{path}: embedded platform receipt label differs")
        aggregate_coordinates = (
            document["candidate_id"],
            document["candidate_lock_sha256"],
            document["candidate_stack_contract_sha256"],
            document["qualification_ref"],
            document["scripts_qualification_commit"],
            document["github_repository"],
            document["github_run_id"],
            document["github_run_attempt"],
        )
        embedded_coordinates = (
            embedded["candidate_id"],
            embedded["candidate_lock_sha256"],
            embedded["candidate_stack_contract_sha256"],
            embedded["qualification_ref"],
            embedded["scripts_qualification_commit"],
            embedded["github_repository"],
            embedded["github_run_id"],
            embedded["github_run_attempt"],
        )
        if embedded_coordinates != aggregate_coordinates:
            raise LockError(f"{path}: embedded platform receipt coordinates differ")
    if row_platforms != platforms:
        raise LockError(f"{path}: platform qualification rows are not canonical")
    require_receipt_digest(document, path)
    return document


def verify_qualification(args: argparse.Namespace) -> int:
    candidate = read_candidate(args.candidate)
    candidate_lock_path(args.candidate, candidate)
    candidate_stack_contract_path(args.candidate, candidate)
    verify_candidate_evidence(args.candidate, candidate)
    qualification = read_qualification(args.qualification)
    if qualification["candidate_id"] != candidate["candidate_id"]:
        raise LockError("qualification belongs to another workspace candidate")
    if qualification["candidate_lock_sha256"] != candidate["candidate_lock_sha256"]:
        raise LockError("qualification used another candidate lock")
    if (
        qualification["candidate_stack_contract_sha256"]
        != candidate["candidate_stack_contract_sha256"]
    ):
        raise LockError("qualification used another candidate stack contract")
    expected_ref = qualification_ref_for(str(candidate["candidate_id"]))
    if qualification["qualification_ref"] != expected_ref:
        raise LockError("qualification used another candidate ref")
    if (
        args.scripts_commit is not None
        and qualification["scripts_qualification_commit"] != args.scripts_commit
    ):
        raise LockError("qualification used another scripts qualification commit")
    if (
        args.github_run_id is not None
        and qualification["github_run_id"] != args.github_run_id
    ):
        raise LockError("qualification belongs to another GitHub Actions run")
    print(f"workspace candidate qualified: {candidate['candidate_id']}")
    print("platforms: freebsd, linux, macos")
    return 0


def complete_candidate(args: argparse.Namespace) -> int:
    document = read_candidate(args.candidate)
    candidate_lock = candidate_lock_path(args.candidate, document)
    candidate_stack_contract = candidate_stack_contract_path(
        args.candidate, document
    )
    verify_candidate_evidence(args.candidate, document)
    qualification_digest = ""
    if document["validation"]["status"] == "passed":
        if args.qualification is None:
            raise LockError("validated candidate completion requires qualification")
        qualification = read_qualification(args.qualification)
        if qualification["candidate_id"] != document["candidate_id"]:
            raise LockError("completion qualification belongs to another candidate")
        if (
            qualification["candidate_lock_sha256"]
            != document["candidate_lock_sha256"]
        ):
            raise LockError("completion qualification used another candidate lock")
        qualification_digest = str(qualification["receipt_digest"])
    elif args.qualification is not None:
        raise LockError("bypassed candidate cannot claim platform qualification")
    if sha256(args.lock) != sha256(candidate_lock):
        raise LockError("published repos.lock does not match the validated candidate lock")
    if not args.stack_contract.is_file():
        raise LockError(f"stack contract does not exist: {args.stack_contract}")
    if sha256(args.stack_contract) != sha256(candidate_stack_contract):
        raise LockError(
            "published stack contract does not match the validated candidate contract"
        )
    locked = read_lock(args.lock, args.manifest)
    rows_by_path = {str(row["path"]): row for row in document["repositories"]}
    for entry in locked:
        repository = repository_root(args.scripts_root, entry)
        row = rows_by_path.get(entry.path)
        if row is None or row.get("commit") != entry.commit:
            raise LockError(f"completion candidate is missing {entry.path}")
        inspected = inspect_repository(
            args.scripts_root,
            entry,
            expected_commit=entry.commit,
            require_clean=True,
            require_upstream=True,
        )
        if inspected["upstream"] == "" and entry.commit:
            raise LockError(f"{entry.path}: published commit has no upstream")
    scripts = document["scripts"]
    final_scripts_commit = git(args.scripts_root, "rev-parse", "--verify", "HEAD")
    if git(args.scripts_root, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise LockError("scripts worktree is not clean at transaction completion")
    if not git_succeeds(
        args.scripts_root,
        "merge-base",
        "--is-ancestor",
        str(scripts["commit"]),
        final_scripts_commit,
    ):
        raise LockError("final scripts commit does not descend from the admitted candidate")
    scripts_upstream = git(
        args.scripts_root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    if git(args.scripts_root, "rev-parse", scripts_upstream) != final_scripts_commit:
        raise LockError("final scripts commit is not published to its upstream")
    completion = {
        "schema": CANDIDATE_COMPLETION_SCHEMA,
        "candidate_id": document["candidate_id"],
        "final_scripts_commit": final_scripts_commit,
        "repos_lock_sha256": sha256(args.lock),
        "stack_contract_sha256": sha256(args.stack_contract),
        "repository_count": len(locked),
        "published_repository_count": sum(
            bool(row["publish"]) for row in document["repositories"]
        ),
        "qualification_receipt_digest": qualification_digest,
        "passed": True,
        "does_not_prove": document["does_not_prove"],
    }
    write_json(args.receipt, completion)
    print(f"workspace candidate completed: {document['candidate_id']}")
    print(f"receipt: {args.receipt}")
    return 0


def parser() -> argparse.ArgumentParser:
    scripts_root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(
        description=(
            "Manage exact workspace revisions, preflight candidates, and "
            "publication completion receipts."
        )
    )
    result.add_argument("--scripts-root", type=Path, default=scripts_root)
    result.add_argument("--manifest", type=Path, default=scripts_root / "repos.txt")
    result.add_argument(
        "--lock",
        type=Path,
        default=Path(
            os.environ.get("P101_REPOS_LOCK", os.fspath(scripts_root / "repos.lock"))
        ),
    )
    subparsers = result.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser(
        "refresh", help="write a deterministic lock from upstream-synchronized HEADs"
    )
    refresh_parser.add_argument(
        "--require-clean",
        action="store_true",
        help="also refuse generated or other worktree changes",
    )
    refresh_parser.add_argument(
        "--allow-ahead",
        action="store_true",
        help=(
            "admit clean commits ahead of configured upstreams; intended only "
            "for an ephemeral pre-push verification lock"
        ),
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

    candidate_parser = subparsers.add_parser(
        "candidate", help="bind clean local revisions to validation evidence"
    )
    candidate_parser.add_argument("--receipt", type=Path, required=True)
    candidate_parser.add_argument(
        "--candidate-stack-contract", type=Path, required=True
    )
    candidate_parser.add_argument("--acceptance-receipt", type=Path)
    candidate_parser.add_argument(
        "--evidence",
        type=Path,
        action="append",
        default=[],
        help="validation evidence file inside the candidate receipt directory",
    )
    candidate_parser.add_argument(
        "--bypass-validation",
        action="store_true",
        help="record an explicit emergency validation bypass",
    )
    candidate_parser.set_defaults(function=create_candidate)

    verify_candidate_parser = subparsers.add_parser(
        "verify-candidate", help="verify the immutable candidate and local revisions"
    )
    verify_candidate_parser.add_argument("--candidate", type=Path, required=True)
    verify_candidate_parser.add_argument(
        "--allow-scripts-descendant",
        action="store_true",
        help="admit only the derived lock/stack-contract completion commit",
    )
    verify_candidate_parser.set_defaults(function=verify_candidate)

    candidate_entries_parser = subparsers.add_parser(
        "candidate-entries", help="emit exact publication rows from a candidate"
    )
    candidate_entries_parser.add_argument("--candidate", type=Path, required=True)
    candidate_entries_parser.set_defaults(function=candidate_entries)

    candidate_status_parser = subparsers.add_parser(
        "candidate-status", help="emit passed or bypassed for a candidate"
    )
    candidate_status_parser.add_argument("--candidate", type=Path, required=True)
    candidate_status_parser.set_defaults(function=candidate_status)

    candidate_scripts_parser = subparsers.add_parser(
        "candidate-scripts", help="emit the admitted scripts publication row"
    )
    candidate_scripts_parser.add_argument("--candidate", type=Path, required=True)
    candidate_scripts_parser.set_defaults(function=candidate_scripts)

    candidate_qualification_parser = subparsers.add_parser(
        "candidate-qualification",
        help="emit candidate identity, lock digest, and temporary qualification ref",
    )
    candidate_qualification_parser.add_argument(
        "--candidate", type=Path, required=True
    )
    candidate_qualification_parser.set_defaults(function=candidate_qualification)

    platform_qualification_parser = subparsers.add_parser(
        "platform-qualification",
        help="bind one clean platform acceptance receipt to a workspace candidate",
    )
    platform_qualification_parser.add_argument("--candidate-id", required=True)
    platform_qualification_parser.add_argument(
        "--candidate-lock-sha256", required=True
    )
    platform_qualification_parser.add_argument(
        "--candidate-stack-contract-sha256", required=True
    )
    platform_qualification_parser.add_argument("--qualification-ref", required=True)
    platform_qualification_parser.add_argument("--scripts-commit", required=True)
    platform_qualification_parser.add_argument(
        "--platform", choices=sorted(SUPPORTED_QUALIFICATION_PLATFORMS), required=True
    )
    platform_qualification_parser.add_argument("--github-repository", required=True)
    platform_qualification_parser.add_argument("--github-run-id", required=True)
    platform_qualification_parser.add_argument("--github-run-attempt", required=True)
    platform_qualification_parser.add_argument(
        "--acceptance-receipt", type=Path, required=True
    )
    platform_qualification_parser.add_argument("--receipt", type=Path, required=True)
    platform_qualification_parser.set_defaults(function=write_platform_qualification)

    aggregate_qualification_parser = subparsers.add_parser(
        "aggregate-qualification",
        help="admit one candidate-bound clean receipt from every supported platform",
    )
    aggregate_qualification_parser.add_argument("--candidate-id", required=True)
    aggregate_qualification_parser.add_argument(
        "--candidate-lock-sha256", required=True
    )
    aggregate_qualification_parser.add_argument(
        "--candidate-stack-contract-sha256", required=True
    )
    aggregate_qualification_parser.add_argument("--qualification-ref", required=True)
    aggregate_qualification_parser.add_argument("--scripts-commit", required=True)
    aggregate_qualification_parser.add_argument(
        "--platform-receipt", type=Path, action="append", default=[], required=True
    )
    aggregate_qualification_parser.add_argument(
        "--require-platform",
        action="append",
        default=[],
        choices=sorted(SUPPORTED_QUALIFICATION_PLATFORMS),
    )
    aggregate_qualification_parser.add_argument("--receipt", type=Path, required=True)
    aggregate_qualification_parser.set_defaults(function=aggregate_qualification)

    verify_qualification_parser = subparsers.add_parser(
        "verify-qualification",
        help="verify three-platform qualification for an immutable candidate",
    )
    verify_qualification_parser.add_argument("--candidate", type=Path, required=True)
    verify_qualification_parser.add_argument(
        "--qualification", type=Path, required=True
    )
    verify_qualification_parser.add_argument("--scripts-commit")
    verify_qualification_parser.add_argument("--github-run-id")
    verify_qualification_parser.set_defaults(function=verify_qualification)

    complete_candidate_parser = subparsers.add_parser(
        "complete-candidate", help="write the final atomic-publication receipt"
    )
    complete_candidate_parser.add_argument("--candidate", type=Path, required=True)
    complete_candidate_parser.add_argument("--receipt", type=Path, required=True)
    complete_candidate_parser.add_argument("--qualification", type=Path)
    complete_candidate_parser.add_argument(
        "--stack-contract",
        type=Path,
        default=scripts_root / "contracts" / "p101-stack-contract.json",
    )
    complete_candidate_parser.set_defaults(function=complete_candidate)
    return result


def main() -> int:
    args = parser().parse_args()
    args.scripts_root = args.scripts_root.resolve()
    args.manifest = args.manifest.resolve()
    args.lock = args.lock.resolve()
    for attribute in (
        "receipt",
        "candidate",
        "qualification",
        "stack_contract",
        "acceptance_receipt",
        "candidate_stack_contract",
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            setattr(args, attribute, value.resolve())
    if hasattr(args, "platform_receipt"):
        args.platform_receipt = [path.resolve() for path in args.platform_receipt]
    try:
        return int(args.function(args))
    except LockError as error:
        print(f"repository lock: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
