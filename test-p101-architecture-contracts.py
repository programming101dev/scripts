#!/usr/bin/env python3
"""Negative controls for the test-inventory and source-responsibility gates."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def load(name: str, filename: str):
    specification = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


INVENTORY_MODULE = load("p101_test_inventory", "check-p101-test-inventory.py")
RESPONSIBILITY_MODULE = load(
    "p101_source_responsibilities", "check-p101-source-responsibilities.py"
)


class ArchitectureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = json.loads(
            (SCRIPT_DIR / "p101-test-inventory.json").read_text(encoding="utf-8")
        )
        cls.graph = json.loads(
            (SCRIPT_DIR / "p101-check-graph.json").read_text(encoding="utf-8")
        )
        cls.responsibilities = json.loads(
            (SCRIPT_DIR / "p101-source-responsibilities.json").read_text(
                encoding="utf-8"
            )
        )

    def test_current_contracts(self) -> None:
        inventory_report = INVENTORY_MODULE.validate(
            copy.deepcopy(self.inventory), copy.deepcopy(self.graph)
        )
        responsibility_report = RESPONSIBILITY_MODULE.validate(
            copy.deepcopy(self.responsibilities)
        )
        self.assertGreater(inventory_report["repository_entries"], 100)
        self.assertGreater(responsibility_report["consumer_files"], 100)

    def test_stale_inventory_exclusion_is_rejected(self) -> None:
        document = copy.deepcopy(self.inventory)
        document["standalone_verification_exclusions"][0]["path"] = "missing-check.sh"
        with self.assertRaisesRegex(INVENTORY_MODULE.InventoryError, "stale"):
            INVENTORY_MODULE.validate(document, copy.deepcopy(self.graph))

    def test_facade_growth_is_rejected(self) -> None:
        document = copy.deepcopy(self.responsibilities)
        document["facades"][0]["maximum_lines"] = 1
        with self.assertRaisesRegex(
            RESPONSIBILITY_MODULE.ResponsibilityError, "facade responsibility grew"
        ):
            RESPONSIBILITY_MODULE.validate(document)

    def test_owner_bypass_is_rejected(self) -> None:
        document = copy.deepcopy(self.responsibilities)
        document["owners"][2]["forbidden_calls"] = ["p101_tool_run_capture"]
        with self.assertRaisesRegex(
            RESPONSIBILITY_MODULE.ResponsibilityError, "bypasses tool-subprocess"
        ):
            RESPONSIBILITY_MODULE.validate(document)

    def test_repository_build_order_respects_library_dependencies(self) -> None:
        repositories: list[Path] = []
        for raw_line in (SCRIPT_DIR / "repos.txt").read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("|")
            self.assertGreaterEqual(len(fields), 2)
            repositories.append((SCRIPT_DIR / fields[1]).resolve())

        position = {repository: index for index, repository in enumerate(repositories)}
        target_owner: dict[str, Path] = {}
        for repository in repositories:
            config = repository / "config.cmake"
            if not config.is_file():
                continue
            text = config.read_text(encoding="utf-8")
            match = re.search(r"set\(LIBRARY_TARGETS\s+([^)]*)\)", text, re.DOTALL)
            if match is not None:
                for target in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", match.group(1)):
                    target_owner[target] = repository

        violations: list[str] = []
        for repository in repositories:
            config = repository / "config.cmake"
            if not config.is_file():
                continue
            text = config.read_text(encoding="utf-8")
            for match in re.finditer(
                r"set\([^\s()]+_LINK_LIBRARIES\s+([^)]*)\)", text, re.DOTALL
            ):
                for dependency in re.findall(
                    r"[A-Za-z_][A-Za-z0-9_]*", match.group(1)
                ):
                    owner = target_owner.get(dependency)
                    if owner is not None and position[owner] > position[repository]:
                        violations.append(
                            f"{repository.name} precedes {owner.name} ({dependency})"
                        )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
