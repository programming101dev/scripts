#!/usr/bin/env python3
"""Validate the lightweight p101 architecture boundary register."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
DEFAULT_REGISTER = SCRIPTS_ROOT / "contracts" / "p101-boundaries.json"
REQUIRED_TESTS = {"clean", "typed_refusal", "binding_swap"}
REQUIRED_MATRIX_CASES = {
    "success",
    "typed_refusal",
    "stale_version",
    "identity_mismatch",
    "resource_limit",
    "binding_swap",
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


def validate(document: dict[str, Any]) -> dict[str, int]:
    if document.get("schema") != "p101-boundary-register-v1":
        raise BoundaryError("unexpected boundary-register schema")
    require_text(document, "does_not_prove", "register")
    matrix = document.get("test_matrix")
    if not isinstance(matrix, dict) or set(matrix) != REQUIRED_MATRIX_CASES:
        raise BoundaryError(
            f"register test_matrix must contain {sorted(REQUIRED_MATRIX_CASES)}"
        )
    for case in sorted(REQUIRED_MATRIX_CASES):
        row = matrix[case]
        context = f"test_matrix {case}"
        if not isinstance(row, dict) or set(row) != {"path", "marker"}:
            raise BoundaryError(f"{context} must contain path and marker")
        path = require_text(row, "path", context)
        marker = require_text(row, "marker", context)
        if marker not in read_workspace_file(path, context):
            raise BoundaryError(f"{context} marker {marker!r} is absent from {path}")
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
        if not isinstance(tests, dict):
            raise BoundaryError(f"{context} has no tests")
        if set(tests) != {"path", *REQUIRED_TESTS}:
            raise BoundaryError(f"{context} tests must be path plus {sorted(REQUIRED_TESTS)}")
        test_path = require_text(tests, "path", context)
        test_text = read_workspace_file(test_path, context)
        for kind in sorted(REQUIRED_TESTS):
            marker = require_text(tests, kind, context)
            if marker not in test_text:
                raise BoundaryError(
                    f"{context} {kind} marker {marker!r} is absent from {test_path}"
                )

    return {
        "boundaries": len(boundaries),
        "owners": len(owners),
        "matrix_cases": len(matrix),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--register", type=Path, default=DEFAULT_REGISTER, help="boundary register JSON"
    )
    arguments = parser.parse_args()
    document = json.loads(arguments.register.read_text(encoding="utf-8"))
    report = validate(document)
    print(
        "p101 boundary register: "
        f"{report['boundaries']} boundaries, {report['owners']} unique owners, "
        f"{report['matrix_cases']} boundary cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
