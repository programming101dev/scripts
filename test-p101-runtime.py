#!/usr/bin/env python3
"""Regression tests for the one-model runtime policy engine."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p101_runtime import analyze_model, write_analysis


def node(
    domain: str,
    kind: str,
    sequence: int,
    *,
    pid: int = 1,
    context: int = 1,
    **values: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": f"{domain}:{pid}:{context}:{sequence}:{kind}",
        "domain": domain,
        "kind": kind,
        "pid": pid,
        "context": context,
        "sequence": sequence,
        "monotonic_ns": sequence * 10,
        "wall_unix_ns": sequence * 100,
        "source": {"file": "student.c", "line": sequence, "function": "demo"},
    }
    value.update(values)
    return value


def model(*nodes: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "p101-run-model-v1",
        "event_schema": "p101-tool-event-format-v4",
        "identity_policy": "pid-context-event-sequence-kind",
        "ordering": "causal-edges-with-per-context-sequence-and-observed-timestamps",
        "summary": {
            "call_nodes": sum(item["domain"] == "call" for item in nodes),
            "resource_nodes": sum(item["domain"] == "resource" for item in nodes),
        },
        "nodes": list(nodes),
        "edges": [],
    }


class RuntimePolicyTests(unittest.TestCase):
    def test_resource_policy_finds_leak_and_double_close(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "resource",
                    "fd-open",
                    1,
                    resource_class="fd",
                    resource_identity="3",
                ),
                node(
                    "resource",
                    "fd-open",
                    2,
                    resource_class="fd",
                    resource_identity="4",
                ),
                node(
                    "resource",
                    "fd-close",
                    3,
                    resource_class="fd",
                    resource_identity="4",
                ),
                node(
                    "resource",
                    "fd-close",
                    4,
                    resource_class="fd",
                    resource_identity="4",
                ),
            )
        )
        identifiers = {
            finding.diagnostic_id for finding in analysis.resource.findings
        }
        self.assertEqual(identifiers, {"P101-FD-001", "P101-FD-002"})
        self.assertEqual(analysis.resource.status, 1)

    def test_failed_exec_cancels_inheritance_finding(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "resource",
                    "exec",
                    1,
                    resource_class="fd",
                    resource_identity="3",
                    cloexec=False,
                    target="/missing",
                ),
                node("resource", "exec-fail", 2, target="/missing"),
            )
        )
        self.assertNotIn(
            "P101-FD-004",
            [finding.diagnostic_id for finding in analysis.resource.findings],
        )

    def test_resource_policy_exercises_every_generic_lifecycle_finding(self) -> None:
        def resource(
            sequence: int,
            operation: str,
            identity: str,
            related: str = "",
        ) -> dict[str, object]:
            return node(
                "resource",
                "resource",
                sequence,
                operation=operation,
                resource_class="teaching-resource",
                resource_identity=identity,
                related_identity=related,
                metadata="",
                size=1,
            )

        analysis = analyze_model(
            model(
                resource(1, "acquire", "leaked"),
                resource(2, "acquire", "released"),
                resource(3, "release", "released"),
                resource(4, "release", "released"),
                resource(5, "release", "unknown"),
                resource(6, "replace", "missing", "replacement"),
                resource(7, "acquire", "duplicate"),
                resource(8, "acquire", "duplicate"),
            )
        )
        identifiers = {
            finding.diagnostic_id for finding in analysis.resource.findings
        }
        self.assertEqual(
            identifiers,
            {
                "P101-RESOURCE-001",
                "P101-RESOURCE-002",
                "P101-RESOURCE-003",
                "P101-RESOURCE-004",
                "P101-RESOURCE-005",
            },
        )

    def test_resource_policy_finds_realloc_of_unknown_pointer(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "resource",
                    "realloc",
                    1,
                    resource_class="allocation",
                    resource_identity="0x10",
                    related_identity="0x20",
                    size=32,
                )
            )
        )
        self.assertEqual(
            {
                finding.diagnostic_id
                for finding in analysis.resource.findings
            },
            {"P101-ALLOC-004"},
        )

    def test_sync_policy_finds_lock_order_cycle(self) -> None:
        def sync(
            sequence: int,
            operation: str,
            identity: str,
            thread: str,
        ) -> dict[str, object]:
            return node(
                "resource",
                "resource",
                sequence,
                operation=operation,
                resource_class="pthread-mutex-held",
                resource_identity=f"{identity}@{thread}",
                related_identity="",
                metadata=thread,
                size=0,
            )

        analysis = analyze_model(
            model(
                sync(1, "acquire", "A", "thread=one"),
                sync(2, "acquire", "B", "thread=one"),
                sync(3, "release", "B", "thread=one"),
                sync(4, "release", "A", "thread=one"),
                sync(5, "acquire", "B", "thread=two"),
                sync(6, "acquire", "A", "thread=two"),
            )
        )
        self.assertIn(
            "P101-SYNC-001",
            [
                finding.diagnostic_id
                for finding in analysis.synchronization.findings
            ],
        )

    def test_trace_policy_finds_unmatched_and_open_calls(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "call",
                    "call-exit",
                    1,
                    name="p101_close",
                    arguments="-",
                    result="0",
                ),
                node(
                    "call",
                    "call-enter",
                    2,
                    name="p101_open",
                    arguments="-",
                    result="-",
                ),
            )
        )
        identifiers = {
            finding.diagnostic_id for finding in analysis.trace.findings
        }
        self.assertEqual(identifiers, {"P101-TRACE-001", "P101-TRACE-003"})

    def test_trace_policy_finds_mismatched_exit(self) -> None:
        analysis = analyze_model(
            model(
                node(
                    "call",
                    "call-enter",
                    1,
                    name="p101_open",
                    arguments="-",
                    result="-",
                ),
                node(
                    "call",
                    "call-exit",
                    2,
                    name="p101_close",
                    arguments="-",
                    result="0",
                ),
            )
        )
        self.assertIn(
            "P101-TRACE-002",
            [finding.diagnostic_id for finding in analysis.trace.findings],
        )

    def test_renderer_writes_every_compatibility_view(self) -> None:
        analysis = analyze_model(model())
        with tempfile.TemporaryDirectory(prefix="p101-runtime-test.") as name:
            output = Path(name)
            write_analysis(output, analysis)
            expected = {
                "resource-report.txt",
                "resource-report.json",
                "concurrency-report.txt",
                "concurrency-report.json",
                "trace-tree.txt",
                "trace-summary.txt",
                "correlated-report.txt",
                "correlated-report.json",
                "resource-lifetimes.md",
            }
            self.assertEqual(
                expected,
                {path.name for path in output.iterdir() if path.is_file()},
            )


if __name__ == "__main__":
    unittest.main()
