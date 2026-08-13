#!/usr/bin/env python3
"""Create and verify the digest-bound p101 stack policy contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "p101-stack-contract-v1"
RECEIPT_SCHEMA = "p101-stack-contract-receipt-v1"
DEFAULT_PATHS = (
    "repos.txt",
    "repos.lock",
    "clang-format-version.txt",
    "flag-selection.json",
    "flag-selection.standard.json",
    "CMakeLists.txt",
    "update-all.sh",
    "checks/format-workspace.sh",
    "cmake/p101_compile_db.c",
    "cmake/P101Install.cmake",
    "shared/compilers.sh",
    "shared/artifacts.sh",
    "cmake/P101Linking.cmake",
    "cmake/P101Summary.cmake",
    "cmake/ToolConfig.cmake",
    "check-after-update-all.sh",
    "checks/check-shell-scripts.sh",
    "checks/check-p101-boundaries.py",
    "checks/check-c-facts-external-corpus.sh",
    "checks/check-p101-instrumentation.py",
    "checks/check-p101-library-audit.sh",
    "checks/check-p101-quality-contract.py",
    "checks/check-p101-tool-audit.sh",
    "checks/check-workspace-public-api.sh",
    "checks/check-wrapper-conformance.py",
    "checks/check-wrapper-lifecycles.py",
    "checks/check-wrapper-unit-tests.py",
    "checks/p101-check-graph.py",
    "checks/p101-prime-semantic-cache.py",
    "checks/p101-semantic-snapshot.py",
    "runtime/clang_ast.py",
    "runtime/content_manifest.py",
    "runtime/semantic_usage.py",
    "runtime/p101-fault-campaign.py",
    "checks/p101_check_plan.py",
    "checks/p101_check_reporting.py",
    "contracts/p101-curriculum-domains.tsv",
    "contracts/instrumentation-contract.json",
    "contracts/p101-boundaries.json",
    "contracts/p101-check-graph.json",
    "contracts/p101-quality-contract.json",
    "contracts/p101-performance-budget.json",
    "contracts/p101-source-responsibilities.json",
    "contracts/p101-test-inventory.json",
    "contracts/p101-playground-tracks.json",
    "contracts/wrapper-conformance-contract.json",
    "contracts/wrapper-failure-contract.json",
    "contracts/wrapper-fault-semantics.json",
    "contracts/wrapper-lifecycle-contract.json",
    "contracts/wrapper-outcome-contract.json",
    "contracts/wrapper-native-smoke-contract.json",
    "contracts/wrapper-portable-input-contract.json",
    "contracts/wrapper-platform-faults.json",
    "docs/p101-tool-design-contract.md",
    "docs/bootstrap-architecture.md",
    "generators/generate-wrapper-unit-tests.py",
    "generators/analyze-lib-function-graph.py",
    "generators/generate-inspect-rule-catalog.py",
    "generators/refresh-wrapper-platform-faults.py",
    "runtime/wrapper_fault_contract.py",
    "runtime/c_facts.py",
    "github-actions/platform-sentinel.sh",
    "github-actions/preflight.sh",
    "github-actions/p101-stack.yml",
    "distribution/copy-scripts.sh",
    "shared/library/install.sh",
    "workspace/build-repo.sh",
    "workspace/build-lane.sh",
    "workspace/CMakeLists.txt",
    "workspace/filter-sanitizers.sh",
    "workspace/gc-build-cache.sh",
    "workspace/RunAcceptance.cmake",
    "workspace/VerifyAcceptancePerformance.cmake",
    "workspace/stack-contract.py",
    "workspace/update.sh",
    "workspace/WriteHostToolReceipt.cmake",
    "tests/test-build-repo-interactive.sh",
    "tests/test-build-lane.sh",
    "tests/test-copy-scripts-standalone.sh",
    "tests/test-github-actions-summary.sh",
    "tests/test-p101-check-graph.py",
    "tests/test-p101-semantic-prime.py",
    "tests/test-p101-semantic-snapshot.py",
    "tests/test-workspace-cmake.sh",
    "tests/test-audit-workspace.sh",
    "../libraries/lib_c_facts/include/p101_c_facts/analysis.h",
    "../libraries/lib_c_facts/include/p101_c_facts/compile_command.h",
    "../libraries/lib_c_facts/include/p101_c_facts/facts.h",
    "../libraries/lib_env/include/p101_env/env.h",
    "../libraries/lib_fsm/include/p101_fsm/fsm.h",
    "../libraries/lib_tool_event/include/p101_tool_event/event.h",
    "../libraries/lib_tool_event/include/p101_tool_event/analysis.h",
    "../libraries/lib_tool_event/include/p101_tool_event/lifecycle.h",
    "../libraries/lib_tool_event/include/p101_tool_event/model.h",
    "../libraries/lib_tool_event/include/p101_tool_event/receipt.h",
    "../programs/p101-inspect/src/rule_catalog.inc",
    "../libraries/lib_util/include/p101_util/tool_run.h",
    "../programs/p101-audit/test.sh",
    "../programs/p101-audit/config.cmake",
    "../programs/p101-audit/README.md",
    "../programs/p101-audit/include/workspace_analysis.h",
    "../programs/p101-audit/include/workspace_audit.h",
    "../programs/p101-audit/include/workspace_json.h",
    "../programs/p101-audit/components/workspace/src/common.c",
    "../programs/p101-audit/components/workspace/src/fault_semantics.c",
    "../programs/p101-audit/components/workspace/src/functional_layout.c",
    "../programs/p101-audit/components/workspace/src/json.c",
    "../programs/p101-audit/components/workspace/src/main.c",
    "../programs/p101-audit/components/workspace/src/native_parity.c",
    "../programs/p101-audit/components/workspace/src/source_responsibilities.c",
    "../programs/p101-audit/components/workspace/src/test_inventory.c",
    "../programs/p101-test/test.sh",
    "../playgrounds/lessons/manifest.json",
    "../templates/template-c/test.sh",
    "../templates/template-cxx/test.sh",
)
DOES_NOT_PROVE = (
    "This contract binds the declared stack policy bytes. It does not prove "
    "that the policy is complete, correct, or sufficient for an undeclared platform."
)


class ContractError(ValueError):
    """The stack contract or one of its admitted artifacts is invalid."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + sha256_bytes(encoded)


def admitted_path(scripts_root: Path, relative_text: str) -> Path:
    relative = Path(relative_text)
    if relative.is_absolute() or not relative_text or "\\" in relative_text:
        raise ContractError(f"unsafe artifact path {relative_text!r}")
    workspace_root = scripts_root.parent.resolve()
    resolved = (scripts_root / relative).resolve()
    if resolved == workspace_root or workspace_root not in resolved.parents:
        raise ContractError(f"artifact path escapes workspace: {relative_text!r}")
    return resolved


def artifact_record(scripts_root: Path, relative_text: str) -> dict[str, Any]:
    repository_lock_override = os.environ.get("P101_STACK_REPOS_LOCK", "")
    if relative_text == "repos.lock" and repository_lock_override:
        path = Path(repository_lock_override).resolve()
    else:
        path = admitted_path(scripts_root, relative_text)
    try:
        mode = path.stat().st_mode
    except OSError as error:
        raise ContractError(f"cannot stat {relative_text}: {error}") from error
    if not stat.S_ISREG(mode):
        raise ContractError(f"artifact is not a regular file: {relative_text}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ContractError(f"cannot read {relative_text}: {error}") from error
    return {
        "path": relative_text,
        "bytes": len(payload),
        "sha256": sha256_bytes(payload),
    }


def refreshed_document(scripts_root: Path) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "artifacts": [
            artifact_record(scripts_root, path) for path in sorted(DEFAULT_PATHS)
        ],
        "does_not_prove": DOES_NOT_PROVE,
    }


def read_contract(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read stack contract {path}: {error}") from error
    if not isinstance(document, dict) or set(document) != {
        "schema",
        "artifacts",
        "does_not_prove",
    }:
        raise ContractError(f"{path}: invalid top-level fields")
    if document.get("schema") != SCHEMA:
        raise ContractError(f"{path}: expected schema {SCHEMA}")
    if document.get("does_not_prove") != DOES_NOT_PROVE:
        raise ContractError(f"{path}: non-proof statement drifted")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ContractError(f"{path}: artifacts must be a non-empty array")
    paths: list[str] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict) or set(artifact) != {
            "path",
            "bytes",
            "sha256",
        }:
            raise ContractError(f"{path}: artifact {index} has invalid fields")
        relative = artifact.get("path")
        byte_count = artifact.get("bytes")
        digest = artifact.get("sha256")
        if (
            not isinstance(relative, str)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ContractError(f"{path}: artifact {index} has invalid values")
        paths.append(relative)
    if paths != sorted(DEFAULT_PATHS):
        raise ContractError(
            f"{path}: artifact inventory differs from the governed policy set"
        )
    return document


def verify(scripts_root: Path, contract_path: Path) -> dict[str, Any]:
    document = read_contract(contract_path)
    actual = [
        artifact_record(scripts_root, artifact["path"])
        for artifact in document["artifacts"]
    ]
    mismatches = [
        expected["path"]
        for expected, observed in zip(document["artifacts"], actual)
        if expected != observed
    ]
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "passed": not mismatches,
        "contract_sha256": sha256_bytes(contract_path.read_bytes()),
        "artifact_count": len(actual),
        "artifact_bytes": sum(artifact["bytes"] for artifact in actual),
        "mismatches": mismatches,
        "does_not_prove": DOES_NOT_PROVE,
    }
    receipt["receipt_digest"] = canonical_digest(receipt)
    return receipt


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(
                json.dumps(document, indent=2, sort_keys=True) + "\n"
            )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--scripts-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    result.add_argument("--contract", type=Path)
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("refresh")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--receipt", type=Path)
    return result


def main() -> int:
    arguments = parser().parse_args()
    scripts_root = arguments.scripts_root.resolve()
    contract_path = (
        arguments.contract.resolve()
        if arguments.contract is not None
        else Path(
            os.environ.get(
                "P101_STACK_CONTRACT",
                os.fspath(scripts_root / "contracts/p101-stack-contract.json"),
            )
        ).resolve()
    )
    try:
        if arguments.command == "refresh":
            write_json(contract_path, refreshed_document(scripts_root))
            print(f"wrote {contract_path}")
            return 0
        receipt = verify(scripts_root, contract_path)
        if arguments.receipt is not None:
            write_json(arguments.receipt.resolve(), receipt)
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt["passed"] else 1
    except ContractError as error:
        refusal = {
            "schema": "p101-stack-contract-refusal-v1",
            "outcome": "refused",
            "reason": "invalid-input",
            "diagnostic": str(error),
            "does_not_prove": DOES_NOT_PROVE,
        }
        refusal["receipt_digest"] = canonical_digest(refusal)
        print(json.dumps(refusal, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
