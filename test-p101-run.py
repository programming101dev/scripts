#!/usr/bin/env python3
"""Tests for the explicit capture-plus-analysis composition."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "p101-run.py"


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


class RunTests(unittest.TestCase):
    def test_composes_capture_and_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observe = executable(
                root / "observe",
                """
printf '%s\n' "$@" > "$P101_TEST_ARGS"
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then mkdir -p "$2"; break; fi
  shift
done
""",
            )
            analyze = executable(
                root / "analyze",
                """
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then mkdir -p "$2"; break; fi
  shift
done
""",
            )
            output = root / "result"
            args_file = root / "observe.args"
            result = subprocess.run(
                [
                    str(RUN),
                    "-o",
                    str(output),
                    "--observe-tool",
                    str(observe),
                    "--analyze-tool",
                    str(analyze),
                    "--",
                    "/bin/true",
                ],
                env={"P101_TEST_ARGS": str(args_file), "PATH": "/usr/bin:/bin"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "capture").is_dir())
            self.assertTrue((output / "analysis").is_dir())
            self.assertEqual(args_file.read_text(encoding="utf-8").splitlines()[0], "-C")

    def test_command_findings_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observe = executable(
                root / "observe",
                """
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then mkdir -p "$2"; break; fi
  shift
done
exit 1
""",
            )
            analyze = executable(
                root / "analyze",
                """
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then mkdir -p "$2"; break; fi
  shift
done
""",
            )
            result = subprocess.run(
                [
                    str(RUN),
                    "-o",
                    str(root / "result"),
                    "--observe-tool",
                    str(observe),
                    "--analyze-tool",
                    str(analyze),
                    "--",
                    "/bin/false",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 1)

    def test_capture_trouble_stops_before_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "analyzed"
            observe = executable(root / "observe", "exit 2\n")
            analyze = executable(root / "analyze", f"touch {marker}\n")
            result = subprocess.run(
                [
                    str(RUN),
                    "-o",
                    str(root / "result"),
                    "--observe-tool",
                    str(observe),
                    "--analyze-tool",
                    str(analyze),
                    "--",
                    "/bin/false",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
