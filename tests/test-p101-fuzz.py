#!/usr/bin/env python3
"""Regression tests for the first-class p101 fuzz dispatcher."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]


class FuzzDispatcherTests(unittest.TestCase):
    def test_external_tool_runs_from_the_callers_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            invocation_dir = Path(temporary) / "project"
            invocation_dir.mkdir()
            doctor = invocation_dir / "doctor-fixture"
            doctor.write_text("#!/bin/sh\npwd\n", encoding="utf-8")
            doctor.chmod(0o755)
            environment = os.environ.copy()
            environment["P101_DOCTOR"] = str(doctor)
            result = subprocess.run(
                [str(SCRIPTS_ROOT / "p101"), "doctor"],
                cwd=invocation_dir,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), os.path.realpath(invocation_dir))

    def test_help_does_not_require_a_repository_fuzz_target(self) -> None:
        result = subprocess.run(
            [str(SCRIPTS_ROOT / "p101"), "fuzz", "--help"],
            cwd=SCRIPTS_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Usage: p101 fuzz", result.stdout)

    def test_dispatches_in_repository_and_preserves_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "fixture"
            repository.mkdir()
            launcher = repository / "fuzz.sh"
            launcher.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$PWD\" \"$*\"\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
            result = subprocess.run(
                [
                    str(SCRIPTS_ROOT / "p101"),
                    "fuzz",
                    str(repository),
                    "-t",
                    "3",
                ],
                cwd=SCRIPTS_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            result.stdout.splitlines(),
            [os.path.realpath(repository), "-t 3"],
        )

    def test_missing_fuzz_contract_is_tool_trouble(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [str(SCRIPTS_ROOT / "p101"), "fuzz", temporary],
                cwd=SCRIPTS_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("has no executable fuzz.sh", result.stdout)


if __name__ == "__main__":
    unittest.main()
