#!/usr/bin/env python3
"""Regression tests for the p101 boundary-register validator."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_p101_boundaries", SCRIPTS_ROOT / "checks" / "check-p101-boundaries.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            (SCRIPTS_ROOT / "contracts" / "p101-boundaries.json").read_text(encoding="utf-8")
        )

    def test_current_register(self) -> None:
        report = MODULE.validate(copy.deepcopy(self.document))
        self.assertGreaterEqual(report["boundaries"], 6)
        for boundary in self.document["boundaries"]:
            self.assertEqual(
                set(boundary["tests"]),
                MODULE.REQUIRED_TESTS,
                boundary["id"],
            )
        self.assertGreater(report["matrix_cases"], 0)

    def test_fact_decoder_requires_v6(self) -> None:
        file_record = "P101FACT\t6\tFILE\ttest.c\ttest\t0\t0"
        decoded_file = MODULE.c_facts.decode_lines([file_record])
        self.assertEqual(decoded_file[0]["kind"], "FILE")
        valid = (
            "P101FACT\t6\tFUNCTION\ttest.c\ttest\t0\t1\tfunction\t1\t0"
            "\tc:@F@function\t0\t1"
        )
        self.assertEqual(len(MODULE.c_facts.decode_lines([valid])), 1)
        invalid = valid.replace("P101FACT\t6\t", "P101FACT\t5\t", 1)
        with self.assertRaisesRegex(MODULE.c_facts.CFactError, "v6"):
            MODULE.c_facts.decode_lines([invalid])

    def test_duplicate_owner_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"][1]["owner_source"] = document["boundaries"][0][
            "owner_source"
        ]
        document["boundaries"][1]["owner_usr"] = document["boundaries"][0][
            "owner_usr"
        ]
        with self.assertRaisesRegex(MODULE.BoundaryError, "duplicate boundary owner"):
            MODULE.validate(document)

    def test_missing_semantic_test_role_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"][0]["tests"]["binding_swap"][
            "semantic_role"
        ] = "p101:boundary-case:not-real"
        with self.assertRaisesRegex(
            MODULE.BoundaryError, "binding_swap must resolve"
        ):
            MODULE.validate(document)

    def test_missing_limitation_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["does_not_prove"] = ""
        with self.assertRaisesRegex(MODULE.BoundaryError, "does_not_prove"):
            MODULE.validate(document)

    def test_missing_authority_contract_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"][0]["authority_owner"] = ""
        with self.assertRaisesRegex(MODULE.BoundaryError, "authority_owner"):
            MODULE.validate(document)

    def test_missing_per_boundary_case_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        del document["boundaries"][0]["tests"]["resource_limit"]
        with self.assertRaisesRegex(MODULE.BoundaryError, "has no tests"):
            MODULE.validate(document)

    def test_non_applicable_is_only_allowed_for_version_axis(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"][0]["tests"]["identity_mismatch"] = {
            "not_applicable": True,
            "reason": "not useful",
        }
        with self.assertRaisesRegex(MODULE.BoundaryError, "requires executable evidence"):
            MODULE.validate(document)

    def test_matrix_cases_cannot_reuse_the_same_evidence(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"][0]["tests"]["identity_mismatch"] = copy.deepcopy(
            document["boundaries"][0]["tests"]["binding_swap"]
        )
        with self.assertRaisesRegex(
            MODULE.BoundaryError, "reuses another matrix case"
        ):
            MODULE.validate(document)

    def test_execution_receipt_must_cover_every_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "p101-repository-test-receipt-v1",
                        "passed": True,
                        "repositories": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                MODULE.BoundaryError, "owner test suite did not pass"
            ):
                MODULE.validate(copy.deepcopy(self.document), receipt)


if __name__ == "__main__":
    unittest.main()
