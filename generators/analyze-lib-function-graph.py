#!/usr/bin/env python3
"""Build the curriculum graph from semantic C facts and reviewed policy.

The AST supplies declarations, identities, and call edges.  Explicit contracts
assign public wrapper identities to curriculum domains and domains to tracks.
No wrapper purpose is inferred from a function name, variable name, or path.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

import c_facts  # noqa: E402


DOMAIN_CONTRACT = SCRIPTS_ROOT / "contracts" / "p101-curriculum-domains.tsv"
TRACK_CONTRACT = SCRIPTS_ROOT / "contracts" / "p101-playground-tracks.json"
PARITY_CONTRACT = SCRIPTS_ROOT / "contracts" / "native-wrapper-parity.tsv"


@dataclass(frozen=True)
class FunctionNode:
    name: str
    library: str
    domain: str
    header: str
    source: str
    native_guess: str


@dataclass(frozen=True)
class FunctionEdge:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class ApiRow:
    name: str
    usr: str
    library: str
    source: str
    header: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the p101 curriculum graph from semantic C facts."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Workspace root.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SCRIPTS_ROOT / "docs",
        help="Output directory.",
    )
    return parser.parse_args()


def active_libraries(root: Path) -> list[Path]:
    repositories = root / "scripts" / "repos.txt"
    admitted: list[Path] = []
    for raw_line in repositories.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 2:
            continue
        path = (repositories.parent / fields[1]).resolve()
        if path.parent == (root / "libraries").resolve() and path.is_dir():
            admitted.append(path)
    return sorted(admitted)


def workspace_path(root: Path, value: str, context: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{context} escapes the workspace: {value}") from error
    if not path.is_file():
        raise ValueError(f"{context} is absent: {value}")
    return path


def load_api(root: Path) -> dict[str, ApiRow]:
    rows: dict[str, ApiRow] = {}
    for library in active_libraries(root):
        manifest = library / "api-manifest.tsv"
        if not manifest.is_file():
            continue
        with manifest.open(encoding="utf-8", newline="") as stream:
            for record in csv.DictReader(stream, delimiter="\t"):
                name = record["function"]
                usr = record["function_usr"]
                if not usr:
                    raise ValueError(f"{library.name}:{name} has no function_usr")
                if usr in rows:
                    raise ValueError(f"duplicate public API identity: {usr}")
                source = record["current_source"]
                header = record.get("current_header", "")
                workspace_path(root, source, f"{library.name}:{name} source")
                if header:
                    workspace_path(root, header, f"{library.name}:{name} header")
                rows[usr] = ApiRow(name, usr, library.name, source, header)
    if not rows:
        raise ValueError("no active public API manifests were found")
    return rows


def load_domains() -> dict[str, tuple[str, str]]:
    domains: dict[str, tuple[str, str]] = {}
    with DOMAIN_CONTRACT.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            usr = row["wrapper_usr"]
            if usr in domains:
                raise ValueError(f"duplicate curriculum identity: {usr}")
            domains[usr] = (row["function"], row["domain"])
    return domains


def load_native_pairs() -> tuple[dict[str, str], set[tuple[str, str]]]:
    display: dict[str, str] = {}
    pairs: set[tuple[str, str]] = set()
    with PARITY_CONTRACT.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            wrapper_usr = row["wrapper_usr"]
            native_usr = row["native_usr"]
            pairs.add((wrapper_usr, native_usr))
            display.setdefault(wrapper_usr, row["native"])
    return display, pairs


def load_track_contract() -> dict[str, Any]:
    contract = json.loads(TRACK_CONTRACT.read_text(encoding="utf-8"))
    if contract.get("schema") != "p101-playground-track-contract-v1":
        raise ValueError("unexpected playground track contract schema")
    tracks = contract.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("playground track contract has no tracks")
    return contract


def collect(root: Path) -> tuple[dict[str, FunctionNode], list[FunctionEdge]]:
    api = load_api(root)
    domains = load_domains()
    if set(api) != set(domains):
        missing = sorted(set(api) - set(domains))
        stale = sorted(set(domains) - set(api))
        raise ValueError(
            "curriculum identity coverage mismatch: "
            f"missing={missing[:5]} stale={stale[:5]}"
        )
    for usr, row in api.items():
        if domains[usr][0] != row.name:
            raise ValueError(
                f"curriculum display name does not match API identity {usr}"
            )

    native_display, native_pairs = load_native_pairs()
    paths = sorted(
        {
            workspace_path(root, value, "public API input")
            for row in api.values()
            for value in (row.source, row.header)
            if value
        }
    )
    facts = c_facts.acquire(root, paths)
    declarations = {
        str(fact["usr"])
        for fact in facts
        if fact["kind"] == "FUNCTION" and str(fact.get("usr", ""))
    }
    missing_declarations = sorted(set(api) - declarations)
    if missing_declarations:
        raise ValueError(
            "public API identities absent from semantic facts: "
            + ", ".join(missing_declarations[:10])
        )

    nodes = {
        row.name: FunctionNode(
            name=row.name,
            library=row.library,
            domain=domains[usr][1],
            header=row.header,
            source=row.source,
            native_guess=native_display.get(usr, ""),
        )
        for usr, row in api.items()
    }

    edges: set[tuple[str, str, str]] = set()
    for fact in facts:
        if fact["kind"] != "CALL":
            continue
        caller_usr = str(fact.get("caller_usr", ""))
        callee_usr = str(fact.get("usr", ""))
        caller = api.get(caller_usr)
        if caller is None or not callee_usr:
            continue
        callee = api.get(callee_usr)
        if callee is not None:
            if callee_usr != caller_usr:
                edges.add((caller.name, callee.name, "wrapper-call"))
            continue
        kind = (
            "wrapped-native"
            if (caller_usr, callee_usr) in native_pairs
            else "native-call"
        )
        edges.add((caller.name, str(fact.get("value", callee_usr)), kind))

    return nodes, [
        FunctionEdge(source, target, kind)
        for source, target, kind in sorted(edges)
    ]


def domain_summaries(
    nodes: dict[str, FunctionNode],
) -> dict[str, dict[str, Any]]:
    domains: dict[str, list[FunctionNode]] = defaultdict(list)
    for node in nodes.values():
        domains[node.domain].append(node)
    return {
        domain: {
            "count": len(items),
            "libraries": dict(
                sorted(Counter(item.library for item in items).items())
            ),
            "functions": sorted(item.name for item in items),
        }
        for domain, items in sorted(domains.items())
    }


def repository_plan(
    domain_counts: Counter[str], contract: dict[str, Any]
) -> dict[str, Any]:
    assigned: set[str] = set()
    tracks: list[dict[str, Any]] = []
    for raw in contract["tracks"]:
        domains = list(raw["domains"])
        duplicates = assigned.intersection(domains)
        if duplicates:
            raise ValueError(
                f"curriculum domains assigned twice: {sorted(duplicates)}"
            )
        assigned.update(domains)
        tracks.append(
            {
                "track": raw["track"],
                "purpose": raw["purpose"],
                "function_count": sum(domain_counts[domain] for domain in domains),
                "domains": domains,
            }
        )
    uncovered = sorted(set(domain_counts) - assigned)
    stale = sorted(assigned - set(domain_counts))
    if uncovered or stale:
        raise ValueError(
            f"track domain coverage mismatch: uncovered={uncovered} stale={stale}"
        )
    return {
        "repository": contract["repository"],
        "layout": contract["layout"],
        "track_count": len(tracks),
        "covered_function_count": sum(domain_counts.values()),
        "uncovered_function_count": 0,
        "uncovered_domains": [],
        "tracks": tracks,
    }


def write_json(
    path: Path,
    nodes: dict[str, FunctionNode],
    edges: list[FunctionEdge],
    domains: dict[str, dict[str, Any]],
    plan: dict[str, Any],
) -> None:
    document = {
        "schema": "p101-library-function-graph-v2",
        "evidence": {
            "structure": "P101FACT v6 resolved declarations and calls",
            "domain_policy": str(DOMAIN_CONTRACT.relative_to(SCRIPTS_ROOT)),
            "track_policy": str(TRACK_CONTRACT.relative_to(SCRIPTS_ROOT)),
            "blind_spot": "Indirect calls without a resolved declaration identity are omitted.",
        },
        "summary": {
            "function_count": len(nodes),
            "edge_count": len(edges),
            "domain_count": len(domains),
        },
        "nodes": [
            asdict(node)
            for node in sorted(nodes.values(), key=lambda item: item.name)
        ],
        "edges": [asdict(edge) for edge in edges],
        "domains": domains,
        "repository_recommendation": plan,
        "track_recommendations": plan["tracks"],
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def write_dot(
    path: Path, nodes: dict[str, FunctionNode], edges: list[FunctionEdge]
) -> None:
    lines = [
        "digraph p101_lib_functions {",
        "  rankdir=LR;",
        "  node [shape=box, fontsize=10];",
    ]
    for node in sorted(nodes.values(), key=lambda item: item.name):
        lines.append(
            f'  "{node.name}" [label="{node.name}\\n{node.domain}"];'
        )
    for edge in edges:
        if edge.kind == "native-call":
            continue
        style = "solid" if edge.kind == "wrapper-call" else "dashed"
        lines.append(
            f'  "{edge.source}" -> "{edge.target}" '
            f'[label="{edge.kind}", style={style}];'
        )
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_markdown(
    path: Path,
    nodes: dict[str, FunctionNode],
    edges: list[FunctionEdge],
    domains: dict[str, dict[str, Any]],
    plan: dict[str, Any],
) -> None:
    lines = [
        "# p101 library function graph",
        "",
        "Generated from resolved `P101FACT v6` declarations and calls. "
        "Curriculum domains and tracks come from reviewed contracts; they are "
        "not inferred from identifier or path spelling.",
        "",
        "## Summary",
        "",
        f"- Public API nodes: `{len(nodes)}`",
        f"- Resolved call edges: `{len(edges)}`",
        f"- Curriculum domains: `{len(domains)}`",
        f"- Playground tracks: `{plan['track_count']}`",
        "",
        "## Tracks",
        "",
        "| Track | Wrappers | Domains | Purpose |",
        "| --- | ---: | --- | --- |",
    ]
    for track in plan["tracks"]:
        domain_text = ", ".join(f"`{domain}`" for domain in track["domains"])
        lines.append(
            f"| `{track['track']}` | {track['function_count']} | "
            f"{domain_text} | {track['purpose']} |"
        )
    lines.extend(
        [
            "",
            "## Domains",
            "",
            "The function names below are display output. Policy joins use "
            "resolved declaration identities.",
            "",
        ]
    )
    for domain, summary in domains.items():
        functions = summary["functions"]
        sample = ", ".join(f"`{name}`" for name in functions[:35])
        suffix = "" if len(functions) <= 35 else f" … +{len(functions) - 35} more"
        lines.extend(
            [f"### `{domain}` ({summary['count']})", "", sample + suffix, ""]
        )
    lines.extend(
        [
            "## Evidence and limits",
            "",
            "- Structure: resolved Clang AST declarations and call identities.",
            "- Policy: `contracts/p101-curriculum-domains.tsv` and "
            "`contracts/p101-playground-tracks.json`.",
            "- Blind spot: unresolved indirect calls are omitted.",
            "",
            "## Files",
            "",
            "- JSON: `lib-function-graph.json`",
            "- DOT: `lib-function-graph.dot`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    arguments = parse_args()
    root = arguments.root.resolve()
    output = arguments.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    nodes, edges = collect(root)
    domains = domain_summaries(nodes)
    counts = Counter(node.domain for node in nodes.values())
    plan = repository_plan(counts, load_track_contract())
    write_json(output / "lib-function-graph.json", nodes, edges, domains, plan)
    write_dot(output / "lib-function-graph.dot", nodes, edges)
    write_markdown(output / "lib-function-graph.md", nodes, edges, domains, plan)
    print(f"wrote {output / 'lib-function-graph.md'}")
    print(f"wrote {output / 'lib-function-graph.json'}")
    print(f"wrote {output / 'lib-function-graph.dot'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
