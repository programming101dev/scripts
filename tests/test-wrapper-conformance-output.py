#!/usr/bin/env python3
"""Regression checks for wrapper-conformance failure diagnostics."""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
