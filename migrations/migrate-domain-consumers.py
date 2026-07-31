#!/usr/bin/env python3
"""Move p101 consumers from standards-based libraries to functional domains."""

from __future__ import annotations

import csv
import re
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
OLD_NAMES = ("p101_posix_optional", "p101_posix_xsi", "p101_posix", "p101_unix")
SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp"}
SKIP_PARTS = {
    "build",
    "build-clang",
    "build-clang++",
    "build-gcc",
    "build-g++",
    ".git",
}
SKIP_ROOTS = {
    *(WORKSPACE / "libraries" / name for name in (
        "lib_posix",
        "lib_posix_optional",
        "lib_posix_xsi",
        "lib_unix",
    )),
    *(WORKSPACE / "examples" / name for name in (
        "lib_posix_examples",
        "lib_posix_optional_examples",
        "lib_posix_xsi_examples",
        "lib_unix_examples",
    )),
    *(WORKSPACE / "libraries" / f"lib_{name}" for name in (
        "io", "filesystem", "memory", "process", "thread", "sync", "ipc",
        "network", "terminal", "time", "identity", "text", "locale", "math",
        "search", "dynamic_linking", "diagnostics", "database", "cli",
        "random", "host",
    )),
}


def skipped(path: Path) -> bool:
    if any(root == path or root in path.parents for root in SKIP_ROOTS):
        return True
    return any(part in SKIP_PARTS or part.startswith("build-") for part in path.parts)


def load_map() -> tuple[dict[str, str], dict[str, set[str]]]:
    functions: dict[str, str] = {}
    headers: dict[str, set[str]] = {}
    with (WORKSPACE / "scripts" / "wrapper-library-map.tsv").open() as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            functions[row["function"]] = row["domain"]
            marker = "/include/"
            include = row["original_header"].split(marker, 1)[1]
            headers.setdefault(include, set()).add(row["domain"])
    return functions, headers


FUNCTION_DOMAIN, HEADER_DOMAINS = load_map()
P101_NAME = re.compile(r"\bp101_[A-Za-z_]\w*\b")
OLD_INCLUDE = re.compile(
    r'^[ \t]*#include\s+[<"]'
    r'(p101_(?:posix_optional|posix_xsi|posix|unix)/[^>"]+)'
    r'[>"][ \t]*\n?',
    re.MULTILINE,
)
DOMAIN_INCLUDE = re.compile(
    r'^[ \t]*#include\s+<p101_'
    r'(io|filesystem|memory|process|thread|sync|ipc|network|terminal|time|'
    r'identity|text|locale|math|search|dynamic_linking|diagnostics|database|'
    r'cli|random|host)/[^>]+>[ \t]*\n?',
    re.MULTILINE,
)


def domains_in_text(text: str) -> set[str]:
    return {
        FUNCTION_DOMAIN[name]
        for name in P101_NAME.findall(text)
        if name in FUNCTION_DOMAIN
    }


def migrate_source(path: Path) -> None:
    text = path.read_text()
    matches = list(OLD_INCLUDE.finditer(text))
    domain_matches = list(DOMAIN_INCLUDE.finditer(text))
    if not matches and not domain_matches:
        return
    domains = domains_in_text(text)
    if not domains:
        for match in matches:
            domains.update(HEADER_DOMAINS.get(match.group(1), set()))
    replacement = "".join(
        f"#include <p101_{domain}/{domain}.h>\n" for domain in sorted(domains)
    )
    all_matches = sorted(matches + domain_matches, key=lambda item: item.start())
    first = all_matches[0]
    text = OLD_INCLUDE.sub("", text)
    text = DOMAIN_INCLUDE.sub("", text)
    text = text[: first.start()] + replacement + text[first.start() :]
    path.write_text(text)


def source_domains(root: Path) -> set[str]:
    domains: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in SOURCE_SUFFIXES and not skipped(path):
            domains.update(domains_in_text(path.read_text()))
    return domains


def replace_build_references(path: Path) -> None:
    text = path.read_text()
    if not any(name in text for name in OLD_NAMES):
        return
    root = path.parent
    if path.name == "config.cmake":
        root = path.parent
    domains = sorted(source_domains(root))
    targets = [f"p101_{domain}" for domain in domains]

    lines = text.splitlines(keepends=True)
    output: list[str] = []
    inserted = False
    for line in lines:
        stripped = line.strip()
        if stripped in OLD_NAMES:
            if not inserted:
                indent = line[: len(line) - len(line.lstrip())]
                output.extend(f"{indent}{target}\n" for target in targets)
                inserted = True
            continue
        if any(re.search(rf"\b{re.escape(name)}\b", line) for name in OLD_NAMES):
            replacement = " ".join(targets)
            for name in OLD_NAMES:
                line = re.sub(rf"\b{re.escape(name)}\b", replacement, line)
            inserted = True
        output.append(line)
    path.write_text("".join(output))


def repository_root(path: Path) -> Path | None:
    for parent in (path.parent, *path.parents):
        if (parent / ".git").exists():
            return parent
    return None


def normalize_test_libraries(path: Path) -> None:
    text = path.read_text()
    if "set(P101_LIBS" not in text:
        return
    root = repository_root(path)
    if root is None:
        return
    config = root / "config.cmake"
    if not config.is_file():
        return
    config_text = config.read_text()
    link_blocks = re.findall(
        r"set\(\s*[A-Za-z0-9_]+_LINK_LIBRARIES\b(.*?)\)",
        config_text,
        re.DOTALL,
    )
    libraries = []
    for block in link_blocks:
        libraries.extend(re.findall(r"\bp101_[A-Za-z0-9_]+\b", block))
    libraries = list(dict.fromkeys(libraries))
    if not libraries:
        return
    text = re.sub(
        r"set\(P101_LIBS\b.*?\)",
        f"set(P101_LIBS {' '.join(libraries)})",
        text,
        count=1,
        flags=re.DOTALL,
    )
    path.write_text(text)


def main() -> None:
    source_count = 0
    build_count = 0
    for path in WORKSPACE.rglob("*"):
        if not path.is_file() or skipped(path):
            continue
        if path.suffix in SOURCE_SUFFIXES:
            before = path.read_text()
            migrate_source(path)
            source_count += before != path.read_text()
        elif path.name in {"config.cmake", "CMakeLists.txt"}:
            before = path.read_text()
            replace_build_references(path)
            if path.name == "CMakeLists.txt":
                normalize_test_libraries(path)
            build_count += before != path.read_text()
    print(f"migrated {source_count} source/header files and {build_count} build files")


if __name__ == "__main__":
    main()
