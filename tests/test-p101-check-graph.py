#!/usr/bin/env python3
"""Unit tests for the governed p101 check-graph runner."""

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import os
import subprocess
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
CONTENT_SPEC = importlib.util.spec_from_file_location(
    "p101_content_manifest", SCRIPTS_ROOT / "runtime" / "content_manifest.py"
)
assert CONTENT_SPEC is not None and CONTENT_SPEC.loader is not None
CONTENT_MODULE = importlib.util.module_from_spec(CONTENT_SPEC)
CONTENT_SPEC.loader.exec_module(CONTENT_MODULE)


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
        self.assertTrue(self.document["require_complete_inputs"])
        self.assertTrue(
            all(node.get("inputs_complete") is True for node in ordered)
        )
        self.assertNotIn(
            "/tmp",
            {
                path
                for node in ordered
                for path in node.get("resources", {}).get("writes", [])
            },
        )
        self.assertNotIn(
            "{semantic_cache}",
            {
                path
                for node in ordered
                for path in node.get("resources", {}).get("writes", [])
            },
        )
        self.assertLess(
            [node["id"] for node in ordered].index("boundaries"),
            [node["id"] for node in ordered].index("tool-audit"),
        )
        terminal = next(
            node for node in ordered if node["id"] == "semantic-snapshot"
        )
        self.assertTrue(terminal["wait_for_selected"])
        referenced = {
            dependency
            for node in ordered
            if node["id"] != "semantic-snapshot"
            for dependency in node.get("depends_on", [])
        }
        leaves = {
            node["id"]
            for node in ordered
            if node["id"] != "semantic-snapshot"
            and node["id"] not in referenced
        }
        self.assertEqual(set(terminal["depends_on"]), leaves)

    def test_library_edit_selects_a_narrow_complete_impact_set(self) -> None:
        ordered = MODULE.validate(copy.deepcopy(self.document))
        directly_impacted = MODULE.impact_closure(
            ["libraries/lib_io/src/io.c"],
            ordered,
            propagate_dependencies=False,
        )
        selected = MODULE.impact_dependency_closure(
            directly_impacted, {node["id"]: node for node in ordered}
        )
        self.assertIn("wrapper-unit-tests", selected)
        self.assertIn("repository-tests", selected)
        self.assertNotIn("workspace-lock", selected)
        self.assertLess(len(selected), len(ordered) // 3)

    def test_post_update_wrapper_normalizes_internal_graph_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = root / "failing-graph"
            graph.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            graph.chmod(0o755)
            environment = os.environ.copy()
            environment["P101_CHECK_GRAPH"] = str(graph)
            environment["P101_INSPECT_CAPTURE"] = "/usr/bin/true"
            environment["P101_INSPECT"] = "/usr/bin/true"
            environment["P101_TOOL_RECEIPT"] = "/usr/bin/true"
            environment["P101_AUDIT_WORKSPACE"] = "/usr/bin/true"
            result = subprocess.run(
                [
                    str(SCRIPTS_ROOT / "check-after-update-all.sh"),
                    "-c",
                    "/usr/bin/true",
                    "-x",
                    "/usr/bin/true",
                    "-o",
                    str(root / "output"),
                ],
                cwd=SCRIPTS_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("post-update-all checks failed", result.stderr)
            self.assertNotIn("post-update-all checks passed", result.stdout)

    def test_cycle_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        format_node = next(
            node for node in document["nodes"] if node["id"] == "format-workspace"
        )
        format_node["depends_on"] = [document["nodes"][-1]["id"]]
        with self.assertRaisesRegex(MODULE.GraphError, "cycle"):
            MODULE.validate(document)

    def test_unknown_command_variable_is_rejected_during_validation(self) -> None:
        document = copy.deepcopy(self.document)
        document["nodes"][0]["command"].append("{undeclared_workspace}")
        with self.assertRaisesRegex(MODULE.GraphError, "unknown variables"):
            MODULE.validate(document)

    def test_selection_includes_dependencies(self) -> None:
        ordered = MODULE.validate(copy.deepcopy(self.document))
        selected = MODULE.select_nodes(ordered, {"boundaries"}, set(), None)
        self.assertEqual(
            [node["id"] for node in selected],
            [
                "format-workspace",
                "repository-lock-tests",
                "workspace-lock",
                "stack-contract-tests",
                "stack-contract",
                "check-graph-tests",
                "performance-contract-tests",
                "workspace-cmake-tests",
                "semantic-prime-tests",
                "semantic-prime",
                "boundaries",
            ],
        )

    @staticmethod
    def graph_receipt(system: str) -> dict[str, object]:
        receipt: dict[str, object] = {
            "schema": MODULE.RUN_RECEIPT_SCHEMA,
            "host": {"system": system},
            "input": {"schema": "p101-check-graph-v1", "identity": "graph"},
            "outcome": "clean",
            "checks": {"attempted": 2, "completed": 2},
            "stack_contract": {
                "valid": True,
                "contract_sha256": "sha256:stack",
            },
        }
        receipt["receipt_digest"] = MODULE.canonical_sha256(receipt)
        return receipt

    def test_quality_platform_receipts_are_digest_and_identity_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for system in ("Darwin", "FreeBSD", "Linux"):
                path = Path(temporary) / f"{system}.json"
                path.write_text(
                    json.dumps(self.graph_receipt(system)), encoding="utf-8"
                )
                paths.append(path)
            with redirect_stdout(StringIO()) as output:
                MODULE.merge_quality_receipts(
                    paths, {"freebsd", "linux", "macos"}
                )
            self.assertIn("3 receipts", output.getvalue())

            tampered = json.loads(paths[0].read_text(encoding="utf-8"))
            tampered["outcome"] = "tool-error"
            paths[0].write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.GraphError, "digest mismatch"):
                MODULE.merge_quality_receipts(
                    paths, {"freebsd", "linux", "macos"}
                )

    def test_instrumentation_platform_receipts_require_one_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for system in ("Darwin", "FreeBSD", "Linux"):
                path = Path(temporary) / f"{system}.json"
                path.write_text(
                    json.dumps(
                        {
                            "schema": "p101-instrumentation-platform-receipt-v1",
                            "platform": system,
                            "passed": True,
                            "contract_sha256": "contract",
                            "functions": [f"{system}:function"],
                        }
                    ),
                    encoding="utf-8",
                )
                paths.append(path)
            with redirect_stdout(StringIO()) as output:
                MODULE.merge_instrumentation_receipts(
                    paths, {"Darwin", "FreeBSD", "Linux"}
                )
            self.assertIn("union instrumented functions: 3", output.getvalue())

            receipt = json.loads(paths[0].read_text(encoding="utf-8"))
            receipt["contract_sha256"] = "different"
            paths[0].write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.GraphError, "different instrumentation"):
                MODULE.merge_instrumentation_receipts(
                    paths, {"Darwin", "FreeBSD", "Linux"}
                )

    def test_semantic_prime_precedes_all_direct_semantic_consumers(self) -> None:
        nodes = {node["id"]: node for node in self.document["nodes"]}
        self.assertNotEqual(nodes["semantic-prime"].get("affected"), False)
        self.assertIn(
            "semantic-prime", nodes["source-responsibilities"]["depends_on"]
        )
        self.assertIn(
            "semantic-prime", nodes["wrapper-failure-contract"]["depends_on"]
        )

    def test_group_skip_and_resume(self) -> None:
        ordered = MODULE.validate(copy.deepcopy(self.document))
        selected = MODULE.select_nodes(
            ordered, set(), {"cmake"}, "quality-contract"
        )
        identifiers = {node["id"] for node in selected}
        self.assertNotIn("cmake-regression", identifiers)
        self.assertIn("quality-contract", identifiers)
        self.assertNotIn("boundaries", identifiers)

    def test_argv_expansion_drops_empty_optional_tokens(self) -> None:
        command = MODULE.expand_command(
            ["tool", "{required}", "{optional}"],
            {"required": "value", "optional": ""},
        )
        self.assertEqual(command, ["tool", "value"])

    def test_unused_graph_variable_does_not_invalidate_node(self) -> None:
        node = self.node("identity", "print('identity')")
        command = node["command"]
        workspace = {
            "algorithm": "sha256",
            "digest": "sha256:workspace",
            "files": 1,
            "bytes": 1,
            "repositories": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = MODULE.node_input_identity(
                node,
                command,
                {"unused": "first", "cc": "/usr/bin/true"},
                output,
                workspace,
            )
            second = MODULE.node_input_identity(
                node,
                command,
                {"unused": "changed", "cc": "/usr/bin/false"},
                output,
                workspace,
            )
        self.assertEqual(first, second)

    def test_qualified_tool_identity_is_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts_root = root / "scripts"
            first = root / "candidate-one" / "inspect-capture"
            second = root / "candidate-two" / "inspect-capture"
            scripts_root.mkdir()
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"same qualified executable\n")
            second.write_bytes(first.read_bytes())
            with patch.object(MODULE, "SCRIPTS_ROOT", scripts_root):
                first_identity = MODULE.tool_identity(str(first))
                second_identity = MODULE.tool_identity(str(second))
        self.assertEqual(first_identity, second_identity)

    def test_qualified_tool_locators_are_bound_by_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts_root = root / "scripts"
            first_tool = root / "candidate-one" / "inspect-capture"
            second_tool = root / "candidate-two" / "inspect-capture"
            scripts_root.mkdir()
            first_tool.parent.mkdir()
            second_tool.parent.mkdir()
            first_tool.write_bytes(b"same qualified executable\n")
            second_tool.write_bytes(first_tool.read_bytes())
            with patch.object(MODULE, "SCRIPTS_ROOT", scripts_root):
                with patch.dict(
                    os.environ,
                    {"P101_INSPECT_CAPTURE": str(first_tool)},
                    clear=True,
                ):
                    first = MODULE.semantic_environment()
                with patch.dict(
                    os.environ,
                    {"P101_INSPECT_CAPTURE": str(second_tool)},
                    clear=True,
                ):
                    second = MODULE.semantic_environment()
        self.assertEqual(first, second)
        self.assertIn("P101_INSPECT_CAPTURE", first)

    def test_external_policy_file_is_bound_by_content_not_path(self) -> None:
        node = self.node("identity", "print('identity')")
        node["environment_files"] = ["P101_REPOS_LOCK"]
        workspace = {
            "algorithm": "sha256",
            "digest": "sha256:workspace",
            "files": 1,
            "bytes": 1,
            "repositories": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "one.lock"
            second_path = root / "two.lock"
            first_path.write_text("same\n", encoding="utf-8")
            second_path.write_text("same\n", encoding="utf-8")
            with patch.dict(
                os.environ, {"P101_REPOS_LOCK": str(first_path)}, clear=False
            ):
                first = MODULE.node_input_identity(
                    node, node["command"], {}, root, workspace
                )
            with patch.dict(
                os.environ, {"P101_REPOS_LOCK": str(second_path)}, clear=False
            ):
                second = MODULE.node_input_identity(
                    node, node["command"], {}, root, workspace
                )
            second_path.write_text("changed\n", encoding="utf-8")
            with patch.dict(
                os.environ, {"P101_REPOS_LOCK": str(second_path)}, clear=False
            ):
                changed = MODULE.node_input_identity(
                    node, node["command"], {}, root, workspace
                )
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_temporary_root_marker_is_not_owned_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve()
            node = {
                "id": "temporary",
                "resources": {
                    "writes": ["{temporary_root}", "{out}/owned"],
                    "units": {},
                },
            }
            paths = MODULE.expanded_writes(
                node,
                {
                    "out": os.fspath(output),
                    "temporary_root": tempfile.gettempdir(),
                },
                output,
            )
            self.assertEqual(
                paths,
                [
                    output / "owned",
                    output / "semantic-usage" / "temporary.jsonl",
                ],
            )

    def test_obsolete_semantic_usage_logs_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            usage_directory = output / "semantic-usage"
            usage_directory.mkdir()
            current = usage_directory / "current.jsonl"
            retired = usage_directory / "retired.jsonl"
            current.write_text("current\n", encoding="utf-8")
            retired.write_text("retired\n", encoding="utf-8")

            MODULE.prune_obsolete_usage_logs(output, [{"id": "current"}])

            self.assertTrue(current.is_file())
            self.assertFalse(retired.exists())

    def test_semantic_usage_requires_its_cache_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usage = root / "usage.jsonl"
            key = "a" * 64
            usage.write_text(
                json.dumps(
                    {
                        "schema": "p101-semantic-usage-v1",
                        "kind": "compile-database-facts",
                        "key": key,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(MODULE.semantic_usage_available(usage, root))
            (root / "entries" / key).mkdir(parents=True)
            self.assertTrue(MODULE.semantic_usage_available(usage, root))

    def test_semantic_usage_validates_clang_ast_dependency_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = "c" * 64
            usage = root / "usage.jsonl"
            usage.write_text(
                json.dumps({"kind": "clang-ast", "key": key}) + "\n",
                encoding="utf-8",
            )
            entry = root / "ast" / key
            entry.mkdir(parents=True)
            dependency = root / "demo.c"
            dependency.write_text("int demo(void) { return 0; }\n", encoding="utf-8")
            payload = entry / "ast.json.gz"
            with gzip.open(payload, "wt", encoding="utf-8") as stream:
                json.dump({"kind": "TranslationUnitDecl"}, stream)
            status = dependency.stat()
            manifest = {
                "schema": "p101-clang-ast-cache-v3",
                "key": key,
                "payload_bytes": payload.stat().st_size,
                "payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                "dependencies": [
                    {
                        "path": str(dependency),
                        "bytes": status.st_size,
                        "sha256": hashlib.sha256(
                            dependency.read_bytes()
                        ).hexdigest(),
                        "modified_ns": status.st_mtime_ns,
                        "changed_ns": status.st_ctime_ns,
                        "device": status.st_dev,
                        "inode": status.st_ino,
                    }
                ],
            }
            (entry / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            self.assertTrue(MODULE.semantic_usage_available(usage, root))
            dependency.write_text("int demo(void) { return 1; }\n", encoding="utf-8")
            self.assertFalse(MODULE.semantic_usage_available(usage, root))

    def test_impact_selection_is_conservative_and_flows_downstream(self) -> None:
        nodes = [
            self.node("scoped", "pass"),
            self.node("unknown", "pass"),
            self.node("consumer", "pass", dependencies=["scoped"]),
        ]
        nodes[0]["inputs"] = ["libraries/lib_env/**"]
        nodes[0]["inputs_complete"] = True
        nodes[2]["inputs"] = ["programs/p101-inspect/**"]
        nodes[2]["inputs_complete"] = True
        impacted = MODULE.impact_closure(
            ["libraries/lib_env/src/env.c"],
            nodes,
        )
        self.assertEqual(impacted, {"scoped", "unknown", "consumer"})

    def test_complete_impact_uses_only_artifact_dependencies(self) -> None:
        nodes = [
            self.node("ordering-only", "pass"),
            self.node("artifact", "pass"),
            self.node(
                "consumer",
                "pass",
                dependencies=["ordering-only", "artifact"],
            ),
        ]
        nodes[2]["impact_dependencies"] = ["artifact"]
        selected = MODULE.impact_dependency_closure(
            {"consumer"}, {node["id"]: node for node in nodes}
        )
        self.assertEqual(selected, {"consumer", "artifact"})

    def test_affected_false_excludes_global_release_guard(self) -> None:
        node = self.node("release-guard", "pass")
        node["inputs"] = ["**"]
        node["inputs_complete"] = True
        node["affected"] = False
        self.assertEqual(
            MODULE.impact_closure(
                ["libraries/lib_demo/src/demo.c"],
                [node],
                propagate_dependencies=False,
            ),
            set(),
        )

    def test_subset_run_skips_identity_for_unselected_ordering_dependency(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guard = self.node("release-guard", "raise SystemExit(9)")
            consumer = self.node(
                "consumer", "print('selected')", dependencies=["release-guard"]
            )
            document = self.make_document([guard, consumer], jobs=1)
            status = MODULE.run_graph(
                document,
                [consumer],
                root,
                {"out": str(root)},
                False,
                all_nodes=[guard, consumer],
            )
            self.assertEqual(status, 0)
            receipt = json.loads(
                (root / "receipt.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [record["id"] for record in receipt["records"]], ["consumer"]
            )

    def test_affected_discovery_includes_ahead_and_worktree_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            repository = workspace / "libraries" / "lib_demo"
            remote = Path(directory) / "remote.git"
            repository.mkdir(parents=True)
            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "p101 test"],
                cwd=repository,
                check=True,
            )
            tracked = repository / "tracked.c"
            tracked.write_text("int tracked;\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "tracked.c"], cwd=repository, check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "remote", "add", "origin", str(remote)],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "push", "-u", "origin", "main"],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            ahead = repository / "ahead.c"
            ahead.write_text("int ahead;\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "ahead.c"], cwd=repository, check=True
            )
            subprocess.run(
                ["git", "commit", "-m", "ahead"],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            tracked.write_text("int tracked = 1;\n", encoding="utf-8")
            (repository / "untracked.c").write_text(
                "int untracked;\n", encoding="utf-8"
            )

            changed = MODULE.workspace_changed_paths(
                workspace, [repository]
            )
            self.assertEqual(
                changed,
                {
                    "libraries/lib_demo/ahead.c",
                    "libraries/lib_demo/tracked.c",
                    "libraries/lib_demo/untracked.c",
                },
            )

    def test_invalid_input_scope_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["nodes"][0]["inputs"] = ["../outside"]
        with self.assertRaisesRegex(MODULE.GraphError, "invalid inputs"):
            MODULE.validate(document)

    def test_required_complete_input_declaration_is_enforced(self) -> None:
        document = copy.deepcopy(self.document)
        document["nodes"][0].pop("inputs_complete")
        with self.assertRaisesRegex(
            MODULE.GraphError, "lacks a complete admitted-input declaration"
        ):
            MODULE.validate(document)

    def test_nodes_share_one_location_independent_semantic_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = self.node(
                "semantic-cache",
                "import os; "
                "assert os.environ['P101_FACTS_CACHE'] == "
                "os.environ['P101_C_FACTS_CACHE_DIR']",
            )
            document = self.make_document([node], jobs=1)
            identities: list[str] = []
            for index in range(2):
                output = root / f"output-{index}"
                status = MODULE.run_graph(
                    document,
                    [node],
                    output,
                    {},
                    False,
                    cache_directory=root / f"cache-{index}",
                    use_cache=False,
                )
                self.assertEqual(status, 0)
                receipt = json.loads(
                    (output / "receipt.json").read_text(encoding="utf-8")
                )
                identities.append(receipt["records"][0]["input_identity"])
                self.assertTrue(
                    (root / f"cache-{index}" / "semantic-facts").is_dir()
                )
            self.assertEqual(identities[0], identities[1])

    def test_terminal_node_waits_for_every_selected_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "producer-finished"
            producer = self.node(
                "producer",
                "from pathlib import Path; import time; "
                "time.sleep(0.1); "
                f"Path({str(marker)!r}).write_text('done')",
            )
            terminal = self.node(
                "terminal",
                "from pathlib import Path; "
                f"assert Path({str(marker)!r}).read_text() == 'done'",
            )
            terminal["wait_for_selected"] = True
            document = self.make_document([producer, terminal], jobs=2)
            status = MODULE.run_graph(
                document,
                [producer, terminal],
                root,
                {"out": str(root)},
                False,
            )
            self.assertEqual(status, 0)

    @staticmethod
    def node(
        identifier: str,
        code: str,
        *,
        dependencies: list[str] | None = None,
        writes: list[str] | None = None,
        units: dict[str, int] | None = None,
        receipts: list[str] | None = None,
    ) -> dict:
        return {
            "id": identifier,
            "title": identifier,
            "group": "test",
            "required": True,
            "command": [sys.executable, "-c", code],
            "depends_on": dependencies or [],
            "resources": {"writes": writes or [], "units": units or {}},
            "receipts": receipts or [],
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
            self.assertEqual(receipt["schema"], "p101-check-graph-receipt-v2")
            self.assertTrue(receipt["host"]["system"])
            self.assertTrue(receipt["host"]["machine"])
            self.assertTrue(receipt["receipt_digest"].startswith("sha256:"))
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

    def test_interactive_eof_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            document = {
                "schema": "p101-check-graph-v1",
                "does_not_prove": "test receipt only",
            }
            nodes = [
                {
                    "id": "failure",
                    "title": "failure",
                    "required": True,
                    "command": [sys.executable, "-c", "raise SystemExit(7)"],
                    "guarantee": "EOF is handled",
                }
            ]
            with patch("builtins.input", side_effect=EOFError):
                status = MODULE.run_graph(
                    document,
                    nodes,
                    output,
                    {},
                    True,
                )
            self.assertEqual(status, 1)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["records"][0]["outcome"], "tool-error")
            self.assertEqual(receipt["records"][0]["attempts"], 1)

    def test_declared_receipt_is_verified_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            verifier = output / "verify-receipt"
            verifier.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = require-clean ] || exit 2\n"
                "grep -q '^valid$' \"$2\"\n",
                encoding="utf-8",
            )
            verifier.chmod(0o755)
            receipt_path = output / "evidence" / "tool-receipt.json"
            code = (
                "from pathlib import Path; "
                f"p=Path({str(receipt_path)!r}); "
                "p.parent.mkdir(parents=True); p.write_text('valid\\n')"
            )
            nodes = [
                self.node(
                    "receipted",
                    code,
                    writes=["{out}/evidence"],
                    receipts=["{out}/evidence/tool-receipt.json"],
                )
            ]
            document = self.make_document(nodes)
            with patch.dict(
                "os.environ", {"P101_TOOL_RECEIPT": str(verifier)}, clear=False
            ):
                status = MODULE.run_graph(
                    document, nodes, output, {"out": str(output)}, False, jobs=1
                )
            self.assertEqual(status, 0)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(
                receipt["records"][0]["verified_receipts"],
                ["{out}/evidence/tool-receipt.json"],
            )

    def test_missing_declared_receipt_fails_the_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            verifier = output / "verify-receipt"
            verifier.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = require-clean ] || exit 2\n"
                "[ -f \"$2\" ]\n",
                encoding="utf-8",
            )
            verifier.chmod(0o755)
            nodes = [
                self.node(
                    "missing-receipt",
                    "print('command completed')",
                    writes=["{out}/evidence"],
                    receipts=["{out}/evidence/tool-receipt.json"],
                )
            ]
            document = self.make_document(nodes)
            with patch.dict(
                "os.environ", {"P101_TOOL_RECEIPT": str(verifier)}, clear=False
            ):
                status = MODULE.run_graph(
                    document, nodes, output, {"out": str(output)}, False, jobs=1
                )
            self.assertEqual(status, 1)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["records"][0]["outcome"], "tool-error")

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
                    "import os; from pathlib import Path; "
                    f"counter=Path({str(counter)!r}); "
                    "value=int(counter.read_text())+1 if counter.exists() else 1; "
                    "counter.write_text(str(value)); "
                    f"Path({str(artifact)!r}).write_text('evidence'); "
                    "Path(os.environ['P101_SEMANTIC_USAGE_LOG']).write_text('')"
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
                self.assertEqual(
                    (output / "semantic-usage" / "cached.jsonl").read_text(),
                    "",
                )
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

    def test_ordering_dependency_does_not_invalidate_exact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            counter = root / "consumer-count"

            def run(output: Path, producer_value: str) -> dict:
                producer = self.node("producer", f"print({producer_value!r})")
                consumer = self.node(
                    "consumer",
                    "from pathlib import Path; "
                    f"p=Path({str(counter)!r}); "
                    "value=int(p.read_text())+1 if p.exists() else 1; "
                    "p.write_text(str(value))",
                    dependencies=["producer"],
                )
                document = self.make_document([producer, consumer])
                output.mkdir()
                status = MODULE.run_graph(
                    document,
                    [producer, consumer],
                    output,
                    {"out": str(output)},
                    False,
                    jobs=1,
                    cache_directory=cache,
                )
                self.assertEqual(status, 0)
                return json.loads((output / "receipt.json").read_text())

            run(root / "one", "first")
            second = run(root / "two", "changed")
            self.assertEqual(counter.read_text(), "1")
            by_id = {record["id"]: record for record in second["records"]}
            self.assertEqual(by_id["producer"]["outcome"], "clean")
            self.assertEqual(by_id["consumer"]["outcome"], "reused")

    def test_artifact_dependency_invalidates_exact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            counter = root / "consumer-count"

            def run(output: Path, producer_value: str) -> None:
                producer = self.node("producer", f"print({producer_value!r})")
                consumer = self.node(
                    "consumer",
                    "from pathlib import Path; "
                    f"p=Path({str(counter)!r}); "
                    "value=int(p.read_text())+1 if p.exists() else 1; "
                    "p.write_text(str(value))",
                    dependencies=["producer"],
                )
                consumer["impact_dependencies"] = ["producer"]
                document = self.make_document([producer, consumer])
                output.mkdir()
                status = MODULE.run_graph(
                    document,
                    [producer, consumer],
                    output,
                    {"out": str(output)},
                    False,
                    jobs=1,
                    cache_directory=cache,
                )
                self.assertEqual(status, 0)

            run(root / "one", "first")
            run(root / "two", "changed")
            self.assertEqual(counter.read_text(), "2")

    def test_cache_publication_keeps_execution_input_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            cache = root / "cache"
            invalidated = root / "invalidated"
            invalidator = self.node(
                "invalidator",
                (
                    "from pathlib import Path; "
                    f"Path({str(invalidated)!r}).write_text('changed')"
                ),
            )
            invalidator["invalidates_source_identity"] = True
            invalidator["cacheable"] = False
            sibling = self.node(
                "sibling",
                "import time; time.sleep(0.25); print('old identity result')",
            )
            document = self.make_document([invalidator, sibling], jobs=2)

            def source_identity(
                patterns: tuple[str, ...] | None = None,
                _source_indexes: object | None = None,
            ) -> dict[str, object]:
                generation = "new" if invalidated.exists() else "old"
                return {
                    "algorithm": "sha256",
                    "digest": f"sha256:{generation}",
                    "files": 1,
                    "bytes": 1,
                    "repositories": [],
                    "patterns": patterns,
                }

            with patch.object(
                MODULE, "workspace_source_identity", side_effect=source_identity
            ):
                status = MODULE.run_graph(
                    document,
                    [invalidator, sibling],
                    output,
                    {"out": str(output)},
                    False,
                    jobs=2,
                    cache_directory=cache,
                )

            self.assertEqual(status, 0)
            receipt = json.loads((output / "receipt.json").read_text())
            sibling_record = next(
                record
                for record in receipt["records"]
                if record["id"] == "sibling"
            )
            cache_receipts = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in cache.rglob("receipt.json")
            ]
            sibling_cache = next(
                item for item in cache_receipts if item["node"] == "sibling"
            )
            self.assertEqual(
                sibling_cache["input_identity"],
                sibling_record["input_identity"],
            )

    def test_replace_outputs_makes_fresh_rerun_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            artifact = output / "evidence"
            code = (
                "from pathlib import Path; "
                f"p=Path({str(artifact)!r}); "
                "p.mkdir(); (p / 'current').write_text('fresh')"
            )
            node = self.node(
                "fresh-output",
                code,
                writes=["{out}/evidence"],
            )
            node["replace_outputs"] = True
            document = self.make_document([node], jobs=1)

            first_status = MODULE.run_graph(
                document,
                [node],
                output,
                {"out": str(output)},
                False,
                use_cache=False,
            )
            (artifact / "stale").write_text("old", encoding="utf-8")
            second_status = MODULE.run_graph(
                document,
                [node],
                output,
                {"out": str(output)},
                False,
                use_cache=False,
            )

            self.assertEqual(first_status, 0)
            self.assertEqual(second_status, 0)
            self.assertTrue((artifact / "current").is_file())
            self.assertFalse((artifact / "stale").exists())

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

    def test_resume_refuses_a_modified_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            node = self.node("resume", "print('clean')")
            document = self.make_document([node], jobs=1)
            self.assertEqual(
                MODULE.run_graph(document, [node], output, {}, False), 0
            )
            path = output / "receipt.json"
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipt["records"][0]["outcome"] = "reused"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.GraphError, "receipt digest does not match"
            ):
                MODULE.read_previous_receipt(path)

    def test_stack_contract_identity_rejects_modified_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            receipt = {
                "schema": "p101-stack-contract-receipt-v1",
                "passed": True,
                "contract_sha256": "a" * 64,
                "artifact_count": 3,
            }
            receipt["receipt_digest"] = MODULE.canonical_sha256(receipt)
            path = output / "stack-contract-receipt.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertTrue(MODULE.stack_contract_identity(output)["valid"])

            receipt["artifact_count"] = 4
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertFalse(MODULE.stack_contract_identity(output)["valid"])


class WorkspaceSourceIndexTests(unittest.TestCase):
    def test_repository_source_files_ignores_deleted_tracked_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(
                ["git", "init", "-q", os.fspath(repository)], check=True
            )
            source = repository / "deleted.c"
            source.write_text("int deleted(void);\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", os.fspath(repository), "add", "deleted.c"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(repository),
                    "-c",
                    "user.name=p101 test",
                    "-c",
                    "user.email=p101@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            source.unlink()

            self.assertEqual(MODULE.repository_source_files(repository), [])

    def test_content_manifest_rejects_duplicate_or_escaping_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            manifest_path = workspace / "content.json"
            record = {
                "path": "source.c",
                "kind": "file",
                "sha256": "0" * 64,
                "bytes": 0,
                "modified_ns": 0,
                "changed_ns": 0,
                "device": 0,
                "inode": 0,
            }
            for records in ([record, record], [{**record, "path": "../source.c"}]):
                manifest_path.write_text(
                    json.dumps(
                        {
                            "schema": CONTENT_MODULE.SCHEMA,
                            "workspace": os.fspath(workspace),
                            "files": records,
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(CONTENT_MODULE.ContentManifestError):
                    CONTENT_MODULE.ContentManifest(manifest_path)

    def test_content_manifest_reuses_only_unchanged_admitted_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            scripts = workspace / "scripts"
            library = workspace / "libraries" / "lib_demo"
            scripts.mkdir()
            (library / "src").mkdir(parents=True)
            (scripts / "repos.txt").write_text(
                "https://example.invalid/lib_demo.git|"
                "../libraries/lib_demo|c\n",
                encoding="utf-8",
            )
            source = library / "src" / "demo.c"
            source.write_text("int demo(void);\n", encoding="utf-8")
            manifest_path = workspace / "content.json"
            with patch.object(MODULE, "SCRIPTS_ROOT", scripts):
                index = MODULE.WorkspaceSourceIndex()
                index.write_manifest(manifest_path)

            manifest = CONTENT_MODULE.ContentManifest(manifest_path)
            expected = MODULE.hashlib.sha256(source.read_bytes()).digest()
            self.assertEqual(manifest.digest(source), expected)
            source.write_text("int other(void);\n", encoding="utf-8")
            self.assertIsNone(manifest.digest(source))

    def test_scoped_identities_share_one_workspace_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            scripts = workspace / "scripts"
            library = workspace / "libraries" / "lib_demo"
            scripts.mkdir()
            (library / "src").mkdir(parents=True)
            (scripts / "repos.txt").write_text(
                "https://example.invalid/lib_demo.git|"
                "../libraries/lib_demo|c\n",
                encoding="utf-8",
            )
            source = library / "src" / "demo.c"
            source.write_text("int demo(void) { return 1; }\n", encoding="utf-8")
            (library / "README.md").write_text("demo\n", encoding="utf-8")

            holder: dict[str, MODULE.WorkspaceSourceIndex] = {}
            original = MODULE.repository_source_files
            with patch.object(MODULE, "SCRIPTS_ROOT", scripts), patch.object(
                MODULE,
                "repository_source_files",
                wraps=original,
            ) as source_files:
                first = MODULE.workspace_source_identity(
                    ("libraries/lib_demo/src/**",), holder
                )
                second = MODULE.workspace_source_identity(
                    ("libraries/lib_demo/**",), holder
                )
                self.assertEqual(source_files.call_count, 2)
                self.assertEqual(first["files"], 1)
                self.assertGreater(second["files"], first["files"])
                self.assertEqual(
                    set(first["repositories"][0]), {"path"}
                )

                source.write_text(
                    "int demo(void) { return 2; }\n", encoding="utf-8"
                )
                still_snapshotted = MODULE.workspace_source_identity(
                    ("libraries/lib_demo/src/**",), holder
                )
                self.assertEqual(still_snapshotted["digest"], first["digest"])
                holder.clear()
                refreshed = MODULE.workspace_source_identity(
                    ("libraries/lib_demo/src/**",), holder
                )
                self.assertNotEqual(refreshed["digest"], first["digest"])


if __name__ == "__main__":
    unittest.main()
