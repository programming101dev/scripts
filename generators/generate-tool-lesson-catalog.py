#!/usr/bin/env python3
"""Generate lib_tool_support's typed lesson lookup from the playground catalog."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "p101-finding-lesson-catalog-v2"
FINDING_ID = re.compile(r"^P101-[A-Z][A-Z0-9_-]*-[0-9]{3}$")


class CatalogError(ValueError):
    """The catalog cannot safely generate the C lookup."""


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def enum_name(finding_id: str) -> str:
    suffix = finding_id.removeprefix("P101-").replace("-", "_")
    return f"P101_TOOL_FINDING_{suffix}"


def c_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def require_string(document: dict[str, Any], field: str, source: Path) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{source}: {field} must be a non-empty string")
    return value


def load_entries(source: Path) -> list[tuple[str, str, str, str]]:
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"{source}: cannot read lesson catalog: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise CatalogError(f"{source}: expected schema {SCHEMA}")
    url_base = require_string(document, "url_base", source)
    if not url_base.endswith("/"):
        raise CatalogError(f"{source}: url_base must end in /")
    lessons = document.get("lessons")
    if not isinstance(lessons, list):
        raise CatalogError(f"{source}: lessons must be an array")

    entries: list[tuple[str, str, str, str]] = []
    seen_ids: set[str] = set()
    seen_enums: set[str] = set()
    for index, raw_lesson in enumerate(lessons):
        if not isinstance(raw_lesson, dict):
            raise CatalogError(f"{source}: lesson {index} must be an object")
        lesson_id = require_string(raw_lesson, "lesson_id", source)
        lesson_path = require_string(raw_lesson, "path", source)
        relative_path = PurePosixPath("lessons") / PurePosixPath(lesson_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise CatalogError(f"{source}: lesson path escapes playground: {lesson_path}")
        local_path = source.parent.parent / relative_path
        if not local_path.is_file():
            raise CatalogError(f"{source}: lesson file does not exist: {local_path}")
        finding_ids = raw_lesson.get("finding_ids")
        if not isinstance(finding_ids, list):
            raise CatalogError(f"{source}: {lesson_id} finding_ids must be an array")
        for finding_id in finding_ids:
            if not isinstance(finding_id, str) or FINDING_ID.fullmatch(finding_id) is None:
                raise CatalogError(f"{source}: invalid finding ID in {lesson_id}: {finding_id!r}")
            generated_name = enum_name(finding_id)
            if finding_id in seen_ids:
                raise CatalogError(f"{source}: duplicate lesson route for {finding_id}")
            if generated_name in seen_enums:
                raise CatalogError(f"{source}: generated enum collision for {finding_id}")
            seen_ids.add(finding_id)
            seen_enums.add(generated_name)
            entries.append((finding_id, lesson_id, relative_path.as_posix(), url_base + relative_path.as_posix()))
    if not entries:
        raise CatalogError(f"{source}: no finding-to-lesson routes")
    return entries


def header(entries: list[tuple[str, str, str, str]], source_label: str) -> str:
    enum_lines = [f"        {enum_name(entry[0])} = {index}," for index, entry in enumerate(entries)]
    enum_lines.append(f"        P101_TOOL_FINDING_COUNT = {len(entries)}")
    return "\n".join(
        [
            "#ifndef P101_TOOL_SUPPORT_LESSON_CATALOG_H",
            "#define P101_TOOL_SUPPORT_LESSON_CATALOG_H",
            "",
            f"/* Generated from {source_label}; do not edit. */",
            "",
            "#ifdef __cplusplus",
            'extern \"C\"',
            "{",
            "#endif",
            "",
            "    // clang-format off",
            "    typedef enum",
            "    {",
            *enum_lines,
            "    } p101_tool_finding;",
            "",
            "    struct p101_tool_rule_definition",
            "    {",
            "        const char *id;",
            "        const char *lesson_id;",
            "        const char *lesson_path;",
            "        const char *lesson_url;",
            "    };",
            "",
            "    // clang-format on",
            "",
            "    const struct p101_tool_rule_definition *p101_tool_rule_definition_lookup(p101_tool_finding finding);",
            "    const struct p101_tool_rule_definition *p101_tool_rule_definition_lookup_id(const char *diagnostic_id);",
            "",
            "#ifdef __cplusplus",
            "}",
            "#endif",
            "",
            "#endif",
            "",
        ]
    )


def source(entries: list[tuple[str, str, str, str]], source_label: str) -> str:
    rows = [
        "        {" + ", ".join(c_string(value) for value in entry) + "},"
        for entry in entries
    ]
    rows[-1] = rows[-1].removesuffix(",")
    return "\n".join(
        [
            "#include <errno.h>",
            "#include <p101_tool_support/lesson_catalog.h>",
            "#include <stddef.h>",
            "#include <string.h>",
            "",
            f"/* Generated from {source_label}; do not edit. */",
            "",
            "const struct p101_tool_rule_definition *p101_tool_rule_definition_lookup(p101_tool_finding finding)",
            "{",
            "    // clang-format off",
            "    static const struct p101_tool_rule_definition rules[] = {",
            *rows,
            "    };",
            "",
            "    // clang-format on",
            "    const struct p101_tool_rule_definition *rule;",
            "",
            "    if(finding >= P101_TOOL_FINDING_COUNT)",
            "    {",
            "        errno = EINVAL;",
            "        rule  = NULL;",
            "    }",
            "    else",
            "    {",
            "        rule = &rules[finding];",
            "    }",
            "    return rule;",
            "}",
            "",
            "const struct p101_tool_rule_definition *p101_tool_rule_definition_lookup_id(const char *diagnostic_id)",
            "{",
            "    const struct p101_tool_rule_definition *p101_single_result_;",
            "",
            "    p101_single_result_ = NULL;",
            "    if(diagnostic_id == NULL)",
            "    {",
            "        errno = EINVAL;",
            "        goto p101_single_exit_;",
            "    }",
            "    for(p101_tool_finding finding = P101_TOOL_FINDING_WRAP_001; finding < P101_TOOL_FINDING_COUNT; finding++)",
            "    {",
            "        const struct p101_tool_rule_definition *definition;",
            "        int                                     comparison;",
            "",
            "        definition = p101_tool_rule_definition_lookup(finding);",
            "        comparison = strcmp(definition->id, diagnostic_id);",
            "        if(comparison == 0)",
            "        {",
            "            p101_single_result_ = definition;",
            "            break;",
            "        }",
            "    }",
            "",
            "p101_single_exit_:",
            "    return p101_single_result_;",
            "}",
            "",
        ]
    )


def update(path: Path, content: str, check: bool) -> bool:
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    if existing == content:
        return False
    if check:
        print(f"generated lesson catalog drift: {path}", file=sys.stderr)
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path}")
    return True


def main() -> int:
    root = workspace_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=root / "playgrounds" / "lessons" / "manifest.json")
    parser.add_argument("--header", type=Path, default=root / "libraries" / "lib_tool_support" / "include" / "p101_tool_support" / "lesson_catalog.h")
    parser.add_argument("--source", type=Path, default=root / "libraries" / "lib_tool_support" / "src" / "lesson_catalog.c")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        entries = load_entries(args.catalog.resolve())
        source_label = "playgrounds/lessons/manifest.json"
        changed = update(args.header.resolve(), header(entries, source_label), args.check)
        changed = update(args.source.resolve(), source(entries, source_label), args.check) or changed
    except CatalogError as error:
        print(f"generate-tool-lesson-catalog: {error}", file=sys.stderr)
        return 2
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
