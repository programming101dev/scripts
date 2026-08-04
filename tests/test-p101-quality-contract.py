#!/usr/bin/env python3
"""Negative and positive controls for the p101 quality catalog."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_p101_quality_contract",
    SCRIPTS_ROOT / "checks" / "check-p101-quality-contract.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class QualityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            (SCRIPTS_ROOT / "contracts" / "p101-quality-contract.json").read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def discovered(document: dict[str, object]) -> dict[tuple[str, str], list[str]]:
        result: dict[tuple[str, str], list[str]] = {}
        for row in document["typed_outcome_sets"]:  # type: ignore[index]
            result[(row["source"], row["type"])] = list(row["variants"])
        for row in document["typed_outcome_exclusions"]:  # type: ignore[index]
            result[(row["source"], row["type"])] = ["CLASSIFICATION_VALUE"]
        return result

    def test_current_contract(self) -> None:
        report = MODULE.validate(copy.deepcopy(self.document))
        self.assertGreaterEqual(report["public_surfaces"], 4)
        self.assertGreaterEqual(report["typed_outcome_sets"], 8)
        self.assertGreaterEqual(report["typed_outcome_variants"], 40)
        self.assertEqual(report["platforms"], 3)

    def test_unknown_oracle_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["public_surfaces"][0]["oracle"] = "missing-oracle"
        with self.assertRaisesRegex(MODULE.QualityContractError, "unknown oracle"):
            MODULE.validate(document)

    def test_refusal_variant_drift_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        discovered = self.discovered(document)
        document["typed_outcome_sets"][0]["variants"].pop()
        with self.assertRaisesRegex(MODULE.QualityContractError, "drifted"):
            MODULE.validate(document, discovered)

    def test_unclassified_public_enum_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        discovered = self.discovered(document)
        document["typed_outcome_exclusions"].pop()
        with self.assertRaisesRegex(
            MODULE.QualityContractError, "public enum classification drift"
        ):
            MODULE.validate(document, discovered)

    def test_lib_c_facts_enum_inventory_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-quality-facts.") as temporary:
            facts_root = Path(temporary)
            facts = facts_root / "lib_error" / "source-facts.tsv"
            facts.parent.mkdir()
            source = (
                SCRIPTS_ROOT.parent
                / "libraries"
                / "lib_error"
                / "include"
                / "p101_error"
                / "error.h"
            )
            facts.write_text(
                f"P101FACT\t4\tENUM\t{source}\terror\t1\t50\tp101_error_type\n"
                f"P101FACT\t4\tENUMERATOR\t{source}\terror\t1\t52\tP101_ERROR_NONE\tp101_error_type\n",
                encoding="utf-8",
            )
            observed = MODULE.discover_public_enums(facts_root)
            self.assertEqual(
                observed[(str(source.relative_to(SCRIPTS_ROOT.parent)), "p101_error_type")],
                ["P101_ERROR_NONE"],
            )

    def test_invalid_audit_delegation_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["audit_responsibilities"][0]["mode"] = "ambient"
        with self.assertRaisesRegex(MODULE.QualityContractError, "invalid mode"):
            MODULE.validate(document)

    def test_boundary_omission_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["boundaries"].pop()
        with self.assertRaisesRegex(
            MODULE.QualityContractError, "quality boundary coverage drift"
        ):
            MODULE.validate(document)

    def test_non_main_exit_owner_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["process_termination"]["allowed_owner"] = "library"
        with self.assertRaisesRegex(MODULE.QualityContractError, "only main"):
            MODULE.validate(document)

    def test_platform_omission_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["platform_evidence"]["required"].remove("freebsd")
        with self.assertRaisesRegex(MODULE.QualityContractError, "FreeBSD"):
            MODULE.validate(document)

    def test_missing_limitation_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["does_not_prove"] = ""
        with self.assertRaisesRegex(MODULE.QualityContractError, "does_not_prove"):
            MODULE.validate(document)

    @staticmethod
    def platform_receipt(system: str) -> dict[str, object]:
        receipt: dict[str, object] = {
            "schema": "p101-check-graph-receipt-v2",
            "tool": {"name": "p101-check-graph", "version": "2"},
            "host": {
                "system": system,
                "release": "test",
                "machine": "test",
                "python": "test",
            },
            "input": {
                "schema": "p101-check-graph-v1",
                "identity": "contracts/p101-check-graph.json",
                "workspace": {},
            },
            "mode": "functional",
            "jobs": 1,
            "cache": {"enabled": False, "directory": "", "reused": 0},
            "outcome": "clean",
            "failure": {"reason": "none", "stage": "", "first_diagnostic": ""},
            "checks": {"attempted": 2, "completed": 2},
            "elapsed_ns": 1,
            "workspace_lock": None,
            "stack_contract": {
                "valid": True,
                "contract_sha256": "contract",
            },
            "records": [],
            "does_not_prove": "fixture",
        }
        receipt["receipt_digest"] = MODULE.canonical_sha256(receipt)
        return receipt

    def test_platform_receipt_merge(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="p101-quality-receipts."
        ) as temporary:
            paths = []
            for system in ("FreeBSD", "Linux", "Darwin"):
                path = Path(temporary) / f"{system}.json"
                path.write_text(
                    json.dumps(self.platform_receipt(system)) + "\n",
                    encoding="utf-8",
                )
                paths.append(path)
            report = MODULE.merge_platform_receipts(
                paths, {"freebsd", "linux", "macos"}
            )
            self.assertEqual(report["receipt_count"], 3)

    def test_platform_receipt_digest_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="p101-quality-receipts."
        ) as temporary:
            path = Path(temporary) / "Linux.json"
            receipt = self.platform_receipt("Linux")
            receipt["outcome"] = "tool-error"
            path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.QualityContractError, "receipt digest mismatch"
            ):
                MODULE.merge_platform_receipts([path], {"linux"})


if __name__ == "__main__":
    unittest.main()
