#!/usr/bin/env python3
"""Ratchet shared parser, subprocess, lifecycle, and facade responsibilities."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
REGISTER = SCRIPTS_ROOT / "contracts" / "p101-source-responsibilities.json"
SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".hpp"}
CONFIG_ROOTS = ("libraries", "programs", "templates", "playgrounds")


class ResponsibilityError(ValueError):
    """A shared mechanism escaped its declared source owner."""


def source_files(roots: Iterable[str]) -> Iterable[Path]:
    for relative in roots:
        root = WORKSPACE / relative
        if not root.exists():
            raise ResponsibilityError(f"missing consumer root: {relative}")
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix in SOURCE_SUFFIXES
                and not any(part.startswith("build") for part in path.parts)
            ):
                yield path


def validate(document: dict[str, Any]) -> dict[str, int]:
    if document.get("schema") != "p101-source-responsibilities-v1":
        raise ResponsibilityError("unexpected source-responsibility schema")
    if not isinstance(document.get("does_not_prove"), str) or not document["does_not_prove"]:
        raise ResponsibilityError("register has no does_not_prove")
    owners = document.get("owners")
    facades = document.get("facades")
    if not isinstance(owners, list) or not owners:
        raise ResponsibilityError("register has no owners")
    if not isinstance(facades, list) or not facades:
        raise ResponsibilityError("register has no facade ratchets")

    checked_files: set[Path] = set()
    for owner in owners:
        if not isinstance(owner, dict):
            raise ResponsibilityError("owner row is not an object")
        identifier = owner.get("id")
        owner_root = owner.get("owner")
        markers = owner.get("markers")
        roots = owner.get("consumer_roots")
        if not isinstance(identifier, str) or not identifier:
            raise ResponsibilityError("owner has no id")
        if not isinstance(owner_root, str) or not (WORKSPACE / owner_root).is_dir():
            raise ResponsibilityError(f"owner {identifier} has no owner root")
        if not isinstance(markers, list) or not markers:
            raise ResponsibilityError(f"owner {identifier} has no markers")
        owner_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in source_files([owner_root])
        )
        for marker in markers:
            if not isinstance(marker, str) or marker not in owner_text:
                raise ResponsibilityError(f"owner {identifier} lacks marker {marker!r}")
        if not isinstance(roots, list) or not roots:
            raise ResponsibilityError(f"owner {identifier} has no consumers")

        forbidden_definitions = owner.get("forbidden_definitions", [])
        forbidden_calls = owner.get("forbidden_calls", [])
        for path in source_files(roots):
            checked_files.add(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            for symbol in forbidden_definitions:
                pattern = re.compile(
                    rf"(?m)^[ \t]*(?:static[ \t]+)?[A-Za-z_][A-Za-z0-9_ \t*]*"
                    rf"\b{re.escape(symbol)}\s*\([^;\n]*\)\s*\{{"
                )
                if pattern.search(text):
                    raise ResponsibilityError(
                        f"{path.relative_to(WORKSPACE)} redefines owner symbol {symbol}"
                    )
            for function in forbidden_calls:
                pattern = re.compile(rf"\b{re.escape(function)}\s*\(")
                if pattern.search(text):
                    raise ResponsibilityError(
                        f"{path.relative_to(WORKSPACE)} bypasses {identifier} with {function}()"
                    )

    for facade in facades:
        if not isinstance(facade, dict):
            raise ResponsibilityError("facade row is not an object")
        relative = facade.get("path")
        maximum = facade.get("maximum_lines")
        reason = facade.get("reason")
        if not isinstance(relative, str) or not isinstance(maximum, int) or maximum <= 0:
            raise ResponsibilityError("invalid facade ratchet")
        if not isinstance(reason, str) or not reason:
            raise ResponsibilityError(f"facade {relative} has no reason")
        path = WORKSPACE / relative
        if not path.is_file():
            raise ResponsibilityError(f"missing facade: {relative}")
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > maximum:
            raise ResponsibilityError(
                f"facade responsibility grew: {relative}={count}>{maximum}"
            )

    dependency_configs = 0
    dependency_edges = 0
    for root_name in CONFIG_ROOTS:
        root = WORKSPACE / root_name
        if not root.exists():
            continue
        for config in root.glob("*/config.cmake") if root_name != "playgrounds" else [root / "config.cmake"]:
            if not config.is_file():
                continue
            repository = config.parent
            config_text = config.read_text(encoding="utf-8")
            source_text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in source_files(
                    [
                        str((repository / child).relative_to(WORKSPACE))
                        for child in ("src", "include")
                        if (repository / child).exists()
                    ]
                )
            )
            declares_event = bool(
                re.search(r"(?m)^[ \t]+p101_tool_event(?:[ \t]|$)", config_text)
            )
            uses_event = "p101_tool_event/" in source_text
            uses_record = "p101_record/" in source_text
            declares_record = bool(
                re.search(r"(?m)^[ \t]+p101_record(?:[ \t]|$)", config_text)
            )
            if declares_event and not uses_event:
                raise ResponsibilityError(
                    f"{config.relative_to(WORKSPACE)} declares p101_tool_event without using its API"
                )
            if uses_record and not declares_record:
                raise ResponsibilityError(
                    f"{config.relative_to(WORKSPACE)} uses p101_record without declaring it"
                )
            declared_targets = set(
                re.findall(
                    r"(?m)^[ \t]+(p101_[A-Za-z0-9_]+)(?:[ \t]|$)",
                    "\n".join(
                        match.group(1)
                        for match in re.finditer(
                            r"set\([^)\s]*LIBRARIES\b(.*?)\)",
                            config_text,
                            re.DOTALL,
                        )
                    ),
                )
            )
            included_targets = set(
                re.findall(
                    r"(?m)^[ \t]*#[ \t]*include[ \t]*[<\"]"
                    r"(p101_[A-Za-z0-9_]+)/",
                    source_text,
                )
            )
            own_targets = set(
                re.findall(
                    r"(?m)^[ \t]+(p101_[A-Za-z0-9_]+)(?:[ \t]|$)",
                    re.search(
                        r"set\(LIBRARY_TARGETS\b(.*?)\)",
                        config_text,
                        re.DOTALL,
                    ).group(1)
                    if re.search(
                        r"set\(LIBRARY_TARGETS\b(.*?)\)",
                        config_text,
                        re.DOTALL,
                    )
                    else "",
                )
            )
            undeclared = included_targets - declared_targets - own_targets
            if undeclared:
                raise ResponsibilityError(
                    f"{config.relative_to(WORKSPACE)} has undeclared p101 "
                    f"include boundaries: {sorted(undeclared)}"
                )
            dependency_edges += len(included_targets - own_targets)
            dependency_configs += 1

    return {
        "owners": len(owners),
        "facades": len(facades),
        "consumer_files": len(checked_files),
        "dependency_configs": dependency_configs,
        "dependency_edges": dependency_edges,
    }


def main() -> int:
    try:
        report = validate(json.loads(REGISTER.read_text(encoding="utf-8")))
    except (ResponsibilityError, json.JSONDecodeError, OSError) as error:
        print(f"p101-source-responsibilities: {error}", file=sys.stderr)
        return 1
    print(
        "p101 source responsibilities: "
        f"{report['owners']} owners, {report['facades']} facade ratchets, "
        f"{report['consumer_files']} consumer source files, "
        f"{report['dependency_configs']} dependency manifests, "
        f"{report['dependency_edges']} discovered include boundaries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
