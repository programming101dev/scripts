#!/usr/bin/env python3
"""Negative and positive controls for the p101 quality catalog."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
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
    def discovered(document: dict[str, object]) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for row in document["typed_outcome_sets"]:  # type: ignore[index]
            result[row["type_usr"]] = {
                "source": row["source"],
                "type": row["type"],
                "variants": list(row["variants"]),
            }
        for row in document["typed_outcome_exclusions"]:  # type: ignore[index]
            result[row["type_usr"]] = {
                "source": row["source"],
                "type": row["type"],
                "variants": ["CLASSIFICATION_VALUE"],
            }
        return result

    def test_current_contract(self) -> None:
        report = MODULE.validate(copy.deepcopy(self.document))
        self.assertGreaterEqual(report["public_surfaces"], 4)
        self.assertGreaterEqual(report["typed_outcome_sets"], 8)
        self.assertGreaterEqual(report["typed_outcome_variants"], 40)
        self.assertEqual(report["platforms"], 3)

    def test_cli_requires_public_enum_evidence_by_default(self) -> None:
        completed = subprocess.run(
            [str(SCRIPTS_ROOT / "checks" / "check-p101-quality-contract.py")],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("public enum facts are required", completed.stdout)

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
                f"P101FACT\t7\tENUM\t{source}\terror\t1\t50\tp101_error_type\tc:@E@p101_error_type\n"
                f"P101FACT\t7\tENUMERATOR\t{source}\terror\t1\t52\tP101_ERROR_NONE\tp101_error_type\tc:@E@p101_error_type@P101_ERROR_NONE\tc:@E@p101_error_type\n",
                encoding="utf-8",
            )
            observed = MODULE.discover_public_enums(facts_root)
            self.assertEqual(
                observed["c:@E@p101_error_type"],
                {
                    "source": str(source.relative_to(SCRIPTS_ROOT.parent)),
                    "type": "p101_error_type",
                    "variants": ["P101_ERROR_NONE"],
                },
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
        document["process_termination"]["allowed_caller_usr"] = "c:@F@worker"
        with self.assertRaisesRegex(MODULE.QualityContractError, "only main"):
            MODULE.validate(document)

    def test_termination_exclusion_requires_a_semantic_role(self) -> None:
        document = copy.deepcopy(self.document)
        document["process_termination"]["excluded_semantic_roles"] = []
        with self.assertRaisesRegex(
            MODULE.QualityContractError,
            "invalid semantic scope",
        ):
            MODULE.validate(document)

    def test_platform_omission_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["platform_evidence"]["required"].remove("freebsd")
        with self.assertRaisesRegex(MODULE.QualityContractError, "FreeBSD"):
            MODULE.validate(document)

    def test_semantic_program_scans_are_repository_local(self) -> None:
        units = MODULE.c_facts._analysis_units(  # pylint: disable=protected-access
            SCRIPTS_ROOT.parent,
            [SCRIPTS_ROOT.parent / "programs"],
        )
        by_repository = {
            repository: set(paths) for repository, paths in units
        }
        audit = (SCRIPTS_ROOT.parent / "programs" / "p101-audit").resolve()
        self.assertIn(audit, by_repository)
        self.assertIn(audit / "src", by_repository[audit])
        self.assertIn(audit / "include", by_repository[audit])
        error_contract = audit / "components" / "error-contract"
        self.assertIn(error_contract, by_repository)
        self.assertIn(
            error_contract / "include",
            by_repository[error_contract],
        )
        direct_units = MODULE.c_facts._analysis_units(  # pylint: disable=protected-access
            SCRIPTS_ROOT.parent,
            [audit],
        )
        direct_by_scope = {
            scope: set(paths) for scope, paths in direct_units
        }
        self.assertIn(audit, direct_by_scope)
        doctor = audit / "components" / "doctor"
        self.assertIn(doctor, direct_by_scope)
        self.assertIn(doctor / "src", direct_by_scope[doctor])
        self.assertIn(doctor / "test", direct_by_scope[doctor])
        self.assertNotIn(doctor, direct_by_scope[doctor])

    def test_public_headers_are_standalone_semantic_inputs(self) -> None:
        root = SCRIPTS_ROOT.parent / "libraries" / "lib_c_facts"
        self.assertTrue(
            MODULE.c_facts._is_standalone_header_input(  # pylint: disable=protected-access
                root / "include" / "p101_c_facts" / "compile_command.h"
            )
        )
        self.assertTrue(
            MODULE.c_facts._is_standalone_header_input(  # pylint: disable=protected-access
                root / "include"
            )
        )
        self.assertFalse(
            MODULE.c_facts._is_standalone_header_input(  # pylint: disable=protected-access
                root / "test" / "test_analysis.c"
            )
        )

    def test_semantic_scans_import_compile_database_include_roots(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="p101-quality-compile-db."
        ) as temporary:
            repository = Path(temporary)
            include = repository / "include"
            system = repository / "system"
            quote = repository / "quote"
            after = repository / "after"
            command = repository / "command"
            for path in (include, system, quote, after, command):
                path.mkdir()
            database = [
                {
                    "directory": str(repository),
                    "arguments": [
                        "clang",
                        "-I",
                        "include",
                        f"-isystem={system}",
                        f"-iquote{quote}",
                        "-idirafter",
                        str(after),
                        "-c",
                        "source.c",
                    ],
                    "file": "source.c",
                },
                {
                    "directory": str(repository),
                    "command": "clang -Icommand -c other.c",
                    "file": "other.c",
                },
            ]
            (repository / "compile_commands.json").write_text(
                json.dumps(database),
                encoding="utf-8",
            )
            roots = MODULE.c_facts._compile_database_include_roots(  # pylint: disable=protected-access
                repository
            )
            self.assertEqual(
                roots,
                {
                    include.resolve(),
                    system.resolve(),
                    quote.resolve(),
                    after.resolve(),
                    command.resolve(),
                },
            )

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
