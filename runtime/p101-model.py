#!/usr/bin/env python3
"""Verify and compare canonical p101 analysis models."""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from p101_receipt import (
    ANALYSIS_FILES,
    CAPTURE_FILES,
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_RECORDS,
    ReceiptError,
    fingerprint_file,
    parse_fingerprint_line,
    parse_nonnegative,
)


EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_TROUBLE = 2

ANALYSIS_STATUS_ROLES = {
    "event_model",
    "resource_policy",
    "sync_policy",
    "trace_policy",
    "sanitizer_policy",
    "report_renderer",
}
ANALYSIS_TOOL_ROLES = {
    "event_model",
    "analyze_driver",
}
RULE_KINDS = {
    "forbid-finding",
    "require-finding",
    "forbid-call",
    "require-call",
    "require-edge",
    "require-resource",
}
MAX_RULES_PER_PACK = 1024
MAX_RULE_PACK_BYTES = 1024 * 1024


class ModelError(Exception):
    """An analysis model or expectation contract is invalid."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ModelError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ModelError(f"top-level JSON value is not an object: {path}")
    return value


def analysis_dir(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.is_dir():
        raise ModelError(f"analysis directory not found: {path}")
    return path


def validate_model(directory: Path) -> dict[str, Any]:
    model = load_json(directory / "run-model.json")
    if model.get("schema") != "p101-run-model-v1":
        raise ModelError("run-model.json does not use p101-run-model-v1")
    if model.get("event_schema") != "p101-tool-event-format-v5":
        raise ModelError("run-model.json does not admit event protocol v5")
    if model.get("identity_policy") != "run-pid-context-event-sequence-kind":
        raise ModelError("run-model.json has an unknown identity policy")
    if (
        model.get("ordering")
        != "causal-edges-with-per-context-sequence-and-observed-timestamps"
    ):
        raise ModelError("run-model.json has an unknown ordering contract")
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
        raise ModelError(
            "run model nodes, edges, lifecycle entries, and lifecycle findings must be arrays"
        )
    node_ids: set[str] = set()
    observation_ids: set[tuple[str, int, int, int]] = set()
    node_by_id: dict[str, dict[str, Any]] = {}
    domain_counts = Counter()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise ModelError(f"node {index} has no string id")
        domain = node.get("domain")
        kind = node.get("kind")
        run_id = node.get("run_id")
        pid = node.get("pid")
        context = node.get("context")
        sequence = node.get("sequence")
        if domain not in {"call", "resource"}:
            raise ModelError(f"node {index} has an unknown domain")
        if not isinstance(kind, str):
            raise ModelError(f"node {index} has no string kind")
        if not isinstance(run_id, str) or not run_id:
            raise ModelError(f"node {index} has no run identity")
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or not isinstance(context, int)
            or isinstance(context, bool)
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or context < 0
            or sequence < 1
        ):
            raise ModelError(f"node {index} has invalid observation identity")
        if domain == "call":
            if kind not in {"call-enter", "call-exit"}:
                raise ModelError(f"call node {index} has an unknown kind")
            if not all(
                isinstance(node.get(key), str)
                for key in ("name", "arguments", "result")
            ):
                raise ModelError(f"call node {index} has invalid call metadata")
        else:
            resource_kinds = {
                "fd-open",
                "fd-close",
                "alloc",
                "free",
                "realloc",
                "fork",
                "spawn",
                "exec",
                "exec-fail",
                "resource",
            }
            if kind not in resource_kinds:
                raise ModelError(f"resource node {index} has an unknown kind")
            if kind in {"fd-open", "fd-close", "exec"} and (
                node.get("resource_class") != "fd"
                or not isinstance(node.get("resource_identity"), str)
            ):
                raise ModelError(f"resource node {index} has invalid fd identity")
            if kind in {"alloc", "free", "realloc"} and (
                node.get("resource_class") != "allocation"
                or not isinstance(node.get("resource_identity"), str)
                or not isinstance(node.get("size"), int)
                or isinstance(node.get("size"), bool)
                or node["size"] < 0
            ):
                raise ModelError(
                    f"resource node {index} has invalid allocation metadata"
                )
            if kind == "realloc" and not isinstance(
                node.get("related_identity"), str
            ):
                raise ModelError(
                    f"resource node {index} has invalid replacement identity"
                )
            if kind in {"fork", "spawn"} and (
                not isinstance(node.get("child_pid"), int)
                or isinstance(node.get("child_pid"), bool)
            ):
                raise ModelError(f"resource node {index} has invalid child pid")
            if kind in {"spawn", "exec", "exec-fail"} and not isinstance(
                node.get("target"), str
            ):
                raise ModelError(f"resource node {index} has invalid target")
            if kind == "exec" and not isinstance(node.get("cloexec"), bool):
                raise ModelError(f"resource node {index} has invalid cloexec state")
            if kind == "resource" and (
                node.get("operation")
                not in {"acquire", "release", "replace", "transfer"}
                or not isinstance(node.get("resource_class"), str)
                or not node["resource_class"]
                or not isinstance(node.get("resource_identity"), str)
                or not isinstance(node.get("related_identity"), str)
                or not isinstance(node.get("metadata"), str)
                or not isinstance(node.get("size"), int)
                or isinstance(node.get("size"), bool)
                or node["size"] < 0
            ):
                raise ModelError(
                    f"resource node {index} has invalid generic resource metadata"
                )
        for time_key in ("monotonic_ns", "wall_unix_ns"):
            time_value = node.get(time_key)
            if time_value is not None and (
                not isinstance(time_value, int)
                or isinstance(time_value, bool)
                or time_value < 0
            ):
                raise ModelError(f"node {index} has invalid {time_key}")
        source = node.get("source")
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("file"), str)
            or not isinstance(source.get("function"), str)
            or not isinstance(source.get("line"), int)
            or isinstance(source.get("line"), bool)
            or source["line"] < 0
        ):
            raise ModelError(f"node {index} has invalid source metadata")
        node_id = node["id"]
        if node_id in node_ids:
            raise ModelError(f"duplicate node id: {node_id}")
        expected_id = f"{domain}:{run_id}:{pid}:{context}:{sequence}:{kind}"
        if node_id != expected_id:
            raise ModelError(
                f"node {index} id does not match its observation identity"
            )
        observation_id = (run_id, pid, context, sequence)
        if observation_id in observation_ids:
            raise ModelError(
                f"duplicate observation identity: {pid}:{context}:{sequence}"
            )
        node_ids.add(node_id)
        observation_ids.add(observation_id)
        node_by_id[node_id] = node
        domain_counts[domain] += 1
    summary = model.get("summary")
    if (
        not isinstance(summary, dict)
        or summary.get("call_nodes") != domain_counts["call"]
        or summary.get("resource_nodes") != domain_counts["resource"]
    ):
        raise ModelError("run model summary does not match its nodes")
    edge_ids: set[tuple[str, str, str]] = set()
    edge_kinds = {
        "call-parent",
        "call-return",
        "call-caused-event",
        "resource-lifetime",
        "process-child-event",
    }
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ModelError(f"edge {index} is not an object")
        if not all(isinstance(edge.get(key), str) for key in ("kind", "from", "to")):
            raise ModelError(f"edge {index} lacks kind/from/to")
        if edge["from"] not in node_ids or edge["to"] not in node_ids:
            raise ModelError(f"edge {index} references a missing node")
        if edge["kind"] not in edge_kinds:
            raise ModelError(f"edge {index} has an unknown kind")
        edge_id = (edge["kind"], edge["from"], edge["to"])
        if edge_id in edge_ids:
            raise ModelError(f"duplicate edge: {edge_id}")
        edge_ids.add(edge_id)
        source_node = node_by_id[edge["from"]]
        target_node = node_by_id[edge["to"]]
        source_domain = source_node["domain"]
        target_domain = target_node["domain"]
        expected_domains = {
            "call-parent": ("call", "call"),
            "call-return": ("call", "call"),
            "call-caused-event": ("call", "resource"),
            "resource-lifetime": ("resource", "resource"),
        }
        if edge["kind"] != "process-child-event" and (
            source_domain,
            target_domain,
        ) != expected_domains[edge["kind"]]:
            raise ModelError(f"edge {index} joins incompatible node domains")
        if edge["kind"] == "process-child-event" and (
            source_domain != "resource"
            or source_node["kind"] not in {"fork", "spawn"}
            or target_node["pid"] != source_node["child_pid"]
        ):
            raise ModelError(f"edge {index} is not a valid child-process edge")
        if edge["kind"] == "call-parent" and (
            source_node["kind"] != "call-enter"
            or target_node["kind"] != "call-enter"
        ):
            raise ModelError(f"edge {index} is not an enter-to-enter parent edge")
        if edge["kind"] == "call-return" and (
            source_node["kind"] != "call-enter"
            or target_node["kind"] != "call-exit"
        ):
            raise ModelError(f"edge {index} is not an enter-to-exit return edge")
        if edge["kind"] == "call-caused-event" and source_node["kind"] != "call-enter":
            raise ModelError(f"edge {index} is not caused by a call-enter node")
        same_context = (
            source_node["pid"] == target_node["pid"]
            and source_node["context"] == target_node["context"]
        )
        if edge["kind"] not in {
            "resource-lifetime",
            "process-child-event",
        } and not same_context:
            raise ModelError(f"edge {index} crosses an observation context")
        if same_context and source_node["sequence"] >= target_node["sequence"]:
            raise ModelError(f"edge {index} does not move forward in sequence")
    return model


def finding_documents(directory: Path) -> list[dict[str, Any]]:
    schemas = {
        "correlated-report.json": "p101-analysis-findings-v1",
        "resource-report.json": "p101-resource-policy-findings-v1",
        "concurrency-report.json": "p101-synchronization-policy-findings-v1",
    }
    loaded: dict[str, dict[str, Any]] = {}
    for name, schema in schemas.items():
        path = directory / name
        if not path.is_file():
            raise ModelError(f"missing analysis artifact: {path}")
        document = load_json(path)
        if document.get("schema") != schema:
            raise ModelError(f"{name} does not use {schema}")
        loaded[name] = document
    # The correlated document is the canonical finding set. Domain documents
    # are schema-checked above but are views of the same policy results.
    return [loaded["correlated-report.json"]]


def semantic_finding(finding: dict[str, Any]) -> str:
    finding_id = str(finding.get("id", "?"))
    location = finding.get("location", {})
    if not isinstance(location, dict):
        location = {}
    path = location.get("path", location.get("file", finding.get("file", "?")))
    line = location.get("line", finding.get("line", 0))
    function = location.get("function", finding.get("function", "?"))
    resource_class = finding.get("resource_class", "")
    kind = finding.get("kind", "")
    return json.dumps(
        [finding_id, path, line, function, resource_class, kind],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def semantic_node(node: dict[str, Any]) -> str:
    source = node.get("source", {})
    if not isinstance(source, dict):
        source = {}
    return json.dumps(
        [
            node.get("domain", "?"),
            node.get("kind", "?"),
            node.get("name", ""),
            node.get("arguments", ""),
            node.get("result", ""),
            node.get("resource_class", ""),
            node.get("operation", ""),
            node.get("size", 0),
            node.get("target", ""),
            node.get("cloexec", False),
            node.get("metadata", ""),
            source.get("file", "?"),
            source.get("line", 0),
            source.get("function", "?"),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def semantic_graph(model: dict[str, Any]) -> tuple[Counter[str], Counter[str]]:
    node_by_id = {node["id"]: semantic_node(node) for node in model["nodes"]}
    nodes = Counter(node_by_id.values())
    edges = Counter(
        json.dumps(
            [edge["kind"], node_by_id[edge["from"]], node_by_id[edge["to"]]],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        for edge in model["edges"]
    )
    return nodes, edges


def findings(directory: Path) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    semantic: list[str] = []
    for document in finding_documents(directory):
        values = document.get("findings", [])
        if not isinstance(values, list):
            raise ModelError("findings must be an array")
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise ModelError("every finding must have a string id")
            ids.append(value["id"])
            semantic.append(semantic_finding(value))
    return ids, semantic


def finding_values(directory: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for document in finding_documents(directory):
        document_values = document.get("findings", [])
        if not isinstance(document_values, list):
            raise ModelError("findings must be an array")
        for value in document_values:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise ModelError("every finding must have a string id")
            values.append(value)
    return values


def rule_pack_path(name_or_path: str) -> Path:
    supplied = Path(name_or_path).expanduser()
    if supplied.is_file():
        return supplied.resolve()
    candidate = Path(__file__).resolve().parent.parent / "rules" / f"{name_or_path}.json"
    if candidate.is_file():
        return candidate
    raise ModelError(f"rule pack not found: {name_or_path}")


def load_rule_pack(name_or_path: str) -> dict[str, Any]:
    path = rule_pack_path(name_or_path)
    try:
        if path.stat().st_size > MAX_RULE_PACK_BYTES:
            raise ModelError(
                f"{path} exceeds the {MAX_RULE_PACK_BYTES}-byte safety limit"
            )
    except OSError as error:
        raise ModelError(f"cannot inspect rule pack {path}: {error}") from error
    pack = load_json(path)
    if pack.get("schema") != "p101-rule-pack-v1":
        raise ModelError(f"{path} does not use p101-rule-pack-v1")
    if not isinstance(pack.get("name"), str) or not pack["name"]:
        raise ModelError(f"{path} has no rule-pack name")
    rules = pack.get("rules")
    if not isinstance(rules, list):
        raise ModelError(f"{path} has no rules array")
    if len(rules) > MAX_RULES_PER_PACK:
        raise ModelError(
            f"{path} exceeds the {MAX_RULES_PER_PACK}-rule safety limit"
        )
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ModelError(f"rule {index} is not an object")
        if set(rule) != {"id", "kind", "pattern", "title", "lesson"}:
            raise ModelError(f"rule {index} has unknown or missing fields")
        if not all(
            isinstance(rule.get(key), str) and rule[key]
            for key in ("id", "kind", "pattern", "title", "lesson")
        ):
            raise ModelError(f"rule {index} has an empty field")
        if rule["id"] in seen:
            raise ModelError(f"duplicate rule id: {rule['id']}")
        if rule["kind"] not in RULE_KINDS:
            raise ModelError(f"unsupported rule kind: {rule['kind']}")
        seen.add(rule["id"])
    pack["_path"] = str(path)
    return pack


def rule_evidence(
    rule: dict[str, str],
    model: dict[str, Any],
    observed_findings: list[dict[str, Any]],
) -> list[str]:
    pattern = rule["pattern"]
    kind = rule["kind"]
    finding_ids = [value["id"] for value in observed_findings]
    call_nodes = [
        node
        for node in model["nodes"]
        if node["domain"] == "call" and node["kind"] == "call-enter"
    ]
    resource_nodes = [
        node for node in model["nodes"] if node["domain"] == "resource"
    ]
    if kind in {"forbid-finding", "require-finding"}:
        matches = [
            finding_id
            for finding_id in finding_ids
            if fnmatch.fnmatchcase(finding_id, pattern)
        ]
    elif kind in {"forbid-call", "require-call"}:
        matches = [
            node["id"]
            for node in call_nodes
            if fnmatch.fnmatchcase(node["name"], pattern)
        ]
    elif kind == "require-edge":
        matches = [
            f"{edge['from']} -> {edge['to']}"
            for edge in model["edges"]
            if fnmatch.fnmatchcase(edge["kind"], pattern)
        ]
    else:
        matches = [
            node["id"]
            for node in resource_nodes
            if fnmatch.fnmatchcase(str(node.get("resource_class", "")), pattern)
        ]
    return matches


def check_rules(directory: Path, pack_names: list[str], json_output: bool) -> int:
    receipt_result(directory)
    model = validate_model(directory)
    observed_findings = finding_values(directory)
    packs = [load_rule_pack(name) for name in pack_names]
    violations: list[dict[str, Any]] = []
    for pack in packs:
        for rule_value in pack["rules"]:
            rule: dict[str, str] = rule_value
            matches = rule_evidence(rule, model, observed_findings)
            violated = (
                bool(matches)
                if rule["kind"].startswith("forbid-")
                else not bool(matches)
            )
            if violated:
                violations.append(
                    {
                        "id": rule["id"],
                        "pack": pack["name"],
                        "title": rule["title"],
                        "lesson": rule["lesson"],
                        "evidence": matches[:20],
                    }
                )
    if json_output:
        print(
            json.dumps(
                {
                    "schema": "p101-rule-check-v1",
                    "result": "findings" if violations else "clean",
                    "packs": [pack["name"] for pack in packs],
                    "violations": violations,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            f"p101 check: packs={','.join(pack['name'] for pack in packs)} "
            f"nodes={len(model['nodes'])} violations={len(violations)}"
        )
        for violation in violations:
            print(f"{violation['id']}: {violation['title']}")
            for evidence in violation["evidence"]:
                print(f"  evidence: {evidence}")
            print(f"  lesson: {violation['lesson']}")
    return EXIT_FINDINGS if violations else EXIT_CLEAN


def finding_by_id(
    observed_findings: list[dict[str, Any]], finding_id: str
) -> dict[str, Any]:
    matches = [value for value in observed_findings if value["id"] == finding_id]
    if not matches:
        raise ModelError(f"finding not present: {finding_id}")
    if len(matches) > 1:
        raise ModelError(f"finding id is not unique in this analysis: {finding_id}")
    return matches[0]


def causal_neighborhood(
    model: dict[str, Any], finding: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    location = finding.get("location", {})
    if not isinstance(location, dict):
        location = {}
    path = location.get("path", location.get("file", finding.get("file", "")))
    line = location.get("line", finding.get("line", 0))
    function = location.get("function", finding.get("function", ""))
    source_nodes = [
        node
        for node in model["nodes"]
        if isinstance(node.get("source"), dict)
    ]
    seeds = {
        node["id"]
        for node in source_nodes
        if node["source"].get("file") == path
        and node["source"].get("line") == line
        and node["source"].get("function") == function
    }
    if not seeds:
        seeds = {
            node["id"]
            for node in source_nodes
            if node["source"].get("file") == path
            and node["source"].get("function") == function
        }
    if not seeds:
        seeds = {
            node["id"]
            for node in source_nodes
            if node["source"].get("file") == path
        }
    selected = set(seeds)
    selected_edges: list[dict[str, Any]] = []
    for _ in range(3):
        changed = False
        for edge in model["edges"]:
            if edge["from"] in selected or edge["to"] in selected:
                if edge not in selected_edges:
                    selected_edges.append(edge)
                for endpoint in (edge["from"], edge["to"]):
                    if endpoint not in selected:
                        selected.add(endpoint)
                        changed = True
        if not changed:
            break
    nodes = [node for node in model["nodes"] if node["id"] in selected]
    return nodes[:40], selected_edges[:60]


def explain(directory: Path, finding_id: str) -> int:
    receipt_result(directory)
    model = validate_model(directory)
    finding = finding_by_id(finding_values(directory), finding_id)
    nodes, edges = causal_neighborhood(model, finding)
    location = finding.get("location", {})
    print(f"# {finding_id}: {finding.get('message', finding.get('kind', 'finding'))}")
    if isinstance(location, dict):
        print(
            f"source={location.get('path', location.get('file', '?'))}:{location.get('line', 0)} "
            f"function={location.get('function', '?')}"
        )
    print(f"causal_nodes={len(nodes)} causal_edges={len(edges)}")
    lesson = finding.get("lesson")
    primary = lesson.get("primary") if isinstance(lesson, dict) else None
    if isinstance(primary, dict):
        print(
            f"lesson={primary.get('lesson_id', '?')} "
            f"{primary.get('title', 'lesson')} {primary.get('url', '')}"
        )
    for node in nodes:
        source = node["source"]
        label = node.get("name", node.get("resource_class", node["kind"]))
        print(
            f"node {node['id']} {label} "
            f"{source['file']}:{source['line']}:{source['function']}"
        )
    for edge in edges:
        print(f"edge {edge['kind']} {edge['from']} -> {edge['to']}")
    if not nodes:
        print("note: no run-model node matched the finding source")
    return EXIT_CLEAN


def print_delta(prefix: str, values: list[str], limit: int = 20) -> None:
    for value in sorted(values)[:limit]:
        print(f"{prefix} {value}")
    omitted = len(values) - min(len(values), limit)
    if omitted > 0:
        print(f"{prefix} ... {omitted} more")


def parse_expectations(path: Path) -> list[tuple[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ModelError(f"cannot read expectations: {error}") from error
    if not lines or lines[0].strip() != "p101-expectations-v1":
        raise ModelError("expectations must begin with p101-expectations-v1")
    result: list[tuple[str, str]] = []
    for line_number, raw in enumerate(lines[1:], 2):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ModelError(f"expectation line {line_number} has no '='")
        key, value = line.split("=", 1)
        if key not in {
            "result",
            "finding_count",
            "forbid",
            "require",
            "require_edge",
            "forbid_call",
            "require_call",
            "require_resource",
            "min_edges",
            "min_nodes",
        }:
            raise ModelError(f"unknown expectation on line {line_number}: {key}")
        result.append((key, value))
    return result


def receipt_result(directory: Path) -> str:
    path = directory / "analysis-receipt.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ModelError(f"cannot read analysis receipt: {error}") from error
    if not lines or lines[0] != "p101 analysis receipt":
        raise ModelError("missing p101 analysis receipt")
    scalar: dict[str, str] = {}
    artifacts = {}
    missing_artifacts: set[str] = set()
    inputs = {}
    statuses: dict[str, int] = {}
    tools: set[str] = set()
    allowed_artifacts = set(ANALYSIS_FILES)
    for line_number, line in enumerate(lines[1:], 2):
        if line.startswith("artifact="):
            try:
                role, fingerprint = parse_fingerprint_line(
                    line, line_number, "artifact", allowed_artifacts
                )
            except ReceiptError as error:
                raise ModelError(str(error)) from error
            if role in artifacts or role in missing_artifacts:
                raise ModelError(f"duplicate artifact role: {role}")
            artifacts[role] = fingerprint
        elif line.startswith("input="):
            try:
                role, fingerprint = parse_fingerprint_line(
                    line,
                    line_number,
                    "input",
                    {"receipt", *CAPTURE_FILES.keys()},
                )
            except ReceiptError as error:
                raise ModelError(str(error)) from error
            if role in inputs:
                raise ModelError(f"duplicate input role: {role}")
            inputs[role] = fingerprint
        elif line.startswith("tool="):
            fields = {}
            for item in line.split("\t"):
                if "=" not in item:
                    raise ModelError(f"malformed tool on line {line_number}")
                key, value = item.split("=", 1)
                if not key or key in fields:
                    raise ModelError(
                        f"duplicate tool field on line {line_number}"
                    )
                fields[key] = value
            if set(fields) != {"tool", "path_json", "version", "bytes"}:
                raise ModelError(f"malformed tool on line {line_number}")
            role = fields["tool"]
            if role not in ANALYSIS_TOOL_ROLES:
                raise ModelError(f"unsupported analysis tool role: {role}")
            if role in tools:
                raise ModelError(f"duplicate analysis tool role: {role}")
            try:
                path_value = json.loads(fields["path_json"])
                parse_nonnegative(fields["bytes"], "bytes")
            except (json.JSONDecodeError, ReceiptError) as error:
                raise ModelError(f"malformed tool on line {line_number}") from error
            if not isinstance(path_value, str) or not fields["version"].startswith(
                "binary-fnv1a64:"
            ):
                raise ModelError(f"malformed tool on line {line_number}")
            tools.add(role)
        elif line.startswith("artifact_missing="):
            role = line.split("=", 1)[1]
            if role not in allowed_artifacts:
                raise ModelError(f"unsupported missing artifact role: {role}")
            if role in artifacts or role in missing_artifacts:
                raise ModelError(f"duplicate artifact role: {role}")
            missing_artifacts.add(role)
        elif line.startswith("status="):
            fields = {}
            for item in line.split("\t"):
                if "=" not in item:
                    raise ModelError(f"malformed status on line {line_number}")
                key, value = item.split("=", 1)
                if not key or key in fields:
                    raise ModelError(
                        f"duplicate status field on line {line_number}"
                    )
                fields[key] = value
            if set(fields) not in ({"status", "exit"}, {"status", "exit", "signal"}):
                raise ModelError(f"malformed status on line {line_number}")
            role = fields["status"]
            if role not in ANALYSIS_STATUS_ROLES | {
                "capture_stability",
                "lesson_catalog_stability",
                "tool_stability",
            }:
                raise ModelError(f"unsupported analysis status: {role}")
            if role in statuses:
                raise ModelError(f"duplicate analysis status: {role}")
            try:
                statuses[role] = parse_nonnegative(fields["exit"], "exit")
                if "signal" in fields:
                    parse_nonnegative(fields["signal"], "signal")
            except ReceiptError as error:
                raise ModelError(str(error)) from error
        elif "=" in line:
            key, value = line.split("=", 1)
            if key in scalar:
                raise ModelError(f"duplicate analysis receipt key: {key}")
            scalar[key] = value
        elif line:
            raise ModelError(f"malformed analysis receipt line {line_number}")
    if scalar.get("schema") != "p101-analysis-receipt-v1":
        raise ModelError("analysis receipt does not use p101-analysis-receipt-v1")
    if scalar.get("fingerprint") != "fnv1a64":
        raise ModelError("analysis receipt does not use fnv1a64 fingerprints")
    if scalar.get("fingerprint_security") != "change-detection-only":
        raise ModelError("analysis receipt overstates fingerprint security")
    result = scalar.get("result")
    if result not in {"clean", "findings", "trouble"}:
        raise ModelError("analysis receipt has no unique valid result")
    verification = scalar.get("capture_verification")
    if verification not in {"verified", "overridden", "failed-after-analysis"}:
        raise ModelError("analysis receipt has no valid capture verification state")
    if not {"resources", "calls"}.issubset(inputs):
        raise ModelError("analysis receipt lacks resource/call input fingerprints")
    if tools != ANALYSIS_TOOL_ROLES:
        missing_tools = sorted(ANALYSIS_TOOL_ROLES - tools)
        raise ModelError(
            "analysis receipt tool list is incomplete: " + ", ".join(missing_tools)
        )
    expected_roles = set(ANALYSIS_FILES)
    if artifacts.keys() | missing_artifacts != expected_roles:
        missing = sorted(expected_roles - (artifacts.keys() | missing_artifacts))
        raise ModelError(
            "analysis receipt artifact list is incomplete: " + ", ".join(missing)
        )
    if result != "trouble" and missing_artifacts:
        raise ModelError(
            "successful analysis is missing artifacts: "
            + ", ".join(sorted(missing_artifacts))
        )
    for role, expected in artifacts.items():
        try:
            actual = fingerprint_file(
                directory / ANALYSIS_FILES[role],
                maximum_bytes=MAX_ARTIFACT_BYTES,
                maximum_records=MAX_ARTIFACT_RECORDS,
            )
        except ReceiptError as error:
            raise ModelError(str(error)) from error
        if actual != expected:
            raise ModelError(
                f"analysis artifact fingerprint mismatch: {ANALYSIS_FILES[role]}"
            )
    if not ANALYSIS_STATUS_ROLES.issubset(statuses):
        missing_statuses = sorted(ANALYSIS_STATUS_ROLES - statuses.keys())
        raise ModelError(
            "analysis receipt statuses are incomplete: "
            + ", ".join(missing_statuses)
        )
    status_values = list(statuses.values())
    expected_result = (
        "trouble"
        if any(status not in {EXIT_CLEAN, EXIT_FINDINGS} for status in status_values)
        else (
            "findings"
            if any(status == EXIT_FINDINGS for status in status_values)
            else "clean"
        )
    )
    if result != expected_result:
        raise ModelError(
            f"analysis result/status mismatch: result={result}, "
            f"statuses imply {expected_result}"
        )
    return result


def verify(directory: Path, expectation_path: Path | None) -> int:
    actual_result = receipt_result(directory)
    model = validate_model(directory)
    finding_ids, _ = findings(directory)
    failures: list[str] = []
    if expectation_path is not None:
        edge_kinds = Counter(edge["kind"] for edge in model["edges"])
        call_names = [
            node["name"]
            for node in model["nodes"]
            if node["domain"] == "call" and node["kind"] == "call-enter"
        ]
        resource_classes = [
            str(node.get("resource_class", ""))
            for node in model["nodes"]
            if node["domain"] == "resource"
        ]
        for key, value in parse_expectations(expectation_path):
            if key == "result" and actual_result != value:
                failures.append(f"expected result={value}, got {actual_result}")
            elif key == "finding_count":
                try:
                    expected_count = int(value, 10)
                except ValueError as error:
                    raise ModelError("finding_count must be an integer") from error
                if len(finding_ids) != expected_count:
                    failures.append(
                        f"expected {expected_count} findings, got {len(finding_ids)}"
                    )
            elif key == "forbid" and any(
                fnmatch.fnmatchcase(finding_id, value) for finding_id in finding_ids
            ):
                failures.append(f"forbidden finding pattern matched: {value}")
            elif key == "require" and not any(
                fnmatch.fnmatchcase(finding_id, value) for finding_id in finding_ids
            ):
                failures.append(f"required finding pattern absent: {value}")
            elif key == "require_edge" and edge_kinds[value] == 0:
                failures.append(f"required causal edge absent: {value}")
            elif key == "forbid_call" and any(
                fnmatch.fnmatchcase(name, value) for name in call_names
            ):
                failures.append(f"forbidden call pattern matched: {value}")
            elif key == "require_call" and not any(
                fnmatch.fnmatchcase(name, value) for name in call_names
            ):
                failures.append(f"required call pattern absent: {value}")
            elif key == "require_resource" and not any(
                fnmatch.fnmatchcase(name, value) for name in resource_classes
            ):
                failures.append(f"required resource class absent: {value}")
            elif key == "min_edges":
                edge_kind, separator, count_text = value.rpartition(":")
                if not separator or not edge_kind:
                    raise ModelError("min_edges must be KIND:COUNT")
                try:
                    minimum = int(count_text, 10)
                except ValueError as error:
                    raise ModelError("min_edges count must be an integer") from error
                if minimum < 0:
                    raise ModelError("min_edges count must not be negative")
                if edge_kinds[edge_kind] < minimum:
                    failures.append(
                        f"expected at least {minimum} {edge_kind} edges, "
                        f"got {edge_kinds[edge_kind]}"
                    )
            elif key == "min_nodes":
                try:
                    minimum = int(value, 10)
                except ValueError as error:
                    raise ModelError("min_nodes must be an integer") from error
                if len(model["nodes"]) < minimum:
                    failures.append(
                        f"expected at least {minimum} nodes, got {len(model['nodes'])}"
                    )
    print(
        f"p101 verify: nodes={len(model['nodes'])} edges={len(model['edges'])} "
        f"findings={len(finding_ids)} result={actual_result}"
    )
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return EXIT_FINDINGS if failures else EXIT_CLEAN


def compare(before_dir: Path, after_dir: Path) -> int:
    receipt_result(before_dir)
    receipt_result(after_dir)
    before_model = validate_model(before_dir)
    after_model = validate_model(after_dir)
    _, before_semantic = findings(before_dir)
    _, after_semantic = findings(after_dir)
    before_findings = Counter(before_semantic)
    after_findings = Counter(after_semantic)
    resolved = list((before_findings - after_findings).elements())
    introduced = list((after_findings - before_findings).elements())
    before_nodes, before_edges = semantic_graph(before_model)
    after_nodes, after_edges = semantic_graph(after_model)
    introduced_nodes = list((after_nodes - before_nodes).elements())
    resolved_nodes = list((before_nodes - after_nodes).elements())
    introduced_edges = list((after_edges - before_edges).elements())
    resolved_edges = list((before_edges - after_edges).elements())

    print("# p101 semantic comparison")
    print(f"before_nodes={len(before_model['nodes'])}")
    print(f"after_nodes={len(after_model['nodes'])}")
    print(f"introduced_findings={len(introduced)}")
    print(f"resolved_findings={len(resolved)}")
    print_delta("+ finding", introduced)
    print_delta("- finding", resolved)
    print(f"introduced_nodes={len(introduced_nodes)}")
    print(f"resolved_nodes={len(resolved_nodes)}")
    print_delta("+ node", introduced_nodes)
    print_delta("- node", resolved_nodes)
    print(f"introduced_edges={len(introduced_edges)}")
    print(f"resolved_edges={len(resolved_edges)}")
    print_delta("+ edge", introduced_edges)
    print_delta("- edge", resolved_edges)
    changed = bool(
        introduced
        or resolved
        or introduced_nodes
        or resolved_nodes
        or introduced_edges
        or resolved_edges
    )
    return EXIT_FINDINGS if changed else EXIT_CLEAN


def main() -> int:
    parser = argparse.ArgumentParser(prog="p101")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("analysis_dir")
    verify_parser.add_argument("-e", "--expect")
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("before")
    compare_parser.add_argument("after")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("analysis_dir")
    check_parser.add_argument(
        "--rules", action="append", required=True, dest="rule_packs"
    )
    check_parser.add_argument("--json", action="store_true")
    explain_parser = subparsers.add_parser("explain")
    explain_parser.add_argument("analysis_dir")
    explain_parser.add_argument("finding_id")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            return verify(
                analysis_dir(args.analysis_dir),
                Path(args.expect).expanduser().resolve() if args.expect else None,
            )
        if args.command == "compare":
            return compare(analysis_dir(args.before), analysis_dir(args.after))
        if args.command == "check":
            return check_rules(
                analysis_dir(args.analysis_dir), args.rule_packs, args.json
            )
        return explain(analysis_dir(args.analysis_dir), args.finding_id)
    except ModelError as error:
        print(f"p101 {args.command}: {error}", file=sys.stderr)
        return EXIT_TROUBLE


if __name__ == "__main__":
    raise SystemExit(main())
