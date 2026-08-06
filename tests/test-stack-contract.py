#!/usr/bin/env python3
"""Negative and positive controls for the p101 stack policy contract."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_ROOT / "workspace" / "stack-contract.py"
SPEC = importlib.util.spec_from_file_location("p101_stack_contract", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
stack_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stack_contract)


class StackContractTests(unittest.TestCase):
    def create_workspace(self, root: Path) -> Path:
        scripts = root / "scripts"
        for relative in stack_contract.DEFAULT_PATHS:
            path = (scripts / relative).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{relative}\n", encoding="utf-8")
        return scripts

    def test_refresh_and_verify_are_deterministic_and_digest_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-stack-contract.") as temporary:
            scripts = self.create_workspace(Path(temporary))
            contract = scripts / "contracts/p101-stack-contract.json"
            first = stack_contract.refreshed_document(scripts)
            stack_contract.write_json(contract, first)
            first_bytes = contract.read_bytes()
            stack_contract.write_json(
                contract, stack_contract.refreshed_document(scripts)
            )
            self.assertEqual(contract.read_bytes(), first_bytes)

            receipt = stack_contract.verify(scripts, contract)
            self.assertTrue(receipt["passed"])
            self.assertEqual(
                receipt["artifact_count"], len(stack_contract.DEFAULT_PATHS)
            )
            self.assertTrue(receipt["receipt_digest"].startswith("sha256:"))

    def test_altered_policy_is_a_failing_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-stack-contract.") as temporary:
            scripts = self.create_workspace(Path(temporary))
            contract = scripts / "contracts/p101-stack-contract.json"
            stack_contract.write_json(
                contract, stack_contract.refreshed_document(scripts)
            )
            (scripts / "contracts/p101-boundaries.json").write_text(
                "changed\n", encoding="utf-8"
            )
            receipt = stack_contract.verify(scripts, contract)
            self.assertFalse(receipt["passed"])
            self.assertEqual(
                receipt["mismatches"], ["contracts/p101-boundaries.json"]
            )

    def test_inventory_substitution_and_path_escape_are_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-stack-contract.") as temporary:
            scripts = self.create_workspace(Path(temporary))
            contract = scripts / "contracts/p101-stack-contract.json"
            document = stack_contract.refreshed_document(scripts)
            document["artifacts"][0]["path"] = "replacement"
            stack_contract.write_json(contract, document)
            with self.assertRaises(stack_contract.ContractError):
                stack_contract.verify(scripts, contract)
            with self.assertRaises(stack_contract.ContractError):
                stack_contract.admitted_path(scripts, "../../outside")

    def test_invalid_contract_is_a_typed_refusal_shape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="p101-stack-contract.") as temporary:
            scripts = self.create_workspace(Path(temporary))
            contract = scripts / "contracts/p101-stack-contract.json"
            contract.parent.mkdir(parents=True, exist_ok=True)
            contract.write_text("{}\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--scripts-root",
                    str(scripts),
                    "--contract",
                    str(contract),
                    "verify",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            refusal = json.loads(completed.stderr)
            self.assertEqual(
                refusal["schema"], "p101-stack-contract-refusal-v1"
            )
            self.assertEqual(refusal["outcome"], "refused")
            self.assertEqual(refusal["reason"], "invalid-input")
            self.assertTrue(refusal["receipt_digest"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
