#!/usr/bin/env python3
"""Regression tests for repeated, identity-bound performance comparison."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
CHECKER = SCRIPTS_ROOT / "checks" / "compare-check-performance.py"


def receipt(path: Path, elapsed: int, identity: str = "same") -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "p101-check-graph-receipt-v2",
                "outcome": "clean",
                "mode": "measurement",
                "cache": {"reused": 0},
                "elapsed_ns": elapsed,
                "records": [
                    {
                        "id": "one",
                        "outcome": "clean",
                        "input_identity": identity,
                        "result": {"exit_code": 0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class PerformanceTests(unittest.TestCase):
    def run_check(
        self,
        baseline: list[Path],
        candidate: list[Path],
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(CHECKER)]
        for path in baseline:
            command.extend(["--baseline", str(path)])
        for path in candidate:
            command.extend(["--candidate", str(path)])
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_five_identity_matched_samples_accept_real_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = [root / f"b{index}.json" for index in range(5)]
            candidate = [root / f"c{index}.json" for index in range(5)]
            for index, path in enumerate(baseline):
                receipt(path, 100 + index)
            for index, path in enumerate(candidate):
                receipt(path, 70 + index)
            result = self.run_check(baseline, candidate)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("faster", result.stdout)

    def test_identity_drift_is_a_tool_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = [root / f"b{index}.json" for index in range(5)]
            candidate = [root / f"c{index}.json" for index in range(5)]
            for path in baseline:
                receipt(path, 100)
            for index, path in enumerate(candidate):
                receipt(path, 70, "different" if index == 4 else "same")
            result = self.run_check(baseline, candidate)
            self.assertEqual(result.returncode, 2)
            self.assertIn("identity differs", result.stdout)

    def test_cache_reuse_cannot_be_claimed_as_a_speedup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = [root / f"b{index}.json" for index in range(5)]
            candidate = [root / f"c{index}.json" for index in range(5)]
            for path in baseline:
                receipt(path, 100)
            for path in candidate:
                receipt(path, 1)
                document = json.loads(path.read_text(encoding="utf-8"))
                document["cache"]["reused"] = 1
                document["records"][0]["outcome"] = "reused"
                path.write_text(json.dumps(document), encoding="utf-8")
            result = self.run_check(baseline, candidate)
            self.assertEqual(result.returncode, 2)
            self.assertIn("reused cached nodes", result.stdout)


if __name__ == "__main__":
    unittest.main()
