#!/usr/bin/env python3
"""Regression tests for the wrapper lifecycle driver's build boundary."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]


def load_lifecycle_module() -> ModuleType:
    path = SCRIPTS_ROOT / "checks" / "check-wrapper-lifecycles.py"
    specification = importlib.util.spec_from_file_location(
        "check_wrapper_lifecycles", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class LifecycleDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_lifecycle_module()

    def test_build_directory_matches_requested_compiler(self) -> None:
        compiler = shutil.which("cc")
        other_compiler = shutil.which("false")
        self.assertIsNotNone(compiler)
        self.assertIsNotNone(other_compiler)
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            stale = repository / "build-clang"
            matching = repository / "build-selected"
            stale.mkdir()
            matching.mkdir()
            (stale / "CMakeCache.txt").write_text(
                f"CMAKE_C_COMPILER:FILEPATH={other_compiler}\n",
                encoding="utf-8",
            )
            (matching / "CMakeCache.txt").write_text(
                f"CMAKE_C_COMPILER:STRING={compiler}\n",
                encoding="utf-8",
            )
            (repository / ".last-build-dir").write_text(
                "build-clang\n", encoding="utf-8"
            )

            selected = self.module.built_directory(repository, compiler)

            self.assertEqual(selected, matching)

    def test_unsupported_link_flag_is_removed(self) -> None:
        compiler = shutil.which("cc")
        self.assertIsNotNone(compiler)

        supported = self.module.compiler_supported_link_flags(
            compiler,
            ["-Wall", "-fsanitize=p101-this-sanitizer-does-not-exist"],
        )

        self.assertEqual(supported, ["-Wall"])

    def test_replay_trace_is_stable_and_typed(self) -> None:
        specification = {
            "initial": "empty",
            "terminal": "empty",
            "transitions": [
                {"from": "empty", "operation": "open", "to": "live"},
                {"from": "live", "operation": "close", "to": "empty"},
            ],
        }

        trace = self.module.replay_trace(specification, ["open", "close"])

        self.assertEqual(
            trace,
            [
                {"step": 1, "from": "empty", "operation": "open", "to": "live"},
                {"step": 2, "from": "live", "operation": "close", "to": "empty"},
            ],
        )

    def test_replay_trace_rejects_invalid_counterexamples(self) -> None:
        specification = {
            "initial": "empty",
            "terminal": "empty",
            "transitions": [
                {"from": "empty", "operation": "open", "to": "live"},
                {"from": "live", "operation": "close", "to": "empty"},
            ],
        }

        with self.assertRaisesRegex(ValueError, "cannot apply"):
            self.module.replay_trace(specification, ["close"])
        with self.assertRaisesRegex(ValueError, "ended in"):
            self.module.replay_trace(specification, ["open"])


if __name__ == "__main__":
    unittest.main()
