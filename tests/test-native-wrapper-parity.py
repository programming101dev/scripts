#!/usr/bin/env python3
"""Negative controls for the native-wrapper parity checker."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]


class NativeWrapperParityTests(unittest.TestCase):
    def test_workspace_contract_passes(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS_ROOT / "checks" / "check-native-wrapper-parity.py")],
            cwd=SCRIPTS_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("native wrapper parity passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
