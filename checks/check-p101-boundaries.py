#!/usr/bin/env python3
"""Validate the lightweight p101 architecture boundary register."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

import c_facts  # noqa: E402


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


def require_shell_test_wiring(
    test_path: str, marker: str, text: str, context: str
) -> None:
    """Reject shell evidence that is absent or not registered with CTest."""
    path = WORKSPACE / test_path
    if path.suffix != ".sh":
        raise BoundaryError(f"{context} has unsupported lexical evidence: {test_path}")
    if marker not in text:
        raise BoundaryError(f"{context} marker {marker!r} is absent from {test_path}")
    owner = path.parents[1]
    launcher = owner / "test.sh"
    cmake = path.parent / "CMakeLists.txt"
    if not launcher.is_file():
        raise BoundaryError(f"{context} owner has no test.sh launcher")
    cmake_text = (
        "\n".join(
            line.split("#", 1)[0]
            for line in cmake.read_text(encoding="utf-8").splitlines()
        )
        if cmake.is_file()
        else ""
    )
    wired = (
        re.search(
            rf"(?<![A-Za-z0-9_.-]){re.escape(path.name)}"
            r"(?![A-Za-z0-9_.-])",
            cmake_text,
        )
        is not None
    )
    if not wired:
        raise BoundaryError(f"{context} shell evidence is not registered with CTest")


def semantic_index(
    paths: set[str],
) -> tuple[
    dict[Path, set[str]],
    dict[str, set[tuple[Path, str]]],
    dict[Path, set[str]],
]:
    facts = c_facts.acquire(WORKSPACE, [WORKSPACE / path for path in sorted(paths)])
    declarations: dict[Path, set[str]] = {}
    roles: dict[str, set[tuple[Path, str]]] = {}
    wired: dict[Path, set[str]] = {}
    for fact in facts:
        kind = fact["kind"]
        path = Path(str(fact["path"])).resolve()
        if kind == "FUNCTION":
            usr = str(fact.get("usr", ""))
            if usr:
                declarations.setdefault(path, set()).add(usr)
        elif kind == "CALL":
            usr = str(fact.get("usr", ""))
            if usr:
                wired.setdefault(path, set()).add(usr)
        elif kind == "NOTE":
            value = str(fact.get("value", ""))
            caller_usr = str(fact.get("caller_usr", ""))
            if value.startswith("SEMANTIC_ROLE:") and caller_usr:
                roles.setdefault(value.removeprefix("SEMANTIC_ROLE:"), set()).add(
                    (path, caller_usr)
                )
            elif value.startswith("FUNCTION_REFERENCE:"):
                referenced_usr = value.removeprefix("FUNCTION_REFERENCE:")
                if referenced_usr:
                    wired.setdefault(path, set()).add(referenced_usr)
    return declarations, roles, wired


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
    if document.get("schema") != "p101-boundary-register-v3":
        raise BoundaryError("unexpected boundary-register schema")
    require_text(document, "does_not_prove", "register")
    boundaries = document.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise BoundaryError("register has no boundaries")

    semantic_paths: set[str] = set()
    for raw in boundaries:
        semantic_paths.add(require_text(raw, "owner_source", "boundary owner"))
        tests = raw.get("tests", {})
        if isinstance(tests, dict):
            for evidence in tests.values():
                if (
                    isinstance(evidence, dict)
                    and isinstance(evidence.get("path"), str)
                    and Path(evidence["path"]).suffix in {".c", ".cc", ".cpp", ".cxx"}
                ):
                    semantic_paths.add(evidence["path"])
    try:
        declarations, semantic_roles, wired_functions = semantic_index(
            semantic_paths
        )
    except c_facts.CFactError as error:
        raise BoundaryError(str(error)) from error

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
        owner_usr = require_text(raw, "owner_usr", context)
        owner_key = (owner_source, owner_usr)
        if owner_key in owners:
            raise BoundaryError(f"duplicate boundary owner: {owner_source}::{owner_usr}")
        owners.add(owner_key)
        read_workspace_file(owner_source, context)
        owner_path = (WORKSPACE / owner_source).resolve()
        if owner_usr not in declarations.get(owner_path, set()):
            raise BoundaryError(
                f"{context} owner declaration identity is absent from "
                f"{owner_source}: {owner_usr}"
            )

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
            test_path = require_text(evidence, "path", evidence_context)
            suffix = Path(test_path).suffix
            if suffix in {".c", ".cc", ".cpp", ".cxx"}:
                if set(evidence) != {"path", "semantic_role"}:
                    raise BoundaryError(
                        f"{evidence_context} must contain path and semantic_role"
                    )
                semantic_role = require_text(
                    evidence, "semantic_role", evidence_context
                )
                evidence_key = (test_path, semantic_role)
            elif suffix == ".sh":
                if set(evidence) != {"path", "marker"}:
                    raise BoundaryError(
                        f"{evidence_context} must contain path and marker"
                    )
                marker = require_text(evidence, "marker", evidence_context)
                evidence_key = (test_path, marker)
            else:
                raise BoundaryError(
                    f"{evidence_context} has unsupported evidence: {test_path}"
                )
            if evidence_key in boundary_evidence:
                raise BoundaryError(
                    f"{evidence_context} reuses another matrix case's evidence"
                )
            boundary_evidence.add(evidence_key)
            test_text = read_workspace_file(test_path, evidence_context)
            if suffix == ".sh":
                require_shell_test_wiring(
                    test_path, marker, test_text, evidence_context
                )
                continue
            test_source_path = (WORKSPACE / test_path).resolve()
            functions = {
                function_usr
                for path, function_usr in semantic_roles.get(
                    semantic_role, set()
                )
                if path == test_source_path
            }
            if len(functions) != 1:
                raise BoundaryError(
                    f"{evidence_context} must resolve to exactly one annotated "
                    f"function in {test_path}, got {len(functions)}"
                )
            function_usr = next(iter(functions))
            if function_usr not in wired_functions.get(test_source_path, set()):
                raise BoundaryError(
                    f"{evidence_context} annotated test function is not "
                    f"statically wired: "
                    f"{function_usr}"
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
