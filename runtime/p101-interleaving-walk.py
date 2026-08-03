#!/usr/bin/env python3
"""Bounded partial-order exploration over a validated p101 run model."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

from p101_runtime import (
    RuntimeModelError,
    analyze_model,
    analyze_synchronization,
    load_model,
)


def _thread(node: dict[str, Any]) -> tuple[int, str]:
    metadata = str(node.get("metadata", ""))
    identity = metadata if metadata.startswith("thread=") else f"context={node['context']}"
    return (
        int(node["pid"]),
        identity,
    )


def _is_sync(node: dict[str, Any]) -> bool:
    return (
        node.get("domain") == "resource"
        and node.get("kind") == "resource"
        and str(node.get("resource_class", "")).startswith("pthread-")
    )


def explore(model: dict[str, Any], limit: int) -> dict[str, Any]:
    ordered = analyze_model(model).model
    baseline = analyze_synchronization(ordered)

    def prefix_findings(candidate_nodes: list[dict[str, Any]]) -> list[Any]:
        findings: list[Any] = []
        seen: set[tuple[str, str, str]] = set()
        for end in range(1, len(candidate_nodes) + 1):
            result = analyze_synchronization({**ordered, "nodes": candidate_nodes[:end]})
            for finding in result.findings:
                key = (
                    finding.diagnostic_id,
                    str(finding.evidence.get("from", "")),
                    str(finding.evidence.get("to", "")),
                )
                if key not in seen:
                    seen.add(key)
                    findings.append(finding)
        return findings

    baseline_keys = {
        (
            item.diagnostic_id,
            str(item.evidence.get("from", "")),
            str(item.evidence.get("to", "")),
        )
        for item in prefix_findings(ordered["nodes"])
    }
    nodes = ordered["nodes"]
    edges = {(edge["from"], edge["to"]) for edge in ordered["edges"]}
    start = tuple(node["id"] for node in nodes)
    by_id = {node["id"]: node for node in nodes}
    pending: deque[tuple[tuple[str, ...], list[dict[str, str]]]] = deque(
        [(start, [])]
    )
    visited = {start}
    schedules: list[dict[str, Any]] = []

    while pending and len(visited) < limit:
        order, swaps = pending.popleft()
        for index in range(len(order) - 1):
            left = by_id[order[index]]
            right = by_id[order[index + 1]]
            if (
                not _is_sync(left)
                or not _is_sync(right)
                or _thread(left) == _thread(right)
                or (left["id"], right["id"]) in edges
            ):
                continue
            candidate_list = list(order)
            candidate_list[index], candidate_list[index + 1] = (
                candidate_list[index + 1],
                candidate_list[index],
            )
            candidate = tuple(candidate_list)
            if candidate in visited:
                continue
            visited.add(candidate)
            candidate_swaps = [
                *swaps,
                {"before": left["id"], "after": right["id"]},
            ]
            candidate_model = {
                **ordered,
                "nodes": [by_id[node_id] for node_id in candidate],
            }
            result_findings = prefix_findings(candidate_model["nodes"])
            new_findings = [
                finding
                for finding in result_findings
                if (
                    finding.diagnostic_id,
                    str(finding.evidence.get("from", "")),
                    str(finding.evidence.get("to", "")),
                )
                not in baseline_keys
            ]
            if new_findings:
                schedules.append(
                    {
                        "swaps": candidate_swaps,
                        "findings": [finding.as_json() for finding in new_findings],
                    }
                )
            pending.append((candidate, candidate_swaps))
            if len(visited) >= limit:
                break

    return {
        "schema": "p101-interleaving-walk-v1",
        "admitted_model": str(model.get("schema", "")),
        "summary": {
            "schedules_explored": len(visited),
            "counterexample_schedules": len(schedules),
            "baseline_findings": len(baseline.findings),
            "bound": limit,
        },
        "counterexamples": schedules,
        "blind_spots": [
            "explores reorderings of observed synchronization events only",
            "does not execute the program or invent unobserved branches",
            "preserves recorded causal edges and each thread's local order",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explore bounded reorderings of observed p101 synchronization events."
    )
    parser.add_argument("model", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--schedules", type=int, default=256)
    args = parser.parse_args()
    if args.schedules < 1:
        parser.error("--schedules must be positive")
    try:
        report = explore(load_model(args.model), args.schedules)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, RuntimeModelError, ValueError) as error:
        print(f"p101 interleaving walk: {error}", file=sys.stderr)
        return 2
    summary = report["summary"]
    print(
        "p101 interleaving walk: "
        f"{summary['schedules_explored']} schedules, "
        f"{summary['counterexample_schedules']} counterexamples"
    )
    for counterexample in report["counterexamples"]:
        for finding in counterexample["findings"]:
            location = finding["location"]
            print(
                f"{finding['id']}: {finding['message']} "
                f"[{location['file']}:{location['line']}]"
            )
    return 1 if report["counterexamples"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
