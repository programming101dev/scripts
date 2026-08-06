#!/usr/bin/env python3
"""Negative controls for the test-inventory and source-responsibility gates."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    specification = importlib.util.spec_from_file_location(
        name, SCRIPTS_ROOT / "checks" / filename
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


INVENTORY_MODULE = load("p101_test_inventory", "check-p101-test-inventory.py")
RESPONSIBILITY_MODULE = load(
    "p101_source_responsibilities", "check-p101-source-responsibilities.py"
)
FUNCTIONAL_LAYOUT_MODULE = load(
    "p101_functional_layout", "check-functional-library-split.py"
)


class ArchitectureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = json.loads(
            (SCRIPTS_ROOT / "contracts" / "p101-test-inventory.json").read_text(encoding="utf-8")
        )
        cls.graph = json.loads(
            (SCRIPTS_ROOT / "contracts" / "p101-check-graph.json").read_text(encoding="utf-8")
        )
        cls.responsibilities = json.loads(
            (SCRIPTS_ROOT / "contracts" / "p101-source-responsibilities.json").read_text(
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
        owner = next(
            candidate
            for candidate in document["owners"]
            if candidate["id"] == "tool-subprocess"
        )
        owner["forbidden_call_usrs"] = ["c:@F@p101_tool_run_capture"]
        with self.assertRaisesRegex(
            RESPONSIBILITY_MODULE.ResponsibilityError, "bypasses tool-subprocess"
        ):
            RESPONSIBILITY_MODULE.validate(document)

    def test_repository_build_order_respects_library_dependencies(self) -> None:
        repositories: list[Path] = []
        for raw_line in (SCRIPTS_ROOT / "repos.txt").read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("|")
            self.assertGreaterEqual(len(fields), 2)
            repositories.append((SCRIPTS_ROOT / fields[1]).resolve())

        position = {repository: index for index, repository in enumerate(repositories)}
        target_owner: dict[str, Path] = {}
        inspected_configs = 0
        for repository in repositories:
            config = repository / "config.cmake"
            if not config.is_file():
                continue
            inspected_configs += 1
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

        self.assertGreater(
            inspected_configs,
            0,
            "repository dependency-order test inspected no config.cmake files",
        )
        self.assertGreater(
            len(target_owner),
            0,
            "repository dependency-order test discovered no library targets",
        )
        self.assertEqual(violations, [])

    def test_functional_layout_accepts_native_headers_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "lib_demo"
            source = repository / "src" / "unistd.c"
            header = repository / "include" / "p101_demo" / "p101_unistd.h"
            source.parent.mkdir(parents=True)
            header.parent.mkdir(parents=True)
            source.write_text("int p101_demo(void) { return 0; }\n")
            header.write_text("int p101_demo(void);\n")
            (repository / "config.cmake").write_text(
                "set(p101_demo_SOURCES src/unistd.c)\n"
                "set(p101_demo_HEADERS include/p101_demo/p101_unistd.h)\n"
            )

            self.assertEqual(
                FUNCTIONAL_LAYOUT_MODULE.validate_functional_layout(
                    repository,
                    "demo",
                    {"src/unistd.c"},
                    {"include/p101_demo/p101_unistd.h"},
                ),
                [],
            )

    def test_functional_layout_rejects_origin_directories_and_layout_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "lib_demo"
            source = repository / "src" / "unistd.c"
            stale_source = repository / "src" / "posix" / "legacy.c"
            header = repository / "include" / "p101_demo" / "p101_unistd.h"
            extra_header = repository / "include" / "p101_demo" / "legacy.h"
            for path in (source, stale_source, header, extra_header):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n")
            (repository / "config.cmake").write_text(
                "set(p101_demo_SOURCES src/unistd.c src/posix/legacy.c)\n"
                "set(p101_demo_HEADERS include/p101_demo/p101_unistd.h "
                "include/p101_demo/legacy.h)\n"
            )

            failures = (
                FUNCTIONAL_LAYOUT_MODULE.validate_functional_layout(
                    repository,
                    "demo",
                    {"src/unistd.c"},
                    {"include/p101_demo/p101_unistd.h"},
                )
            )

            self.assertTrue(
                any("native source layout drift" in failure for failure in failures)
            )
            self.assertTrue(
                any("native header layout drift" in failure for failure in failures)
            )
            self.assertTrue(
                any("obsolete implementation origin" in failure for failure in failures)
            )
            self.assertTrue(
                any("p101_demo_SOURCES does not match" in failure for failure in failures)
            )


if __name__ == "__main__":
    unittest.main()
