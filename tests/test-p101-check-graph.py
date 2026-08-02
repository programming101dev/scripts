#!/usr/bin/env python3
"""Unit tests for the governed p101 check-graph runner."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "p101_check_graph", SCRIPTS_ROOT / "checks" / "p101-check-graph.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CheckGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            (SCRIPTS_ROOT / "contracts" / "p101-check-graph.json").read_text(encoding="utf-8")
        )

    def test_current_graph(self) -> None:
        ordered = MODULE.validate(copy.deepcopy(self.document))
        self.assertGreaterEqual(len(ordered), 30)
        self.assertLess(
            [node["id"] for node in ordered].index("boundaries"),
            [node["id"] for node in ordered].index("tool-audit"),
        )

    def test_cycle_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["nodes"][0]["depends_on"] = [document["nodes"][-1]["id"]]
        with self.assertRaisesRegex(MODULE.GraphError, "cycle"):
            MODULE.validate(document)

    def test_selection_includes_dependencies(self) -> None:
        ordered = MODULE.validate(copy.deepcopy(self.document))
        selected = MODULE.select_nodes(ordered, {"boundary-tests"}, set(), None)
        self.assertEqual(
            [node["id"] for node in selected],
            [
                "repository-lock-tests",
                "workspace-lock",
                "check-graph-tests",
                "boundaries",
                "boundary-tests",
            ],
        )

    def test_group_skip_and_resume(self) -> None:
        ordered = MODULE.validate(copy.deepcopy(self.document))
        selected = MODULE.select_nodes(
            ordered, set(), {"cmake"}, "tool-contracts"
        )
        identifiers = {node["id"] for node in selected}
        self.assertNotIn("cmake-regression", identifiers)
        self.assertIn("tool-contracts", identifiers)
        self.assertNotIn("boundaries", identifiers)

    def test_argv_expansion_drops_empty_optional_tokens(self) -> None:
        command = MODULE.expand_command(
            ["tool", "{required}", "{optional}"],
            {"required": "value", "optional": ""},
        )
        self.assertEqual(command, ["tool", "value"])

    def test_interactive_run_retries_only_failed_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            marker = output / "attempt"
            code = (
                "from pathlib import Path; import sys; "
                f"p=Path({str(marker)!r}); "
                "exists=p.exists(); p.write_text('attempt'); "
                "raise SystemExit(0 if exists else 7)"
            )
            document = {
                "schema": "p101-check-graph-v1",
                "does_not_prove": "test receipt only",
            }
            nodes = [
                {
                    "id": "retry",
                    "title": "retry",
                    "required": True,
                    "command": [sys.executable, "-c", code],
                    "guarantee": "retry is local",
                }
            ]
            with patch("builtins.input", return_value="r"):
                status = MODULE.run_graph(document, nodes, output, {}, True)
            self.assertEqual(status, 0)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["schema"], "p101-tool-run-receipt-v1")
            self.assertEqual(receipt["records"][0]["attempts"], 2)
            self.assertEqual(receipt["records"][0]["outcome"], "clean")

    def test_noninteractive_failure_prints_complete_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            code = "print('actionable finding'); raise SystemExit(9)"
            document = {
                "schema": "p101-check-graph-v1",
                "does_not_prove": "test receipt only",
            }
            nodes = [
                {
                    "id": "failure",
                    "title": "failure",
                    "required": True,
                    "command": [sys.executable, "-c", code],
                    "guarantee": "failure evidence is visible",
                }
            ]
            console = StringIO()
            with redirect_stdout(console):
                status = MODULE.run_graph(document, nodes, output, {}, False)
            self.assertEqual(status, 1)
            self.assertIn("--- failure log ---", console.getvalue())
            self.assertIn("actionable finding", console.getvalue())


if __name__ == "__main__":
    unittest.main()
