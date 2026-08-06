#!/usr/bin/env python3
"""Flatten each functional wrapper library into one implementation file.

The old per-standard directories described where an interface originated.
That information belongs in api-manifest.tsv, not in the compiled layout.
This migration is intentionally deterministic and idempotent.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
DOMAINS = (
    "io",
    "filesystem",
    "memory",
    "process",
    "thread",
    "sync",
    "ipc",
    "network",
    "terminal",
    "time",
    "identity",
    "text",
    "locale",
    "math",
    "search",
    "dynamic_linking",
    "diagnostics",
    "database",
    "cli",
    "random",
    "host",
)
ORIGIN_DIRECTORIES = {"posix", "posix_xsi", "posix_optional", "unix"}
CURRENT_SOURCE = re.compile(
    r"libraries/lib_(?P<domain>"
    + "|".join(re.escape(domain) for domain in DOMAINS)
    + r")/src/(?:posix|posix_xsi|posix_optional|unix)/[^\t\n\" ]+\.c"
)


def merged_source(domain: str, sources: list[Path]) -> str:
    sections: list[str] = []
    for source in sources:
        text = source.read_text(encoding="utf-8").rstrip() + "\n"
        # The former POSIX and common-Unix inet implementations each had a
        # private helper/constants with translation-unit-local names. Give the
        # latter section domain-specific private names before combining the
        # translation units.
        if domain == "network" and source.as_posix().endswith("/src/unix/arpa/inet.c"):
            replacements = {
                "is_inet_addr_none_string": "is_legacy_inet_addr_none_string",
                "P101_INET_ADDR_NONE_VALUE": "P101_INET_LEGACY_ADDR_NONE_VALUE",
                "P101_INET_TWO_BYTE_MAX": "P101_INET_LEGACY_TWO_BYTE_MAX",
                "P101_INET_THREE_BYTE_MAX": "P101_INET_LEGACY_THREE_BYTE_MAX",
                "P101_INET_OCTET_MAX": "P101_INET_LEGACY_OCTET_MAX",
                "P101_INET_ADDR_PARTS": "P101_INET_LEGACY_ADDR_PARTS",
            }
            for old, new in replacements.items():
                text = text.replace(old, new)

        sections.append("\n" + text)

    return normalize_merged_source(domain, "".join(sections).lstrip())


def normalize_merged_source(domain: str, text: str) -> str:
    """Remove translation-unit preamble duplication after source merging."""
    if domain in {"filesystem", "process"}:
        text = text.replace(
            "#ifdef __APPLE__\n    #include <unistd.h>\n#endif",
            "#include <unistd.h>",
            1,
        )
    if domain == "text":
        text = text.replace(
            "#ifdef __APPLE__\n    #include <xlocale.h>\n#endif",
            "#if defined(__APPLE__) || defined(__FreeBSD__)\n"
            "    #include <xlocale.h>\n"
            "#endif",
            1,
        )

    seen: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#include "):
            if stripped in seen:
                continue
            seen.add(stripped)
        lines.append(line)
    normalized = "\n".join(lines) + "\n"
    empty_condition = re.compile(
        r"(?m)^[ \t]*#(?:if|ifdef|ifndef)[^\n]*\n"
        r"(?:[ \t]*\n)*"
        r"[ \t]*#endif(?:[ \t]*//[^\n]*)?\n"
    )
    while empty_condition.search(normalized):
        normalized = empty_condition.sub("", normalized)
    return normalized


def update_config(repo: Path, domain: str) -> None:
    path = repo / "config.cmake"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(set\(p101_{re.escape(domain)}_SOURCES\n).*?(\n\))",
        re.DOTALL,
    )
    replacement = rf"\1        src/{domain}.c\2"
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"could not update source list in {path}")
    path.write_text(updated, encoding="utf-8")


def update_local_manifest(repo: Path, domain: str) -> None:
    path = repo / "api-manifest.tsv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fieldnames = reader.fieldnames
        if fieldnames is None or "current_source" not in fieldnames:
            raise RuntimeError(f"{path}: missing current_source column")
        rows = list(reader)
    current_source = f"libraries/lib_{domain}/src/{domain}.c"
    for row in rows:
        row["current_source"] = current_source
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(output.getvalue(), encoding="utf-8")


def replace_workspace_source_paths() -> None:
    roots = (SCRIPTS_ROOT / "contracts", SCRIPTS_ROOT / "docs")
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".json", ".tsv", ".md"}:
                continue
            text = path.read_text(encoding="utf-8")
            updated = CURRENT_SOURCE.sub(
                lambda match: (
                    f"libraries/lib_{match.group('domain')}/src/"
                    f"{match.group('domain')}.c"
                ),
                text,
            )
            if updated != text:
                path.write_text(updated, encoding="utf-8")


def flatten(domain: str) -> None:
    repo = WORKSPACE / "libraries" / f"lib_{domain}"
    source_root = repo / "src"
    target = source_root / f"{domain}.c"
    nested_sources = sorted(
        path
        for path in source_root.rglob("*.c")
        if path != target
        and len(path.relative_to(source_root).parts) > 1
        and path.relative_to(source_root).parts[0] in ORIGIN_DIRECTORIES
    )
    if nested_sources:
        target.write_text(merged_source(domain, nested_sources), encoding="utf-8")
        for source in nested_sources:
            source.unlink()
        for directory in sorted(source_root.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    elif not target.is_file():
        raise RuntimeError(f"{repo.name}: no implementation sources found")

    target.write_text(
        normalize_merged_source(domain, target.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    update_config(repo, domain)
    update_local_manifest(repo, domain)


def main() -> int:
    for domain in DOMAINS:
        flatten(domain)
    replace_workspace_source_paths()
    print(f"flattened {len(DOMAINS)} functional libraries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
