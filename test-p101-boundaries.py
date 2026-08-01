#!/usr/bin/env python3
"""Regression tests for the p101 boundary-register validator."""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "check_p101_boundaries", SCRIPT_DIR / "check-p101-boundaries.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            (SCRIPT_DIR / "p101-boundaries.json").read_text(encoding="utf-8")
        )

    def test_current_register(self) -> None:
        report = MODULE.validate(copy.deepcopy(self.document))
        self.assertGreaterEqual(report["boundaries"], 6)

    def test_duplicate_owner_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"][1]["owner_source"] = document["boundaries"][0][
            "owner_source"
        ]
        document["boundaries"][1]["owner_symbol"] = document["boundaries"][0][
            "owner_symbol"
        ]
        with self.assertRaisesRegex(MODULE.BoundaryError, "duplicate boundary owner"):
            MODULE.validate(document)

    def test_missing_test_marker_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"][0]["tests"]["binding_swap"] = "not_a_real_test_marker"
        with self.assertRaisesRegex(MODULE.BoundaryError, "binding_swap marker"):
            MODULE.validate(document)

    def test_missing_limitation_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["does_not_prove"] = ""
        with self.assertRaisesRegex(MODULE.BoundaryError, "does_not_prove"):
            MODULE.validate(document)


if __name__ == "__main__":
    unittest.main()
