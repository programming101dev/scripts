#!/usr/bin/env python3
"""Regression checks for wrapper-conformance failure diagnostics."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "p101_wrapper_conformance",
        SCRIPT_DIR / "check-wrapper-conformance.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load check-wrapper-conformance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()


class FailureExcerptTests(unittest.TestCase):
    def test_empty_output_is_explicit(self) -> None:
        self.assertEqual(
            CHECKER.failure_excerpt(""),
            ["(test.sh produced no output)"],
        )

    def test_every_short_failure_line_is_preserved(self) -> None:
        output = "compile failed\nassertion failed\nctest failed\n"
        self.assertEqual(
            CHECKER.failure_excerpt(output),
            ["compile failed", "assertion failed", "ctest failed"],
        )

    def test_long_output_is_bounded_to_the_diagnostic_tail(self) -> None:
        count = CHECKER.FAILURE_EXCERPT_LINES + 20
        output = "\n".join(f"line {index}" for index in range(count))
        excerpt = CHECKER.failure_excerpt(output)
        self.assertEqual(len(excerpt), CHECKER.FAILURE_EXCERPT_LINES)
        self.assertEqual(excerpt[0], "line 20")
        self.assertEqual(excerpt[-1], f"line {count - 1}")


if __name__ == "__main__":
    unittest.main()
