#!/usr/bin/env python3
"""Regression checks for wrapper-conformance failure diagnostics."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "p101_wrapper_conformance",
        SCRIPTS_ROOT / "checks" / "check-wrapper-conformance.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load check-wrapper-conformance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()


class FailureOutputTests(unittest.TestCase):
    def test_empty_output_is_explicit(self) -> None:
        self.assertEqual(
            CHECKER.failure_output(""),
            ["(test.sh produced no output)"],
        )

    def test_every_short_failure_line_is_preserved(self) -> None:
        output = "compile failed\nassertion failed\nctest failed\n"
        self.assertEqual(
            CHECKER.failure_output(output),
            ["compile failed", "assertion failed", "ctest failed"],
        )

    def test_long_output_is_not_truncated(self) -> None:
        count = 100
        output = "\n".join(f"line {index}" for index in range(count))
        emitted = CHECKER.failure_output(output)
        self.assertEqual(len(emitted), count)
        self.assertEqual(emitted[0], "line 0")
        self.assertEqual(emitted[-1], f"line {count - 1}")


class FaultOutcomeTests(unittest.TestCase):
    KEY = ("linux", "lib_io", "p101_read", "errno", "EINTR")

    def test_parser_retains_numeric_code_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "outcomes.tsv"
            path.write_text(
                "P101WRAPPER\t1\tFAULT\tlinux\tlib_io\tp101_read\t"
                "errno\tEINTR\t4\tPASS\n",
                encoding="utf-8",
            )
            outcomes, failures = CHECKER.fault_outcome_evidence(path)
        self.assertEqual(failures, [])
        self.assertEqual(outcomes, {self.KEY: (4, "PASS")})

    def test_duplicate_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "outcomes.tsv"
            line = (
                "P101WRAPPER\t1\tFAULT\tlinux\tlib_io\tp101_read\t"
                "errno\tEINTR\t4\tPASS\n"
            )
            path.write_text(line + line, encoding="utf-8")
            _outcomes, failures = CHECKER.fault_outcome_evidence(path)
        self.assertTrue(any("duplicate outcome" in item for item in failures))

    def test_missing_unexpected_and_failed_records_are_rejected(self) -> None:
        unexpected = (
            "linux",
            "lib_io",
            "p101_write",
            "errno",
            "EIO",
        )
        failures = CHECKER.compare_fault_outcomes(
            {self.KEY},
            {unexpected: (5, "FAIL")},
        )
        self.assertTrue(any("missing direct errno:EINTR" in item for item in failures))
        self.assertTrue(any("unexpected direct outcome errno:EIO" in item for item in failures))

        failures = CHECKER.compare_fault_outcomes(
            {self.KEY},
            {self.KEY: (4, "FAIL")},
        )
        self.assertEqual(
            failures,
            ["lib_io:p101_read: direct errno:EINTR outcome failed"],
        )


class CanonicalEventModelTests(unittest.TestCase):
    def test_library_campaign_has_one_stable_run_identity(self) -> None:
        self.assertEqual(
            CHECKER.conformance_run_id("lib_c_facts"),
            "p101-wrapper-conformance-lib-c-facts",
        )

    def test_call_evidence_comes_from_canonical_model_nodes(self) -> None:
        document = {
            "schema": "p101-run-model-v1",
            "nodes": [
                {
                    "domain": "call",
                    "kind": "call-enter",
                    "name": "p101_read",
                    "arguments": "fd=3",
                    "result": "-",
                },
                {
                    "domain": "resource",
                    "kind": "fd-open",
                },
                {
                    "domain": "call",
                    "kind": "call-exit",
                    "name": "p101_read",
                    "arguments": "-",
                    "result": "4",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run-model.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            enters, exits, arguments, results = CHECKER.call_evidence(path)
        self.assertEqual(enters["p101_read"], 1)
        self.assertEqual(exits["p101_read"], 1)
        self.assertEqual(arguments, {"p101_read"})
        self.assertEqual(results, {"p101_read"})

    def test_unsupported_model_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run-model.json"
            path.write_text('{"schema":"old","nodes":[]}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unsupported schema"):
                CHECKER.call_evidence(path)


if __name__ == "__main__":
    unittest.main()
