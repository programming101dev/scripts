#!/usr/bin/env python3
"""Policy modules and renderers over one validated p101 causal run model."""

from __future__ import annotations

import heapq
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


class RuntimeModelError(Exception):
    """The normalized run model cannot be analyzed safely."""


@dataclass(frozen=True)
class Finding:
    diagnostic_id: str
    policy: str
    message: str
    node_id: str
    source: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.diagnostic_id,
            "severity": "error",
            "policy": self.policy,
            "location": self.source,
            "message": self.message,
            "evidence": {"node": self.node_id, **self.evidence},
        }


@dataclass
class PolicyResult:
    name: str
    findings: list[Finding]
    summary: dict[str, Any]
    text: str
    trouble: list[str] = field(default_factory=list)

    @property
    def status(self) -> int:
        if self.trouble:
            return 2
        return 1 if self.findings else 0


@dataclass
class RuntimeAnalysis:
    model: dict[str, Any]
    resource: PolicyResult
    synchronization: PolicyResult
    trace: PolicyResult
    sanitizer: PolicyResult
    trace_tree: str

    @property
    def findings(self) -> list[Finding]:
        return [
            *self.resource.findings,
            *self.synchronization.findings,
            *self.trace.findings,
            *self.sanitizer.findings,
        ]

    @property
    def status(self) -> int:
        statuses = (
            self.resource.status,
            self.synchronization.status,
            self.trace.status,
            self.sanitizer.status,
        )
        if 2 in statuses:
            return 2
        return 1 if 1 in statuses else 0


RESOURCE_MESSAGES = {
    "P101-FD-001": "descriptor is still open at the end of the run",
    "P101-FD-002": "descriptor was closed more than once",
    "P101-FD-003": "descriptor was closed without an observed acquisition",
    "P101-FD-004": "descriptor would be inherited across exec without CLOEXEC",
    "P101-ALLOC-001": "allocation is still live at the end of the run",
    "P101-ALLOC-002": "allocation was freed more than once",
    "P101-ALLOC-003": "pointer was freed without an observed allocation",
    "P101-ALLOC-004": "realloc referenced a pointer that was not live",
    "P101-RESOURCE-001": "resource is still live at the end of the run",
    "P101-RESOURCE-002": "resource was released more than once",
    "P101-RESOURCE-003": "resource was released without an observed acquisition",
    "P101-RESOURCE-004": "resource replacement referenced a resource that was not live",
    "P101-RESOURCE-005": "resource was acquired while the same identity was already live",
}

SYNC_MESSAGES = {
    "P101-SYNC-001": "lock-order graph contains a cycle",
    "P101-SYNC-002": "live wait-for graph contains a deadlock cycle",
    "P101-SYNC-003": "thread join graph contains a cycle",
}

SANITIZER_MESSAGES = {
    "P101-SAN-001": "AddressSanitizer reported an invalid memory access",
    "P101-SAN-002": "LeakSanitizer reported leaked memory",
    "P101-SAN-003": "UndefinedBehaviorSanitizer reported undefined behavior",
    "P101-SAN-004": "ThreadSanitizer reported a data race or synchronization defect",
}

_SANITIZER_HEADERS = (
    (
        re.compile(r"(?:ERROR:\s*)?AddressSanitizer:\s*(.+)"),
        "P101-SAN-001",
        "address",
    ),
    (
        re.compile(r"(?:ERROR:\s*)?LeakSanitizer:\s*(.+)"),
        "P101-SAN-002",
        "leak",
    ),
    (
        re.compile(r"(?:WARNING:\s*)?ThreadSanitizer:\s*(.+)"),
        "P101-SAN-004",
        "thread",
    ),
    (
        re.compile(r"runtime error:\s*(.+)"),
        "P101-SAN-003",
        "undefined",
    ),
)
_SOURCE_LOCATION = re.compile(
    r"((?:[A-Za-z]:)?[^ \t():]+\.(?:c|cc|cpp|cxx|h|hh|hpp))"
    r":(\d+)(?::\d+)?"
)
_SOURCE_FUNCTION = re.compile(
    r"\b(?:in|at)\s+([A-Za-z_][A-Za-z0-9_:<>~]*)"
)


def load_model(path: Path) -> dict[str, Any]:
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeModelError(f"cannot read run model: {error}") from error
    if not isinstance(model, dict) or model.get("schema") != "p101-run-model-v1":
        raise RuntimeModelError("run model does not use p101-run-model-v1")
    nodes = model.get("nodes")
    edges = model.get("edges")
    lifecycle = model.get("lifecycle")
    if (
        not isinstance(nodes, list)
        or not isinstance(edges, list)
        or not isinstance(lifecycle, dict)
        or not isinstance(lifecycle.get("entries"), list)
        or not isinstance(lifecycle.get("findings"), list)
    ):
        raise RuntimeModelError(
            "run model must contain node, edge, and lifecycle arrays"
        )
    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("id"), str)
            or node.get("domain") not in {"call", "resource"}
            or not isinstance(node.get("pid"), int)
            or not isinstance(node.get("context"), int)
            or not isinstance(node.get("sequence"), int)
            or not isinstance(node.get("source"), dict)
        ):
            raise RuntimeModelError(f"invalid run-model node {index}")
        if node["id"] in node_ids:
            raise RuntimeModelError(f"duplicate run-model node id: {node['id']}")
        node_ids.add(node["id"])
    for index, edge in enumerate(edges):
        if (
            not isinstance(edge, dict)
            or edge.get("from") not in node_ids
            or edge.get("to") not in node_ids
            or not isinstance(edge.get("kind"), str)
        ):
            raise RuntimeModelError(f"invalid run-model edge {index}")
    return model


def _source(node: dict[str, Any]) -> dict[str, Any]:
    source = node["source"]
    return {
        "file": str(source.get("file", "?")),
        "line": int(source.get("line", 0)),
        "function": str(source.get("function", "?")),
    }


def _causal_nodes(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a stable topological order for the admitted run model.

    The two event logs are read independently, so their physical node order is
    not a global execution order.  Explicit model edges carry cross-context
    causality (most importantly fork-before-child), while sequence numbers
    carry order within one execution context.
    """
    nodes = model["nodes"]
    node_index = {node["id"]: index for index, node in enumerate(nodes)}
    outgoing: list[set[int]] = [set() for _ in nodes]
    indegree = [0 for _ in nodes]

    def add_edge(source: int, destination: int) -> None:
        if source == destination or destination in outgoing[source]:
            return
        outgoing[source].add(destination)
        indegree[destination] += 1

    for edge in model["edges"]:
        add_edge(node_index[edge["from"]], node_index[edge["to"]])

    contexts: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        contexts[(node["pid"], node["context"])].append(index)
    for indices in contexts.values():
        indices.sort(key=lambda index: (nodes[index]["sequence"], index))
        for source, destination in zip(indices, indices[1:]):
            add_edge(source, destination)

    ready = [index for index, degree in enumerate(indegree) if degree == 0]
    heapq.heapify(ready)
    ordered: list[dict[str, Any]] = []
    while ready:
        current = heapq.heappop(ready)
        ordered.append(nodes[current])
        for destination in sorted(outgoing[current]):
            indegree[destination] -= 1
            if indegree[destination] == 0:
                heapq.heappush(ready, destination)
    if len(ordered) != len(nodes):
        raise RuntimeModelError("run-model causal graph contains a cycle")
    return ordered


def _finding(
    diagnostic_id: str,
    policy: str,
    node: dict[str, Any],
    **evidence: Any,
) -> Finding:
    return Finding(
        diagnostic_id,
        policy,
        RESOURCE_MESSAGES.get(
            diagnostic_id,
            SYNC_MESSAGES.get(diagnostic_id, "runtime contract was violated"),
        ),
        node["id"],
        _source(node),
        evidence,
    )


def _resource_nodes(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [node for node in model["nodes"] if node["domain"] == "resource"]


def _call_nodes(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [node for node in model["nodes"] if node["domain"] == "call"]


def analyze_resources(model: dict[str, Any]) -> PolicyResult:
    lifecycle = model.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise RuntimeModelError("run model does not contain canonical lifecycle facts")
    entries = lifecycle.get("entries")
    lifecycle_findings = lifecycle.get("findings")
    if not isinstance(entries, list) or not isinstance(lifecycle_findings, list):
        raise RuntimeModelError("run model lifecycle must contain entry and finding arrays")

    nodes = _resource_nodes(model)
    node_order = {node["id"]: index for index, node in enumerate(nodes)}
    direct_nodes = {
        (node["pid"], node["context"], node["sequence"]): node for node in nodes
    }

    def node_for(pid: int, location: dict[str, Any]) -> dict[str, Any]:
        context = int(location.get("context", 0))
        sequence = int(location.get("sequence", 0))
        node = direct_nodes.get((pid, context, sequence))
        if node is not None:
            return node
        for candidate in nodes:
            if (
                candidate.get("kind") == "fork"
                and candidate.get("child_pid") == pid
                and candidate.get("context") == context
                and candidate.get("sequence") == sequence
            ):
                return candidate
        source = location.get("source")
        if not isinstance(source, dict):
            source = {"file": "?", "line": 0, "function": "?"}
        return {
            "id": f"lifecycle:{pid}:{context}:{sequence}",
            "domain": "resource",
            "kind": "lifecycle",
            "pid": pid,
            "context": context,
            "sequence": sequence,
            "source": source,
        }

    def diagnostic_id(kind: str, resource_class: str) -> str:
        if kind == "exec-inherit":
            return "P101-FD-004"
        suffixes = {
            "leak": "001",
            "double-release": "002",
            "stray-release": "003",
            "bad-replace": "004",
            "duplicate-acquire": "005",
        }
        suffix = suffixes.get(kind)
        if suffix is None:
            raise RuntimeModelError(f"unknown lifecycle finding kind: {kind}")
        if resource_class == "fd" and kind in {
            "leak",
            "double-release",
            "stray-release",
        }:
            return f"P101-FD-{suffix}"
        if resource_class == "allocation" and kind in {
            "leak",
            "double-release",
            "stray-release",
            "bad-replace",
        }:
            return f"P101-ALLOC-{suffix}"
        return f"P101-RESOURCE-{suffix}"

    findings: list[Finding] = []
    for index, lifecycle_finding in enumerate(lifecycle_findings):
        if not isinstance(lifecycle_finding, dict):
            raise RuntimeModelError(f"invalid lifecycle finding {index}")
        kind = str(lifecycle_finding.get("kind", ""))
        resource_class = str(lifecycle_finding.get("resource_class", ""))
        identity = str(lifecycle_finding.get("identity", ""))
        pid = int(lifecycle_finding.get("pid", -1))
        location = lifecycle_finding.get("at")
        if not isinstance(location, dict):
            raise RuntimeModelError(f"lifecycle finding {index} has no location")
        node = node_for(pid, location)
        identifier = diagnostic_id(kind, resource_class)
        evidence: dict[str, Any] = {
            "pid": pid,
            "resource_class": resource_class,
            "identity": identity,
        }
        if resource_class == "fd":
            evidence["fd"] = identity
        elif resource_class == "allocation":
            evidence["ptr"] = identity
        previous = lifecycle_finding.get("previous")
        if isinstance(previous, dict):
            evidence["previous_node"] = node_for(pid, previous)["id"]
        if kind == "exec-inherit":
            evidence["target"] = str(node.get("target", ""))
        findings.append(
            Finding(
                identifier,
                "resource",
                RESOURCE_MESSAGES[identifier],
                node["id"],
                _source(node),
                evidence,
            )
        )

    process_metrics: dict[int, dict[str, int]] = defaultdict(
        lambda: {
            "fd_live": 0,
            "fd_peak": 0,
            "heap_live": 0,
            "heap_peak": 0,
            "bytes_live": 0,
            "bytes_peak": 0,
        }
    )
    metric_events: list[tuple[int, int, str, int, int]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RuntimeModelError(f"invalid lifecycle entry {index}")
        pid = int(entry.get("pid", -1))
        resource_class = str(entry.get("resource_class", ""))
        size = int(entry.get("size", 0))
        acquired = entry.get("acquired")
        if not isinstance(acquired, dict):
            raise RuntimeModelError(f"lifecycle entry {index} has no acquisition")
        acquired_node = node_for(pid, acquired)
        acquired_order = node_order.get(acquired_node["id"], len(nodes) + index)
        metric_events.append((acquired_order, 0, resource_class, pid, size))
        released = entry.get("released")
        if isinstance(released, dict):
            released_node = node_for(pid, released)
            released_order = node_order.get(
                released_node["id"], len(nodes) + len(entries) + index
            )
            metric_events.append((released_order, 1, resource_class, pid, size))

    for _, phase, resource_class, pid, size in sorted(metric_events):
        metrics = process_metrics[pid]
        if resource_class == "fd":
            metrics["fd_live"] += 1 if phase == 0 else -1
            metrics["fd_peak"] = max(metrics["fd_peak"], metrics["fd_live"])
        elif resource_class == "allocation":
            metrics["heap_live"] += 1 if phase == 0 else -1
            metrics["bytes_live"] += size if phase == 0 else -size
            metrics["heap_peak"] = max(metrics["heap_peak"], metrics["heap_live"])
            metrics["bytes_peak"] = max(
                metrics["bytes_peak"], metrics["bytes_live"]
            )

    summary = {
        "records": len(_resource_nodes(model)),
        "processes": len(process_metrics),
        "findings": len(findings),
        "process_metrics": [
            {"pid": pid, **metrics}
            for pid, metrics in sorted(process_metrics.items())
        ],
    }
    lines = [
        "p101 resource policy",
        f"records={summary['records']} processes={summary['processes']} findings={len(findings)}",
    ]
    for finding in findings:
        source = finding.source
        lines.append(
            f"{finding.diagnostic_id}: {finding.message} "
            f"[{source['file']}:{source['line']} in {source['function']}()]"
        )
    return PolicyResult("resource", findings, summary, "\n".join(lines) + "\n")


def _reaches(edges: Iterable[tuple[str, str]], start: str, target: str) -> bool:
    if start == target:
        return True
    graph: dict[str, set[str]] = defaultdict(set)
    for source, destination in edges:
        graph[source].add(destination)
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        for destination in graph.get(current, set()):
            if destination == target:
                return True
            pending.append(destination)
    return False


def analyze_synchronization(model: dict[str, Any]) -> PolicyResult:
    held: list[dict[str, Any]] = []
    waits: list[dict[str, Any]] = []
    lock_edges: list[tuple[str, str]] = []
    findings: list[Finding] = []

    def thread(node: dict[str, Any]) -> str:
        metadata = str(node.get("metadata", "")) or "thread=?"
        return f"{node['pid']}:{node['context']}:{metadata}"[:159]

    def physical(node: dict[str, Any]) -> str:
        identity = str(node.get("resource_identity", "?")).split("@", 1)[0]
        return f"{node['pid']}:{node['context']}:{identity}"[:159]

    def add(identifier: str, node: dict[str, Any], first: str, second: str) -> None:
        if any(
            item.diagnostic_id == identifier
            and item.evidence.get("from") == first
            and item.evidence.get("to") == second
            for item in findings
        ):
            return
        findings.append(
            _finding(
                identifier,
                "synchronization",
                node,
                **{"from": first, "to": second},
            )
        )

    sync_nodes = [
        node
        for node in _resource_nodes(model)
        if node["kind"] == "resource"
        and str(node.get("resource_class", "")).startswith("pthread-")
    ]
    for node in sync_nodes:
        resource_class = str(node.get("resource_class"))
        operation = node.get("operation")
        current_thread = thread(node)
        current_resource = physical(node)
        if resource_class in {"pthread-mutex-held", "pthread-rwlock-held"}:
            if operation == "acquire":
                for item in held:
                    if (
                        item["active"]
                        and item["thread"] == current_thread
                        and item["resource"] != current_resource
                    ):
                        edge = (item["resource"], current_resource)
                        if edge not in lock_edges:
                            if _reaches(
                                lock_edges, current_resource, item["resource"]
                            ):
                                add(
                                    "P101-SYNC-001",
                                    node,
                                    item["resource"],
                                    current_resource,
                                )
                            lock_edges.append(edge)
                held.append(
                    {
                        "active": True,
                        "thread": current_thread,
                        "resource": current_resource,
                        "node": node,
                    }
                )
            elif operation == "release":
                for item in reversed(held):
                    if (
                        item["active"]
                        and item["thread"] == current_thread
                        and item["resource"] == current_resource
                    ):
                        item["active"] = False
                        break
        elif resource_class in {
            "pthread-mutex-wait",
            "pthread-rwlock-read-wait",
            "pthread-rwlock-write-wait",
            "pthread-condition-wait",
            "pthread-join-wait",
        }:
            join = resource_class == "pthread-join-wait"
            wait_resource = (
                f"{node['pid']}:{node['context']}:"
                f"{node.get('resource_identity') or 'thread=?'}"
                if join
                else current_resource
            )[:159]
            if operation == "acquire":
                target = (
                    f"{node['pid']}:{node['context']}:"
                    f"{node.get('related_identity') or 'thread=?'}"
                    if join
                    else ""
                )[:159]
                waits.append(
                    {
                        "active": True,
                        "join": join,
                        "thread": current_thread,
                        "resource": wait_resource,
                        "target": target,
                        "node": node,
                    }
                )
            elif operation == "release":
                for item in reversed(waits):
                    if (
                        item["active"]
                        and item["thread"] == current_thread
                        and item["resource"] == wait_resource
                    ):
                        item["active"] = False
                        break

    def wait_edges(joins_only: bool = False) -> list[tuple[str, str]]:
        edges: list[tuple[str, str]] = []
        for wait in waits:
            if not wait["active"] or (joins_only and not wait["join"]):
                continue
            if wait["join"]:
                edges.append((wait["thread"], wait["target"]))
            else:
                for owner in held:
                    if owner["active"] and owner["resource"] == wait["resource"]:
                        edges.append((wait["thread"], owner["thread"]))
        return edges

    all_wait_edges = wait_edges()
    join_edges = wait_edges(True)
    for wait in waits:
        if not wait["active"]:
            continue
        if wait["join"]:
            if _reaches(join_edges, wait["target"], wait["thread"]):
                add(
                    "P101-SYNC-003",
                    wait["node"],
                    wait["thread"],
                    wait["target"],
                )
        else:
            for owner in held:
                if (
                    owner["active"]
                    and owner["resource"] == wait["resource"]
                    and _reaches(
                        all_wait_edges, owner["thread"], wait["thread"]
                    )
                ):
                    add(
                        "P101-SYNC-002",
                        wait["node"],
                        wait["thread"],
                        owner["thread"],
                    )

    summary = {
        "findings": len(findings),
        "lock_order_edges": len(lock_edges),
        "observed_sync_events": len(sync_nodes),
    }
    lines = [
        "p101 synchronization policy: "
        f"{len(findings)} finding{'s' if len(findings) != 1 else ''}, "
        f"{len(lock_edges)} lock-order edge{'s' if len(lock_edges) != 1 else ''}"
    ]
    for finding in findings:
        lines.append(
            f"{finding.diagnostic_id}: {finding.message} "
            f"(event {finding.evidence.get('node', finding.node_id)}): "
            f"{finding.evidence.get('from', '?')} -> "
            f"{finding.evidence.get('to', '?')}"
        )
    return PolicyResult(
        "synchronization", findings, summary, "\n".join(lines) + "\n"
    )


def analyze_trace(model: dict[str, Any]) -> tuple[PolicyResult, str]:
    stacks: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    metrics: dict[tuple[int, int], dict[str, int]] = defaultdict(
        lambda: {
            "max_depth": 0,
            "unmatched_exits": 0,
            "mismatched_exits": 0,
            "open_at_end": 0,
        }
    )
    sites: dict[tuple[str, str, str, int], dict[str, int]] = defaultdict(
        lambda: {
            "enters": 0,
            "exits": 0,
            "results": 0,
            "suspect": 0,
            "timed": 0,
            "total_ns": 0,
            "max_ns": 0,
        }
    )
    findings: list[Finding] = []
    tree: list[str] = []

    for node in _call_nodes(model):
        context = (node["pid"], node["context"])
        stack = stacks[context]
        source = _source(node)
        site_key = (
            str(node.get("name", "")),
            source["file"],
            source["function"],
            source["line"],
        )
        depth = len(stack)
        if node["kind"] == "call-enter":
            site = sites[site_key]
            arguments = str(node.get("arguments", "-"))
            suffix = "" if arguments == "-" else arguments
            tree.append(
                f"#{node['sequence']} pid {node['pid']} context {node['context']} "
                f"{'  ' * depth}{node.get('name', '?')}({suffix})  "
                f"[{source['file']}:{source['line']}]"
            )
            stack.append(node)
            site["enters"] += 1
            metrics[context]["max_depth"] = max(
                metrics[context]["max_depth"], len(stack)
            )
            continue

        result = str(node.get("result", "-"))
        result_text = "" if result == "-" else f" = {result}"
        tree.append(
            f"#{node['sequence']} pid {node['pid']} context {node['context']} "
            f"{'  ' * max(depth - 1, 0)}-> {node.get('name', '?')}{result_text}  "
            f"[{source['file']}:{source['line']}]"
        )
        if not stack:
            site = sites[site_key]
            site["exits"] += 1
            if result != "-":
                site["results"] += 1
                if result in {"NULL", "null", "false", "EOF"} or result.startswith("-"):
                    site["suspect"] += 1
            metrics[context]["unmatched_exits"] += 1
            findings.append(
                Finding(
                    "P101-TRACE-001",
                    "trace",
                    "call exit has no matching active call",
                    node["id"],
                    source,
                    {"call": node.get("name", "")},
                )
            )
            continue
        enter = stack[-1]
        enter_source = _source(enter)
        if (
            enter.get("name") != node.get("name")
            or enter_source["file"] != source["file"]
            or enter_source["function"] != source["function"]
        ):
            site = sites[site_key]
            site["exits"] += 1
            if result != "-":
                site["results"] += 1
                if result in {"NULL", "null", "false", "EOF"} or result.startswith("-"):
                    site["suspect"] += 1
            metrics[context]["mismatched_exits"] += 1
            findings.append(
                Finding(
                    "P101-TRACE-002",
                    "trace",
                    "call exit does not match the active call",
                    node["id"],
                    source,
                    {"active_node": enter["id"], "call": node.get("name", "")},
                )
            )
            continue
        enter_site_key = (
            str(enter.get("name", "")),
            enter_source["file"],
            enter_source["function"],
            enter_source["line"],
        )
        site = sites[enter_site_key]
        site["exits"] += 1
        if result != "-":
            site["results"] += 1
            if result in {"NULL", "null", "false", "EOF"} or result.startswith("-"):
                site["suspect"] += 1
        stack.pop()
        begin = enter.get("monotonic_ns")
        end = node.get("monotonic_ns")
        if isinstance(begin, int) and isinstance(end, int) and end >= begin:
            duration = end - begin
            site["timed"] += 1
            site["total_ns"] += duration
            site["max_ns"] = max(site["max_ns"], duration)

    for context, stack in stacks.items():
        metrics[context]["open_at_end"] = len(stack)
        for node in stack:
            findings.append(
                Finding(
                    "P101-TRACE-003",
                    "trace",
                    "call remained open at the end of the event stream",
                    node["id"],
                    _source(node),
                    {"call": node.get("name", "")},
                )
            )

    lines = [
        "event_schema=p101-tool-event-format-v5 "
        "event_id_policy=wire-sequence-with-derived-input-order",
        f"records={len(_call_nodes(model))} execution_contexts={len(metrics)}",
    ]
    for (pid, context), values in sorted(metrics.items()):
        lines.append(
            f"pid {pid} context {context} max_depth={values['max_depth']} "
            f"open_at_end={values['open_at_end']} "
            f"unmatched_exits={values['unmatched_exits']} "
            f"mismatched_exits={values['mismatched_exits']}"
        )
    lines.append(
        "enter  exit  result  suspect  timed      total-ns        max-ns  where"
    )
    ranked = sorted(
        sites.items(),
        key=lambda item: (-(item[1]["enters"] + item[1]["exits"]), item[0]),
    )
    for (name, file_name, function, line), values in ranked:
        lines.append(
            f"{values['enters']:5d}  {values['exits']:4d}  "
            f"{values['results']:6d}  {values['suspect']:7d}  "
            f"{values['timed']:5d}  {values['total_ns']:12d}  "
            f"{values['max_ns']:12d}  {name} at {file_name}:{line} "
            f"in {function}()"
        )
    summary = {
        "records": len(_call_nodes(model)),
        "execution_contexts": len(metrics),
        "findings": len(findings),
        "contexts": [
            {"pid": pid, "context": context, **values}
            for (pid, context), values in sorted(metrics.items())
        ],
    }
    return (
        PolicyResult("trace", findings, summary, "\n".join(lines) + "\n"),
        "\n".join(tree) + ("\n" if tree else ""),
    )


def _sanitizer_source(lines: list[str], start: int) -> dict[str, Any]:
    for line in lines[start : min(start + 12, len(lines))]:
        match = _SOURCE_LOCATION.search(line)
        if match is not None:
            function = _SOURCE_FUNCTION.search(line)
            return {
                "file": match.group(1),
                "line": int(match.group(2)),
                "function": function.group(1) if function is not None else "?",
            }
    return {"file": "stderr.txt", "line": start + 1, "function": "?"}


def analyze_sanitizers(path: Path) -> PolicyResult:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        return PolicyResult(
            "sanitizer",
            [],
            {"records": 0, "findings": 0},
            "p101 sanitizer policy\nrecords=0 findings=0\n",
            [f"cannot read captured stderr: {error}"],
        )

    findings: list[Finding] = []
    counts: dict[str, int] = defaultdict(int)
    for index, line in enumerate(lines):
        for pattern, diagnostic_id, sanitizer in _SANITIZER_HEADERS:
            match = pattern.search(line)
            if match is None:
                continue
            source = _sanitizer_source(lines, index)
            findings.append(
                Finding(
                    diagnostic_id,
                    "sanitizer",
                    SANITIZER_MESSAGES[diagnostic_id],
                    f"stderr:{index + 1}",
                    source,
                    {
                        "sanitizer": sanitizer,
                        "detail": match.group(1).strip(),
                        "stderr_line": index + 1,
                    },
                )
            )
            counts[sanitizer] += 1
            break

    summary = {
        "records": len(lines),
        "findings": len(findings),
        "address": counts["address"],
        "leak": counts["leak"],
        "undefined": counts["undefined"],
        "thread": counts["thread"],
    }
    text = [
        "p101 sanitizer policy",
        f"records={len(lines)} findings={len(findings)}",
    ]
    for finding in findings:
        source = finding.source
        text.append(
            f"{finding.diagnostic_id}: {finding.message} "
            f"[{source['file']}:{source['line']} in {source['function']}()] "
            f"{finding.evidence['detail']}"
        )
    return PolicyResult(
        "sanitizer", findings, summary, "\n".join(text) + "\n"
    )


def analyze_model(
    model: dict[str, Any], sanitizer: PolicyResult | None = None
) -> RuntimeAnalysis:
    ordered_model = {**model, "nodes": _causal_nodes(model)}
    trace, trace_tree = analyze_trace(ordered_model)
    if sanitizer is None:
        sanitizer = PolicyResult(
            "sanitizer",
            [],
            {
                "records": 0,
                "findings": 0,
                "address": 0,
                "leak": 0,
                "undefined": 0,
                "thread": 0,
            },
            "p101 sanitizer policy\nrecords=0 findings=0\n",
        )
    return RuntimeAnalysis(
        model=ordered_model,
        resource=analyze_resources(ordered_model),
        synchronization=analyze_synchronization(ordered_model),
        trace=trace,
        sanitizer=sanitizer,
        trace_tree=trace_tree,
    )


def _json_document(
    schema: str, findings: list[Finding], summary: dict[str, Any]
) -> str:
    return (
        json.dumps(
            {
                "schema": schema,
                "findings": [finding.as_json() for finding in findings],
                "summary": summary,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _mermaid(model: dict[str, Any]) -> str:
    nodes = {node["id"]: node for node in model["nodes"]}
    lines = [
        "# Resource lifetime graph",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    lifetime_edges = [
        edge for edge in model["edges"] if edge["kind"] == "resource-lifetime"
    ]
    if not lifetime_edges:
        lines.append('  empty["No completed resource lifetimes observed"]')
    for index, edge in enumerate(lifetime_edges):
        source = nodes[edge["from"]]
        target = nodes[edge["to"]]
        source_label = (
            f"{source.get('resource_class', 'resource')} "
            f"{source.get('resource_identity', '?')} acquired"
        ).replace('"', "'")
        target_label = f"{target['kind']}".replace('"', "'")
        lines.append(f'  n{index}a["{source_label}"] --> n{index}b["{target_label}"]')
    lines.extend(["```", ""])
    return "\n".join(lines)


def write_analysis(output: Path, analysis: RuntimeAnalysis) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "resource-report.txt").write_text(
        analysis.resource.text, encoding="utf-8"
    )
    (output / "resource-report.json").write_text(
        _json_document(
            "p101-resource-policy-findings-v1",
            analysis.resource.findings,
            analysis.resource.summary,
        ),
        encoding="utf-8",
    )
    (output / "concurrency-report.txt").write_text(
        analysis.synchronization.text, encoding="utf-8"
    )
    (output / "concurrency-report.json").write_text(
        _json_document(
            "p101-synchronization-policy-findings-v1",
            analysis.synchronization.findings,
            analysis.synchronization.summary,
        ),
        encoding="utf-8",
    )
    (output / "trace-tree.txt").write_text(
        analysis.trace_tree, encoding="utf-8"
    )
    (output / "trace-summary.txt").write_text(
        analysis.trace.text, encoding="utf-8"
    )
    (output / "sanitizer-report.txt").write_text(
        analysis.sanitizer.text, encoding="utf-8"
    )
    (output / "sanitizer-report.json").write_text(
        _json_document(
            "p101-sanitizer-findings-v1",
            analysis.sanitizer.findings,
            analysis.sanitizer.summary,
        ),
        encoding="utf-8",
    )
    findings = analysis.findings
    correlated_summary = {
        "findings": len(findings),
        "resource_findings": len(analysis.resource.findings),
        "synchronization_findings": len(analysis.synchronization.findings),
        "trace_findings": len(analysis.trace.findings),
        "sanitizer_findings": len(analysis.sanitizer.findings),
    }
    (output / "correlated-report.json").write_text(
        _json_document("p101-analysis-findings-v1", findings, correlated_summary),
        encoding="utf-8",
    )
    text = [
        "# p101 correlated runtime report",
        "",
        f"Findings: {len(findings)}",
        "",
    ]
    for finding in findings:
        source = finding.source
        text.append(
            f"- {finding.diagnostic_id}: {finding.message} "
            f"(`{source['file']}:{source['line']}`)"
        )
    text.extend(
        [
            "",
            "This report is bounded by emitted p101 wrapper events.",
            "",
        ]
    )
    (output / "correlated-report.txt").write_text(
        "\n".join(text), encoding="utf-8"
    )
    (output / "resource-lifetimes.md").write_text(
        _mermaid(analysis.model), encoding="utf-8"
    )
