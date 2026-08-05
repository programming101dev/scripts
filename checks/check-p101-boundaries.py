#!/usr/bin/env python3
"""Validate the lightweight p101 architecture boundary register."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
DEFAULT_REGISTER = SCRIPTS_ROOT / "contracts" / "p101-boundaries.json"
REQUIRED_TESTS = {
    "clean",
    "typed_refusal",
    "binding_swap",
    "identity_mismatch",
    "resource_limit",
    "stale_version",
}
EVIDENCE_REQUIRED = REQUIRED_TESTS - {"stale_version"}
REQUIRED_CONTRACT_FIELDS = {
    "authority_owner",
    "mechanism_owner",
    "effects",
    "resource_budget",
}


class BoundaryError(ValueError):
    """The boundary register does not satisfy its executable contract."""


def require_text(row: dict[str, Any], key: str, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BoundaryError(f"{context} has no {key}")
    return value


def read_workspace_file(relative: str, context: str) -> str:
    path = WORKSPACE / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BoundaryError(f"{context} refers to missing path: {relative}") from error
    try:
        resolved.relative_to(WORKSPACE.resolve())
    except ValueError as error:
        raise BoundaryError(f"{context} escapes the workspace: {relative}") from error
    if not resolved.is_file():
        raise BoundaryError(f"{context} is not a file: {relative}")
    return resolved.read_text(encoding="utf-8")


def inferred_test_entrypoint(text: str, marker: str) -> str | None:
    """Return the nearest enclosing C test function for a marker."""
    functions = list(
        re.finditer(
            r"(?m)^\s*static\s+void\s+(test_[A-Za-z0-9_]+)\s*"
            r"\([^;]*\)\s*\{",
            text,
        )
    )
    for index, function in enumerate(functions):
        end = (
            functions[index + 1].start()
            if index + 1 < len(functions)
            else len(text)
        )
        if marker in text[function.start() : end]:
            return function.group(1)
    return None


def require_test_wiring(
    test_path: str,
    marker: str,
    text: str,
    context: str,
) -> None:
    """Reject evidence that exists as dead text but is never invoked."""
    path = WORKSPACE / test_path
    if path.suffix in {".c", ".cc", ".cpp", ".cxx"}:
        entrypoint = inferred_test_entrypoint(text, marker)
        if entrypoint is None:
            raise BoundaryError(
                f"{context} marker is not inside a static test function"
            )
        if len(re.findall(rf"\b{re.escape(entrypoint)}\b", text)) < 2:
            raise BoundaryError(
                f"{context} test entrypoint is not invoked: {entrypoint}"
            )
        return

    if path.suffix == ".sh":
        owner = path.parents[1]
        launcher = owner / "test.sh"
        cmake = path.parent / "CMakeLists.txt"
        if not launcher.is_file():
            raise BoundaryError(f"{context} owner has no test.sh launcher")
        if not cmake.is_file() or path.name not in cmake.read_text(
            encoding="utf-8"
        ):
            raise BoundaryError(
                f"{context} shell evidence is not registered with CTest"
            )
        return

    raise BoundaryError(f"{context} has unsupported test evidence: {test_path}")


def executed_repositories(receipt_path: Path) -> set[str]:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BoundaryError(f"cannot read execution receipt: {error}") from error
    if (
        receipt.get("schema") != "p101-repository-test-receipt-v1"
        or receipt.get("passed") is not True
    ):
        raise BoundaryError("repository-test execution receipt is not clean")
    repositories = receipt.get("repositories")
    if not isinstance(repositories, list):
        raise BoundaryError("repository-test execution receipt has no records")
    return {
        str(record.get("repository"))
        for record in repositories
        if isinstance(record, dict) and record.get("unit") == "PASS"
    }


def validate(
    document: dict[str, Any],
    execution_receipt: Path | None = None,
) -> dict[str, int]:
    if document.get("schema") != "p101-boundary-register-v2":
        raise BoundaryError("unexpected boundary-register schema")
    require_text(document, "does_not_prove", "register")
    boundaries = document.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise BoundaryError("register has no boundaries")

    identifiers: set[str] = set()
    owners: set[tuple[str, str]] = set()
    for raw in boundaries:
        if not isinstance(raw, dict):
            raise BoundaryError("boundary row is not an object")
        identifier = require_text(raw, "id", "boundary")
        context = f"boundary {identifier}"
        if not identifier.startswith("boundary:"):
            raise BoundaryError(f"{context} has a noncanonical id")
        if identifier in identifiers:
            raise BoundaryError(f"duplicate boundary id: {identifier}")
        identifiers.add(identifier)

        for key in ("owner_repo", "input", "output", "refusal", "evidence"):
            require_text(raw, key, context)
        for key in REQUIRED_CONTRACT_FIELDS:
            require_text(raw, key, context)
        composition = raw.get("composition")
        if not isinstance(composition, list) or not composition:
            raise BoundaryError(f"{context} has no composition contract")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in composition
        ):
            raise BoundaryError(f"{context} has invalid composition")
        owner_source = require_text(raw, "owner_source", context)
        owner_symbol = require_text(raw, "owner_symbol", context)
        owner_key = (owner_source, owner_symbol)
        if owner_key in owners:
            raise BoundaryError(f"duplicate boundary owner: {owner_source}::{owner_symbol}")
        owners.add(owner_key)
        owner_text = read_workspace_file(owner_source, context)
        if owner_symbol not in owner_text:
            raise BoundaryError(f"{context} owner symbol is absent: {owner_symbol}")

        collaborators = raw.get("collaborators")
        if not isinstance(collaborators, list) or not collaborators:
            raise BoundaryError(f"{context} has no collaborators")
        if any(not isinstance(value, str) or not value.strip() for value in collaborators):
            raise BoundaryError(f"{context} has an invalid collaborator")

        tests = raw.get("tests")
        if not isinstance(tests, dict) or set(tests) != REQUIRED_TESTS:
            raise BoundaryError(f"{context} has no tests")
        boundary_evidence: set[tuple[str, str]] = set()
        for kind in sorted(REQUIRED_TESTS):
            evidence = tests[kind]
            evidence_context = f"{context} {kind}"
            if not isinstance(evidence, dict):
                raise BoundaryError(f"{evidence_context} must be an evidence object")
            if set(evidence) == {"not_applicable", "reason"}:
                if kind in EVIDENCE_REQUIRED:
                    raise BoundaryError(f"{evidence_context} requires executable evidence")
                if evidence.get("not_applicable") is not True:
                    raise BoundaryError(f"{evidence_context} has invalid not_applicable")
                require_text(evidence, "reason", evidence_context)
                continue
            if set(evidence) != {"path", "marker"}:
                raise BoundaryError(
                    f"{evidence_context} must contain path and marker"
                )
            test_path = require_text(evidence, "path", evidence_context)
            marker = require_text(evidence, "marker", evidence_context)
            evidence_key = (test_path, marker)
            if evidence_key in boundary_evidence:
                raise BoundaryError(
                    f"{evidence_context} reuses another matrix case's evidence"
                )
            boundary_evidence.add(evidence_key)
            test_text = read_workspace_file(test_path, evidence_context)
            if marker not in test_text:
                raise BoundaryError(
                    f"{evidence_context} marker {marker!r} is absent from {test_path}"
                )
            require_test_wiring(
                test_path,
                marker,
                test_text,
                evidence_context,
            )

    if execution_receipt is not None:
        executed = executed_repositories(execution_receipt)
        for raw in boundaries:
            owner_name = Path(raw["owner_repo"]).name
            if owner_name not in executed:
                raise BoundaryError(
                    f"boundary {raw['id']} owner test suite did not pass: "
                    f"{owner_name}"
                )

    return {
        "boundaries": len(boundaries),
        "owners": len(owners),
        "matrix_cases": len(boundaries) * len(REQUIRED_TESTS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--register", type=Path, default=DEFAULT_REGISTER, help="boundary register JSON"
    )
    parser.add_argument(
        "--execution-receipt",
        type=Path,
        help="require each boundary owner's repository test suite to have passed",
    )
    arguments = parser.parse_args()
    document = json.loads(arguments.register.read_text(encoding="utf-8"))
    report = validate(document, arguments.execution_receipt)
    print(
        "p101 boundary register: "
        f"{report['boundaries']} boundaries, {report['owners']} unique owners, "
        f"{report['matrix_cases']} per-boundary cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
