#!/usr/bin/env python3
"""Validate or execute the governed p101 post-update check graph."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GRAPH = SCRIPT_DIR / "p101-check-graph.json"


class GraphError(ValueError):
    """The graph is malformed or cannot be selected as requested."""


def require_text(row: dict[str, Any], key: str, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GraphError(f"{context} has no {key}")
    return value


def validate(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema") != "p101-check-graph-v1":
        raise GraphError("unexpected check-graph schema")
    require_text(document, "does_not_prove", "graph")
    if document.get("default_jobs") != 1:
        raise GraphError("the lightweight runner currently requires default_jobs=1")
    nodes = document.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise GraphError("graph has no nodes")

    identifiers: set[str] = set()
    for raw in nodes:
        if not isinstance(raw, dict):
            raise GraphError("graph node is not an object")
        identifier = require_text(raw, "id", "node")
        context = f"node {identifier}"
        if identifier in identifiers:
            raise GraphError(f"duplicate node id: {identifier}")
        identifiers.add(identifier)
        for key in ("title", "group", "guarantee"):
            require_text(raw, key, context)
        if not isinstance(raw.get("required"), bool):
            raise GraphError(f"{context} has no required policy")
        command = raw.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(value, str) or not value for value in command)
        ):
            raise GraphError(f"{context} has an invalid argv command")
        dependencies = raw.get("depends_on")
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str) or not value for value in dependencies
        ):
            raise GraphError(f"{context} has invalid dependencies")
        resources = raw.get("resources")
        if (
            not isinstance(resources, dict)
            or not isinstance(resources.get("writes"), list)
            or not isinstance(resources.get("units"), dict)
        ):
            raise GraphError(f"{context} has invalid resources")

    for node in nodes:
        unknown = set(node["depends_on"]) - identifiers
        if unknown:
            raise GraphError(f"node {node['id']} has unknown dependencies: {sorted(unknown)}")

    order = topological_order(nodes)
    coverage = document.get("coverage")
    if not isinstance(coverage, dict):
        raise GraphError("graph has no coverage contract")
    required_nodes = coverage.get("required_nodes")
    if not isinstance(required_nodes, list) or len(required_nodes) != len(set(required_nodes)):
        raise GraphError("coverage required_nodes is invalid")
    expected = {node["id"] for node in nodes if node["required"]}
    if set(required_nodes) != expected:
        raise GraphError(
            "required-node coverage drift: "
            f"missing={sorted(expected - set(required_nodes))} "
            f"extra={sorted(set(required_nodes) - expected)}"
        )
    return order


def topological_order(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {node["id"]: node for node in nodes}
    remaining = {identifier: set(node["depends_on"]) for identifier, node in by_id.items()}
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [identifier for identifier, dependencies in remaining.items() if not dependencies]
        if not ready:
            raise GraphError(f"dependency cycle among: {sorted(remaining)}")
        for identifier in ready:
            ordered.append(by_id[identifier])
            del remaining[identifier]
            for dependencies in remaining.values():
                dependencies.discard(identifier)
    return ordered


def dependency_closure(
    identifiers: Iterable[str], by_id: dict[str, dict[str, Any]]
) -> set[str]:
    selected = set(identifiers)
    pending = list(selected)
    while pending:
        identifier = pending.pop()
        if identifier not in by_id:
            raise GraphError(f"unknown selected node: {identifier}")
        for dependency in by_id[identifier]["depends_on"]:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return selected


def select_nodes(
    ordered: list[dict[str, Any]],
    only: set[str],
    skipped_groups: set[str],
    start: str | None,
) -> list[dict[str, Any]]:
    by_id = {node["id"]: node for node in ordered}
    selected = dependency_closure(only, by_id) if only else set(by_id)
    selected = {
        identifier
        for identifier in selected
        if by_id[identifier]["group"] not in skipped_groups
    }
    if start is not None:
        if start not in by_id:
            raise GraphError(f"unknown --from node: {start}")
        start_index = next(
            index for index, node in enumerate(ordered) if node["id"] == start
        )
        allowed = {node["id"] for node in ordered[start_index:]}
        selected &= allowed
    return [node for node in ordered if node["id"] in selected]


def expand_command(command: list[str], variables: dict[str, str]) -> list[str]:
    expanded: list[str] = []
    for token in command:
        try:
            value = token.format_map(variables)
        except KeyError as error:
            raise GraphError(f"command references unknown variable: {error.args[0]}") from error
        if value:
            expanded.append(value)
    return expanded


def write_summary(
    output: Path, records: list[dict[str, Any]], document: dict[str, Any]
) -> None:
    summary = output / "summary.md"
    lines = [
        "# p101 governed check graph",
        "",
        f"- Host: {platform.system()} {platform.release()} {platform.machine()}",
        f"- Graph: `{document['schema']}`",
        "",
        "| Status | Check | Guarantee | Artifact |",
        "| --- | --- | --- | --- |",
    ]
    for record in records:
        lines.append(
            f"| {record['outcome'].upper()} | {record['title']} | "
            f"{record['guarantee']} | [log](./logs/{record['id']}.log) |"
        )
    lines.extend(["", "## Limits", "", document["does_not_prove"], ""])
    summary.write_text("\n".join(lines), encoding="utf-8")


def print_log_tail(log_path: Path, line_count: int = 80) -> None:
    """Print a bounded, readable failure receipt to the calling terminal."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        print(f"    unable to read failure log: {error}")
        return

    print("    --- log tail ---")
    for line in lines[-line_count:]:
        print(f"    | {line}")
    print("    --- end log tail ---")


def run_graph(
    document: dict[str, Any],
    selected: list[dict[str, Any]],
    output: Path,
    variables: dict[str, str],
    interactive: bool,
) -> int:
    log_directory = output / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    failed = False

    for node in selected:
        command = expand_command(node["command"], variables)
        log_path = log_directory / f"{node['id']}.log"
        print(f"==> {node['title']}")
        attempts = 0
        while True:
            attempts += 1
            started = time.time_ns()
            with log_path.open("a" if attempts > 1 else "w", encoding="utf-8") as log:
                if attempts > 1:
                    log.write(f"\n# retry {attempts}\n")
                log.write("$ " + " ".join(command) + "\n\n")
                result = subprocess.run(
                    command,
                    cwd=SCRIPT_DIR,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    env=os.environ.copy(),
                )
            finished = time.time_ns()
            if result.returncode == 0:
                outcome = "clean"
                print("    PASS")
                break
            if not interactive:
                outcome = "tool-error"
                print(f"    FAIL (exit {result.returncode}; see {log_path})")
                print_log_tail(log_path)
                failed = True
                break
            print(f"    FAIL (exit {result.returncode}; see {log_path})")
            print_log_tail(log_path)
            prompt = "[r]etry"
            if not node["required"]:
                prompt += ", [s]kip"
            prompt += ", [q]uit: "
            answer = input(prompt).strip().lower()
            if answer in {"", "r", "retry"}:
                continue
            if answer in {"s", "skip"} and not node["required"]:
                outcome = "unsupported"
                break
            outcome = "tool-error"
            failed = True
            break

        records.append(
            {
                "id": node["id"],
                "title": node["title"],
                "outcome": outcome,
                "required": node["required"],
                "return_code": result.returncode,
                "attempts": attempts,
                "started_unix_ns": started,
                "finished_unix_ns": finished,
                "command": command,
                "guarantee": node["guarantee"],
                "log": f"logs/{node['id']}.log",
            }
        )
        write_summary(output, records, document)
        (output / "receipt.json").write_text(
            json.dumps(
                {
                    "schema": "p101-tool-run-receipt-v1",
                    "tool": {"name": "p101-check-graph", "version": "1"},
                    "input": {
                        "schema": document["schema"],
                        "identity": variables.get(
                            "graph_identity", str(DEFAULT_GRAPH.resolve())
                        ),
                    },
                    "outcome": "tool-error" if failed else "clean",
                    "checks": {
                        "attempted": len(records),
                        "completed": sum(
                            record["outcome"] == "clean" for record in records
                        ),
                    },
                    "records": records,
                    "does_not_prove": document["does_not_prove"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if failed:
            return 1
    return 0


def parse_variables(values: list[str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise GraphError(f"--var must be KEY=VALUE: {value}")
        variables[key] = item
    return variables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("check")
    subparsers.add_parser("list")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--var", action="append", default=[])
    run_parser.add_argument("--skip-group", action="append", default=[])
    run_parser.add_argument("--only", action="append", default=[])
    run_parser.add_argument("--from", dest="start")
    run_parser.add_argument("--interactive", action="store_true")
    arguments = parser.parse_args()

    document = json.loads(arguments.graph.read_text(encoding="utf-8"))
    ordered = validate(document)
    if arguments.operation == "check":
        print(f"p101 check graph: {len(ordered)} governed nodes")
        return 0
    if arguments.operation == "list":
        for node in ordered:
            print(f"{node['id']}\t{node['group']}\t{node['title']}")
        return 0

    variables = parse_variables(arguments.var)
    selected = select_nodes(
        ordered, set(arguments.only), set(arguments.skip_group), arguments.start
    )
    if not selected:
        raise GraphError("selection contains no check nodes")
    output = arguments.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    variables.setdefault("out", str(output))
    variables.setdefault("graph_identity", str(arguments.graph.resolve()))
    return run_graph(document, selected, output, variables, arguments.interactive)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GraphError, json.JSONDecodeError, OSError) as error:
        print(f"p101-check-graph: {error}", file=sys.stderr)
        raise SystemExit(2) from error
