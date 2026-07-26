#!/usr/bin/env python3
"""Inventory p101 wrapper functions and emit a coarse function graph.

The goal is not to replace clang. It is to produce a curriculum-planning map:
which wrappers exist, which wrappers call other wrappers, which native APIs they
wrap, and which functional clusters are large enough to deserve playgrounds.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


WRAPPER_RE = re.compile(r"\bp101_[A-Za-z0-9_]+\b")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "alignof",
    "_Alignof",
    "_Generic",
    "defined",
}
IGNORED_CALLS = {
    "P101_TRACE",
    "P101_ERROR_RAISE_ERRNO",
    "P101_ERROR_RAISE_SYSTEM",
    "P101_ERROR_RAISE_USER",
    "P101_ERROR_RAISE_MESSAGE",
    "P101_ATTRIBUTE_NEVER_NULL",
    "va_start",
    "va_arg",
    "va_end",
    "va_copy",
}


@dataclass(frozen=True)
class FunctionNode:
    name: str
    library: str
    domain: str
    header: str | None
    source: str | None
    native_guess: str


@dataclass(frozen=True)
class FunctionEdge:
    source: str
    target: str
    kind: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a p101 library wrapper function graph.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Workspace root.")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "docs", help="Output directory.")
    return parser.parse_args()


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def rel(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def active_libraries(root: Path) -> list[Path]:
    return sorted(path for path in (root / "libraries").glob("lib_*") if path.is_dir())


def find_matching(text: str, start: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def extract_definitions(path: Path) -> dict[str, str]:
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    definitions: dict[str, str] = {}
    for match in WRAPPER_RE.finditer(text):
        name = match.group(0)
        after = match.end()
        while after < len(text) and text[after].isspace():
            after += 1
        if after >= len(text) or text[after] != "(":
            continue
        close_paren = find_matching(text, after, "(", ")")
        if close_paren is None:
            continue
        after_sig = close_paren + 1
        while after_sig < len(text) and text[after_sig].isspace():
            after_sig += 1
        if after_sig >= len(text) or text[after_sig] != "{":
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        prefix = text[line_start : match.start()]
        if ";" in prefix or "typedef" in prefix or "#define" in prefix:
            continue
        close_brace = find_matching(text, after_sig, "{", "}")
        if close_brace is None:
            continue
        definitions[name] = text[after_sig + 1 : close_brace]
    return definitions


def extract_header_prototypes(path: Path) -> set[str]:
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    names: set[str] = set()
    for match in WRAPPER_RE.finditer(text):
        name = match.group(0)
        after = match.end()
        while after < len(text) and text[after].isspace():
            after += 1
        if after >= len(text) or text[after] != "(":
            continue
        close_paren = find_matching(text, after, "(", ")")
        if close_paren is None:
            continue
        tail = text[close_paren + 1 : close_paren + 8]
        if ";" in tail:
            names.add(name)
    return names


def native_guess(wrapper: str) -> str:
    rest = wrapper.removeprefix("p101_")
    if rest.startswith("_"):
        return rest
    return rest


def header_topic(path: str | None, source: str | None) -> str:
    candidate = path or source or ""
    candidate = candidate.replace("\\", "/")
    parts = [part for part in candidate.split("/") if part]
    for part in reversed(parts):
        if part.startswith("p101_"):
            return part.removeprefix("p101_").removesuffix(".h")
    if parts:
        return Path(parts[-1]).stem
    return "unknown"


def classify(library: str, header: str | None, source: str | None, name: str) -> str:
    topic = header_topic(header, source)
    topic_lower = topic.lower()
    path = f"{header or ''}/{source or ''}".lower()

    if library == "lib_c":
        if topic in {"stdlib", "string", "stdio", "wchar", "wctype", "ctype", "inttypes"}:
            return f"c/{topic}"
        if topic in {"math", "complex", "fenv"}:
            return "c/math"
        if topic in {"stdatomic"}:
            return "c/atomics"
        if topic in {"setjmp", "signal"}:
            return "c/control-flow"
        return f"c/{topic}"
    if library == "lib_error":
        return "support/error"
    if library == "lib_env":
        if any(token in name for token in ("fd", "alloc", "trace", "call", "fault", "observer", "track")):
            return "support/instrumentation"
        return "support/environment"
    if library == "lib_fsm":
        return "support/fsm"
    if library == "lib_convert":
        if "network" in path:
            return "network/conversion"
        return "c/conversion"
    if library == "lib_c_facts":
        return "tooling/c-facts"
    if library == "lib_util":
        return "support/util"

    if library in {"lib_posix", "lib_posix_optional", "lib_posix_xsi", "lib_unix"}:
        c_extension_topics = {
            "ctype": "c/ctype-extensions",
            "inttypes": "c/inttypes-extensions",
            "locale": "c/locale-extensions",
            "math": "c/math-extensions",
            "setjmp": "c/control-flow-extensions",
            "stdio": "c/stdio-extensions",
            "stdlib": "c/stdlib-extensions",
            "string": "c/string-extensions",
            "strings": "c/string-extensions",
            "time": "c/time-extensions",
            "wchar": "c/wchar-extensions",
            "wctype": "c/wctype-extensions",
        }
        if topic_lower in c_extension_topics:
            return c_extension_topics[topic_lower]
        if topic_lower == "aio":
            return "systems/async-io"
        if topic_lower in {"dirent", "fcntl", "fnmatch", "ftw", "glob", "libgen", "statvfs", "unistd", "wordexp"}:
            return "systems/file-io"
        if topic_lower in {"poll", "select"}:
            return "systems/io-multiplexing"
        if topic_lower in {"ipc", "mqueue", "msg", "sem", "semaphore", "shm"}:
            return "systems/ipc"
        if topic_lower in {"pthread", "sched"}:
            return "systems/threading"
        if topic_lower in {"signal", "spawn", "wait"}:
            return "systems/process-signal"
        if topic_lower in {"mman", "resource", "times", "timex"}:
            return "systems/resource-time-memory"
        if topic_lower in {"grp", "pwd", "termios", "ttyent", "utmpx"}:
            return "systems/users-terminals"
        if topic_lower in {"err", "fmtmsg", "syslog"}:
            return "systems/logging-diagnostics"
        if topic_lower in {"dlfcn", "iconv", "langinfo", "ndbm", "nl_types", "regex", "search"}:
            return "systems/misc-runtime"
        if topic_lower in {"fstab", "mount", "sysctl", "utsname"}:
            return "systems/platform-admin"
        if topic_lower == "getopt":
            return "c/cli-parsing"

    if any(token in path for token in ("/sys/p101_msg", "/sys/msg", "/sys/p101_sem", "/sys/sem", "/sys/p101_shm", "/sys/shm", "/sys/p101_ipc", "/sys/ipc", "p101_mqueue", "p101_semaphore")):
        return "systems/ipc"
    if any(token in path for token in ("p101_poll", "p101_select")):
        return "systems/io-multiplexing"
    if any(token in path for token in ("p101_pthread", "p101_sched")):
        return "systems/threading"
    if any(token in path for token in ("p101_socket", "p101_netdb", "arpa/", "/net/", "p101_ifaddrs", "p101_resolv", "p101_nameser", "p101_ethernet")):
        return "network"
    if any(token in path for token in ("p101_fcntl", "p101_unistd", "p101_stdio", "/sys/p101_stat", "/sys/stat", "p101_dirent", "p101_ftw", "p101_glob", "p101_fnmatch", "p101_wordexp", "p101_libgen", "p101_statvfs", "/sys/p101_uio", "/sys/uio")):
        return "systems/file-io"
    if any(token in path for token in ("p101_wait", "p101_spawn", "p101_signal")):
        return "systems/process-signal"
    if any(token in path for token in ("p101_mman", "p101_resource", "p101_time", "p101_times", "p101_timex")):
        return "systems/resource-time-memory"
    if any(token in path for token in ("p101_pwd", "p101_grp", "p101_utmpx", "p101_ttyent", "p101_termios")):
        return "systems/users-terminals"
    if any(token in path for token in ("p101_syslog", "p101_err", "p101_fmtmsg")):
        return "systems/logging-diagnostics"
    if any(token in path for token in ("p101_dlfcn", "p101_iconv", "p101_locale", "p101_langinfo", "p101_nl_types", "p101_regex", "p101_search", "p101_ndbm")):
        return "systems/misc-runtime"
    return f"{library}/{topic}"


def collect(root: Path) -> tuple[dict[str, FunctionNode], list[FunctionEdge], dict[str, list[str]]]:
    headers_by_name: dict[str, Path] = {}
    sources_by_name: dict[str, Path] = {}
    definitions_by_name: dict[str, str] = {}
    declared_by_library: dict[str, list[str]] = defaultdict(list)

    for library_dir in active_libraries(root):
        library = library_dir.name
        for header in sorted((library_dir / "include").glob("**/*.h")):
            for name in extract_header_prototypes(header):
                headers_by_name.setdefault(name, header)
                declared_by_library[library].append(name)
        for source in sorted((library_dir / "src").glob("**/*.c")):
            definitions = extract_definitions(source)
            for name, body in definitions.items():
                sources_by_name.setdefault(name, source)
                definitions_by_name.setdefault(name, body)

    all_names = sorted(set(headers_by_name) | set(sources_by_name))
    nodes: dict[str, FunctionNode] = {}
    for name in all_names:
        header = headers_by_name.get(name)
        source = sources_by_name.get(name)
        owner_path = header or source
        library = "unknown"
        if owner_path is not None:
            try:
                library = owner_path.relative_to(root / "libraries").parts[0]
            except ValueError:
                library = "unknown"
        header_rel = rel(root, header)
        source_rel = rel(root, source)
        nodes[name] = FunctionNode(
            name=name,
            library=library,
            domain=classify(library, header_rel, source_rel, name),
            header=header_rel,
            source=source_rel,
            native_guess=native_guess(name),
        )

    edges_set: set[tuple[str, str, str]] = set()
    for name, body in definitions_by_name.items():
        node = nodes.get(name)
        if node is None:
            continue
        for call in CALL_RE.findall(body):
            if call in KEYWORDS or call in IGNORED_CALLS:
                continue
            if call.startswith("P101_"):
                continue
            if call.startswith("p101_") and call != name:
                edges_set.add((name, call, "wrapper-call"))
            elif not call.startswith("p101_"):
                guess = node.native_guess
                kind = "native-call"
                target = call
                if call == guess or call.strip("_") == guess.strip("_"):
                    kind = "wrapped-native"
                edges_set.add((name, target, kind))

    edges = [FunctionEdge(source, target, kind) for source, target, kind in sorted(edges_set)]
    return nodes, edges, declared_by_library


def domain_summaries(nodes: dict[str, FunctionNode]) -> dict[str, dict[str, Any]]:
    domains: dict[str, list[FunctionNode]] = defaultdict(list)
    for node in nodes.values():
        domains[node.domain].append(node)
    return {
        domain: {
            "count": len(items),
            "libraries": dict(sorted(Counter(item.library for item in items).items())),
            "functions": sorted(item.name for item in items),
        }
        for domain, items in sorted(domains.items())
    }


def playground_recommendations(domain_counts: Counter[str]) -> list[dict[str, Any]]:
    plans = [
        (
            "p101-c-playground",
            "C language, memory, strings, integers, parsing, atomics, and portable diagnostics.",
            ("c/",),
        ),
        (
            "p101-systems-playground",
            "POSIX files, processes, signals, resources, terminals, pthreads, IPC, and I/O multiplexing.",
            ("systems/",),
        ),
        (
            "p101-network-playground",
            "Sockets, address resolution, interfaces, resolver/name helpers, and byte-order/network conversions.",
            ("network",),
        ),
        (
            "p101-tooling-playground",
            "p101 support libraries: env/error/fsm/facts/instrumentation and how the tools observe programs.",
            ("support/", "tooling/"),
        ),
    ]
    recommendations = []
    for name, purpose, prefixes in plans:
        domains = [domain for domain in sorted(domain_counts) if any(domain.startswith(prefix) for prefix in prefixes)]
        recommendations.append(
            {
                "playground": name,
                "purpose": purpose,
                "function_count": sum(domain_counts[domain] for domain in domains),
                "domains": domains,
            }
        )
    return recommendations


def specialized_recommendations(domain_counts: Counter[str]) -> list[dict[str, Any]]:
    candidates = [
        (
            "p101-file-io-playground",
            "File descriptors, streams, directories, paths, short reads/writes, descriptor ownership, and exec inheritance.",
            ("systems/file-io", "systems/async-io"),
        ),
        (
            "p101-threading-playground",
            "Threads, mutexes, condition variables, cancellation, cleanup, atomics, and race-oriented resource handling.",
            ("systems/threading", "c/atomics"),
        ),
        (
            "p101-ipc-playground",
            "POSIX and XSI IPC: message queues, semaphores, shared memory, keys, cleanup, and permission mistakes.",
            ("systems/ipc", "systems/io-multiplexing"),
        ),
        (
            "p101-process-playground",
            "fork, exec, wait, spawn, signal handling, CLOEXEC, inherited resources, and failure-path cleanup.",
            ("systems/process-signal", "systems/file-io"),
        ),
        (
            "p101-network-playground",
            "TCP/UDP sockets, address resolution, interfaces, resolver helpers, protocol databases, and byte ordering.",
            ("network",),
        ),
        (
            "p101-observability-playground",
            "env/error/fsm/facts, call traces, resource logs, fault injection, and writing small analyses over event streams.",
            ("support/environment", "support/error", "support/fsm", "support/instrumentation", "tooling/c-facts"),
        ),
    ]

    recommendations: list[dict[str, Any]] = []
    for name, purpose, domains in candidates:
        present = [domain for domain in domains if domain_counts[domain] > 0]
        recommendations.append(
            {
                "playground": name,
                "purpose": purpose,
                "function_count": sum(domain_counts[domain] for domain in present),
                "domains": present,
            }
        )
    return sorted(recommendations, key=lambda item: (-item["function_count"], item["playground"]))


def write_json(
    path: Path,
    nodes: dict[str, FunctionNode],
    edges: list[FunctionEdge],
    domains: dict[str, dict[str, Any]],
    recommendations: list[dict[str, Any]],
    specialized: list[dict[str, Any]],
) -> None:
    data = {
        "summary": {
            "function_count": len(nodes),
            "edge_count": len(edges),
            "domain_count": len(domains),
        },
        "nodes": [asdict(node) for node in sorted(nodes.values(), key=lambda item: item.name)],
        "edges": [asdict(edge) for edge in edges],
        "domains": domains,
        "playground_recommendations": recommendations,
        "specialized_playground_candidates": specialized,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_dot(path: Path, nodes: dict[str, FunctionNode], edges: list[FunctionEdge]) -> None:
    lines = ["digraph p101_lib_functions {", "  rankdir=LR;", "  node [shape=box, fontsize=10];"]
    for node in sorted(nodes.values(), key=lambda item: item.name):
        lines.append(f'  "{node.name}" [label="{node.name}\\n{node.domain}"];')
    for edge in edges:
        if edge.kind == "native-call":
            continue
        style = "solid" if edge.kind == "wrapper-call" else "dashed"
        lines.append(f'  "{edge.source}" -> "{edge.target}" [label="{edge.kind}", style={style}];')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def domain_mermaid(recommendations: list[dict[str, Any]]) -> str:
    lines = ["```mermaid", "flowchart LR"]
    for rec in recommendations:
        pg = rec["playground"].replace("-", "_")
        lines.append(f'  {pg}["{rec["playground"]}\\n{rec["function_count"]} wrappers"]')
        for domain in rec["domains"]:
            did = re.sub(r"[^A-Za-z0-9_]", "_", domain)
            lines.append(f'  {did}["{domain}"] --> {pg}')
    lines.append("```")
    return "\n".join(lines)


def write_markdown(
    path: Path,
    nodes: dict[str, FunctionNode],
    edges: list[FunctionEdge],
    domains: dict[str, dict[str, Any]],
    recommendations: list[dict[str, Any]],
    specialized: list[dict[str, Any]],
) -> None:
    lib_counts = Counter(node.library for node in nodes.values())
    domain_counts = Counter(node.domain for node in nodes.values())
    wrapper_edges = sum(1 for edge in edges if edge.kind == "wrapper-call")
    wrapped_native_edges = sum(1 for edge in edges if edge.kind == "wrapped-native")

    lines: list[str] = [
        "# p101 library function graph",
        "",
        "Generated from active `libraries/lib_*` directories. `_to_delete` is excluded.",
        "",
        "## Summary",
        "",
        f"- Wrapper/function nodes: `{len(nodes)}`",
        f"- Edges: `{len(edges)}`",
        f"- Wrapper-to-wrapper edges: `{wrapper_edges}`",
        f"- Wrapper-to-native wrapped-call edges: `{wrapped_native_edges}`",
        f"- Domains: `{len(domains)}`",
        "",
        "## Playground-level graph",
        "",
        domain_mermaid(recommendations),
        "",
        "## Recommended playground cuts",
        "",
        "| Playground | Function count | Domains | Purpose |",
        "| --- | ---: | --- | --- |",
    ]
    for rec in recommendations:
        lines.append(f"| `{rec['playground']}` | {rec['function_count']} | {', '.join(f'`{domain}`' for domain in rec['domains'])} | {rec['purpose']} |")

    lines.extend(["", "## Candidate specialized playgrounds", "", "| Candidate | Function count | Domains | Why it exists |", "| --- | ---: | --- | --- |"])
    for rec in specialized:
        lines.append(f"| `{rec['playground']}` | {rec['function_count']} | {', '.join(f'`{domain}`' for domain in rec['domains'])} | {rec['purpose']} |")

    lines.extend(
        [
            "",
            "## Curriculum reading",
            "",
            "- The existing wrapper examples can collapse into playground tracks once each cluster has a working \"good path\" plus focused defect labs.",
            "- `systems/ipc` is big enough to justify an IPC unit, especially when paired with `systems/io-multiplexing` so students see blocking, readiness, cleanup, and ownership together.",
            "- `systems/file-io`, `systems/threading`, and `network` are the three largest non-C clusters; they should not be squeezed into one general systems lab.",
            "- `support/instrumentation` plus `tooling/c-facts` should become a meta/tooling playground: students learn that the wrappers are observable APIs, not just safer spelling.",
        ]
    )

    lines.extend(["", "## Counts by library", "", "| Library | Functions |", "| --- | ---: |"])
    for library, count in sorted(lib_counts.items()):
        lines.append(f"| `{library}` | {count} |")

    lines.extend(["", "## Domain clusters", "", "| Domain | Functions | Libraries | Playground signal |", "| --- | ---: | --- | --- |"])
    for domain, count in sorted(domain_counts.items()):
        libraries = ", ".join(f"`{name}`:{value}" for name, value in sorted(domains[domain]["libraries"].items()))
        signal = "candidate"
        if domain == "systems/ipc":
            signal = "strong IPC playground cluster"
        elif domain == "systems/io-multiplexing":
            signal = "systems reference/advanced cluster"
        elif domain == "network":
            signal = "network playground cluster"
        elif domain.startswith("c/"):
            signal = "C playground"
        elif domain.startswith("systems/"):
            signal = "systems playground"
        elif domain.startswith("support/") or domain.startswith("tooling/"):
            signal = "tooling/meta playground"
        lines.append(f"| `{domain}` | {count} | {libraries} | {signal} |")

    lines.extend(["", "## Large clusters and representative functions", ""])
    for domain, count in sorted(domain_counts.items(), key=lambda item: (-item[1], item[0])):
        funcs = domains[domain]["functions"]
        sample = ", ".join(f"`{name}`" for name in funcs[:35])
        more = "" if len(funcs) <= 35 else f" … +{len(funcs) - 35} more"
        lines.extend([f"### `{domain}` ({count})", "", sample + more, ""])

    lines.extend(["## Files", "", "- JSON: `lib-function-graph.json`", "- DOT: `lib-function-graph.dot`", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes, edges, _declared = collect(root)
    domains = domain_summaries(nodes)
    recommendations = playground_recommendations(Counter(node.domain for node in nodes.values()))
    specialized = specialized_recommendations(Counter(node.domain for node in nodes.values()))
    write_json(out_dir / "lib-function-graph.json", nodes, edges, domains, recommendations, specialized)
    write_dot(out_dir / "lib-function-graph.dot", nodes, edges)
    write_markdown(out_dir / "lib-function-graph.md", nodes, edges, domains, recommendations, specialized)
    print(f"wrote {out_dir / 'lib-function-graph.md'}")
    print(f"wrote {out_dir / 'lib-function-graph.json'}")
    print(f"wrote {out_dir / 'lib-function-graph.dot'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
