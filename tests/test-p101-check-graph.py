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

    def setUp(self) -> None:
        patcher = patch.object(
            MODULE,
            "workspace_source_identity",
            return_value={
                "algorithm": "sha256",
                "digest": "sha256:test-workspace",
                "files": 1,
                "bytes": 1,
                "repositories": [],
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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

    @staticmethod
    def node(
        identifier: str,
        code: str,
        *,
        dependencies: list[str] | None = None,
        writes: list[str] | None = None,
        units: dict[str, int] | None = None,
    ) -> dict:
        return {
            "id": identifier,
            "title": identifier,
            "group": "test",
            "required": True,
            "command": [sys.executable, "-c", code],
            "depends_on": dependencies or [],
            "resources": {"writes": writes or [], "units": units or {}},
            "guarantee": f"{identifier} test guarantee",
        }

    @staticmethod
    def make_document(nodes: list[dict], jobs: int = 2) -> dict:
        return {
            "schema": "p101-check-graph-v1",
            "default_jobs": jobs,
            "resource_capacities": {"exclusive": 1},
            "does_not_prove": "test receipt only",
            "coverage": {
                "required_nodes": [node["id"] for node in nodes],
                "does_not_prove": "test coverage only",
            },
            "nodes": nodes,
        }

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
            self.assertEqual(receipt["schema"], "p101-tool-run-receipt-v3")
            self.assertEqual(receipt["failure"]["reason"], "none")
            self.assertEqual(receipt["records"][0]["attempts"], 2)
            self.assertEqual(receipt["records"][0]["outcome"], "clean")
            self.assertGreaterEqual(receipt["records"][0]["duration_ns"], 0)
            self.assertGreater(receipt["records"][0]["log_bytes"], 0)
            self.assertGreater(receipt["records"][0]["log_lines"], 0)
            self.assertTrue(receipt["records"][0]["result"])
            profile = (output / "profile.md").read_text(encoding="utf-8")
            self.assertIn("## Invocation order", profile)
            self.assertIn("## Slowest checks", profile)

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
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["failure"]["reason"], "tool-error")
            self.assertEqual(receipt["failure"]["stage"], "failure")
            self.assertEqual(
                receipt["failure"]["first_diagnostic"], "actionable finding"
            )

    def test_independent_nodes_run_concurrently(self) -> None:
        nodes = [
            self.node("one", "import time; time.sleep(0.25); print('one')"),
            self.node("two", "import time; time.sleep(0.25); print('two')"),
        ]
        document = self.make_document(nodes)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            status = MODULE.run_graph(
                document, nodes, output, {"out": str(output)}, False, jobs=2
            )
            self.assertEqual(status, 0)
            receipt = json.loads((output / "receipt.json").read_text())
            records = receipt["records"]
            self.assertLess(
                records[1]["started_unix_ns"], records[0]["finished_unix_ns"]
            )

    def test_resource_capacity_serializes_nodes(self) -> None:
        nodes = [
            self.node(
                "one",
                "import time; time.sleep(0.1)",
                units={"exclusive": 1},
            ),
            self.node(
                "two",
                "import time; time.sleep(0.1)",
                units={"exclusive": 1},
            ),
        ]
        document = self.make_document(nodes)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            status = MODULE.run_graph(
                document, nodes, output, {"out": str(output)}, False, jobs=2
            )
            self.assertEqual(status, 0)
            records = json.loads((output / "receipt.json").read_text())["records"]
            self.assertGreaterEqual(
                records[1]["started_unix_ns"], records[0]["finished_unix_ns"]
            )

    def test_exact_cache_restores_outputs_without_reexecution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            counter = root / "counter"

            def run(output: Path, measurement: bool = False) -> dict:
                output = output.resolve()
                artifact = output / "artifact.txt"
                code = (
                    "from pathlib import Path; "
                    f"counter=Path({str(counter)!r}); "
                    "value=int(counter.read_text())+1 if counter.exists() else 1; "
                    "counter.write_text(str(value)); "
                    f"Path({str(artifact)!r}).write_text('evidence')"
                )
                node = self.node(
                    "cached", code, writes=["{out}/artifact.txt"]
                )
                document = self.make_document([node])
                output.mkdir()
                status = MODULE.run_graph(
                    document,
                    [node],
                    output,
                    {"out": str(output)},
                    False,
                    cache_directory=cache,
                    measurement=measurement,
                )
                self.assertEqual(status, 0)
                self.assertEqual(artifact.read_text(), "evidence")
                return json.loads((output / "receipt.json").read_text())

            first = run(root / "one")
            second = run(root / "two")
            self.assertEqual(counter.read_text(), "1")
            self.assertEqual(first["records"][0]["outcome"], "clean")
            self.assertEqual(second["records"][0]["outcome"], "reused")
            self.assertEqual(second["records"][0]["evidence"]["source"], "cache")

            measured = run(root / "three", measurement=True)
            self.assertEqual(counter.read_text(), "2")
            self.assertEqual(measured["mode"], "measurement")
            self.assertEqual(measured["records"][0]["outcome"], "clean")

    def test_from_requires_current_receipted_prerequisite(self) -> None:
        first = self.node("first", "print('first')")
        second = self.node(
            "second", "print('second')", dependencies=["first"]
        )
        document = self.make_document([first, second], jobs=1)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            status = MODULE.run_graph(
                document,
                [first, second],
                output,
                {"out": str(output)},
                False,
                all_nodes=[first, second],
            )
            self.assertEqual(status, 0)
            previous = MODULE.read_previous_receipt(output / "receipt.json")
            status = MODULE.run_graph(
                document,
                [second],
                output,
                {"out": str(output)},
                False,
                previous=previous,
                all_nodes=[first, second],
                required_previous={"first"},
            )
            self.assertEqual(status, 0)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertTrue(
                all(row["outcome"] == "reused" for row in receipt["records"])
            )

            changed = self.node("first", "print('changed')")
            with self.assertRaisesRegex(
                MODULE.GraphError, "no current clean receipt"
            ):
                MODULE.run_graph(
                    document,
                    [second],
                    output,
                    {"out": str(output)},
                    False,
                    previous=previous,
                    all_nodes=[changed, second],
                    required_previous={"first"},
                )

    def test_resume_reexecutes_when_declared_output_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            artifact = output / "artifact.txt"
            counter = output / "counter.txt"
            code = (
                "from pathlib import Path; "
                f"counter=Path({str(counter)!r}); "
                "value=int(counter.read_text())+1 if counter.exists() else 1; "
                "counter.write_text(str(value)); "
                f"Path({str(artifact)!r}).write_text('evidence')"
            )
            node = self.node("resume", code, writes=["{out}/artifact.txt"])
            document = self.make_document([node], jobs=1)
            self.assertEqual(
                MODULE.run_graph(document, [node], output, {}, False), 0
            )
            previous = MODULE.read_previous_receipt(output / "receipt.json")
            artifact.unlink()
            self.assertEqual(
                MODULE.run_graph(
                    document,
                    [node],
                    output,
                    {},
                    False,
                    previous=previous,
                ),
                0,
            )
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["records"][0]["outcome"], "clean")
            self.assertEqual(counter.read_text(), "2")


if __name__ == "__main__":
    unittest.main()
