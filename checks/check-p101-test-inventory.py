#!/usr/bin/env python3
"""Check that repository and scripts verification entry points stay governed."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
INVENTORY = SCRIPTS_ROOT / "contracts" / "p101-test-inventory.json"
GRAPH = SCRIPTS_ROOT / "contracts" / "p101-check-graph.json"
VERIFY_NAME = re.compile(r"^(?:check|test)-.*\.(?:sh|py)$")


class InventoryError(ValueError):
    """A verification entry point has no declared owner or runner."""


def require_text(row: dict[str, Any], key: str, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InventoryError(f"{context} has no {key}")
    return value


def repository_rows(path: Path) -> list[tuple[str, Path, str]]:
    rows: list[tuple[str, Path, str]] = []
    seen_paths: set[Path] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("|")
        if len(fields) != 3 or not all(fields):
            raise InventoryError(f"{path.name}:{number}: malformed repository row")
        url, relative, language = fields
        repository = (SCRIPTS_ROOT / relative).resolve()
        if repository in seen_paths:
            raise InventoryError(f"duplicate repository path: {relative}")
        seen_paths.add(repository)
        rows.append((url, repository, language))
    return rows


def graph_entry_points(document: dict[str, Any]) -> set[str]:
    entry_points: set[str] = set()
    for node in document.get("nodes", []):
        command = node.get("command")
        if not isinstance(command, list) or not command:
            continue
        executable = command[0]
        if isinstance(executable, str) and executable.startswith("./"):
            entry_points.add(executable[2:])
    return entry_points


def validate(document: dict[str, Any], graph: dict[str, Any]) -> dict[str, int]:
    if document.get("schema") != "p101-test-inventory-v1":
        raise InventoryError("unexpected test-inventory schema")
    require_text(document, "does_not_prove", "inventory")
    manifest_name = require_text(document, "repository_manifest", "inventory")
    entry_contracts = document.get("entry_points")
    if not isinstance(entry_contracts, dict) or not entry_contracts:
        raise InventoryError("inventory has no repository entry-point contracts")
    for name, raw in entry_contracts.items():
        if not isinstance(raw, dict):
            raise InventoryError(f"entry point {name} is not an object")
        for field in ("owner", "oracle", "runner"):
            require_text(raw, field, f"entry point {name}")
        runner = SCRIPTS_ROOT / raw["runner"]
        if not runner.is_file():
            raise InventoryError(f"entry point {name} has missing runner: {raw['runner']}")

    exclusions_raw = document.get("standalone_verification_exclusions")
    if not isinstance(exclusions_raw, list):
        raise InventoryError("inventory has no exclusion list")
    exclusions: set[str] = set()
    for raw in exclusions_raw:
        if not isinstance(raw, dict):
            raise InventoryError("verification exclusion is not an object")
        path = require_text(raw, "path", "verification exclusion")
        if path in exclusions:
            raise InventoryError(f"duplicate verification exclusion: {path}")
        exclusions.add(path)
        for field in ("owner", "oracle", "reason"):
            require_text(raw, field, f"verification exclusion {path}")
        if not (SCRIPTS_ROOT / path).is_file():
            raise InventoryError(f"stale verification exclusion: {path}")

    repository_count = 0
    repository_entry_count = 0
    for _url, repository, language in repository_rows(SCRIPTS_ROOT / manifest_name):
        repository_count += 1
        if not repository.is_dir():
            raise InventoryError(f"missing repository: {repository}")
        if language not in {"c", "cxx", "python", "c-bootstrap"}:
            raise InventoryError(f"unsupported repository language: {language}")
        for name in entry_contracts:
            path = repository / name
            if path.exists():
                if not path.is_file() or not path.stat().st_mode & 0o111:
                    raise InventoryError(f"repository entry point is not executable: {path}")
                repository_entry_count += 1
        if (repository / "test" / "CMakeLists.txt").is_file() and not (
            repository / "test.sh"
        ).is_file():
            raise InventoryError(f"unit-test tree has no test.sh: {repository}")
        if (repository / "fuzz" / "CMakeLists.txt").is_file() and not (
            repository / "fuzz.sh"
        ).is_file():
            raise InventoryError(f"fuzz tree has no fuzz.sh: {repository}")

    governed = graph_entry_points(graph)
    governed.add("checks/p101-check-graph.py")
    for path in governed:
        entry = SCRIPTS_ROOT / path
        if not entry.is_file() or not entry.stat().st_mode & 0o111:
            raise InventoryError(f"governed scripts entry point is not executable: {path}")
    discovered = {
        path.relative_to(SCRIPTS_ROOT).as_posix()
        for directory in (
            SCRIPTS_ROOT,
            SCRIPTS_ROOT / "checks",
            SCRIPTS_ROOT / "workspace",
        )
        for path in directory.iterdir()
        if path.is_file() and VERIFY_NAME.match(path.name)
    }
    unknown_exclusions = exclusions - discovered
    if unknown_exclusions:
        raise InventoryError(f"exclusions are not verification entry points: {sorted(unknown_exclusions)}")
    missing = discovered - governed - exclusions
    if missing:
        raise InventoryError(f"ungoverned scripts verification entry points: {sorted(missing)}")

    return {
        "repositories": repository_count,
        "repository_entries": repository_entry_count,
        "script_entries": len(discovered),
        "script_exclusions": len(exclusions),
    }


def main() -> int:
    try:
        document = json.loads(INVENTORY.read_text(encoding="utf-8"))
        graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        report = validate(document, graph)
    except (InventoryError, json.JSONDecodeError, OSError) as error:
        print(f"p101-test-inventory: {error}", file=sys.stderr)
        return 1
    print(
        "p101 test inventory: "
        f"{report['repositories']} repositories, "
        f"{report['repository_entries']} repository entry points, "
        f"{report['script_entries']} scripts verification entry points "
        f"({report['script_exclusions']} explicitly delegated)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
