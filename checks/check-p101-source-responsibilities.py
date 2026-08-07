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
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from c_facts import CFactError, acquire  # noqa: E402

REGISTER = SCRIPTS_ROOT / "contracts" / "p101-source-responsibilities.json"
SOURCE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".hpp"}
CONFIG_ROOTS = ("libraries", "programs", "templates", "playgrounds")


class ResponsibilityError(ValueError):
    """A shared mechanism escaped its declared source owner."""


def path_is_beneath(path: str, relative_root: str) -> bool:
    try:
        Path(path).resolve().relative_to((WORKSPACE / relative_root).resolve())
    except (OSError, ValueError):
        return False
    return True


def is_repository_production_path(path: str, repository: Path) -> bool:
    try:
        relative = Path(path).resolve().relative_to(repository.resolve())
    except (OSError, ValueError):
        return False
    return not any(
        part in {"test", "tests", "fuzz", ".git"}
        or part == "build"
        or part.startswith(("build-", "build_", "build."))
        for part in relative.parts
    )


def source_files(roots: Iterable[str]) -> Iterable[Path]:
    for relative in roots:
        root = WORKSPACE / relative
        if not root.exists():
            raise ResponsibilityError(f"missing consumer root: {relative}")
        for path in root.rglob("*"):
            relative_parts = path.relative_to(root).parts
            if (
                path.is_file()
                and path.suffix in SOURCE_SUFFIXES
                and not any(part.startswith("build") for part in relative_parts)
            ):
                yield path


def gather_facts(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Acquire the semantic facts the register's roots admit.

    Gathering is split from judging so callers that judge one fact set many
    times — the negative-control tests mutate only the register — pay for the
    Clang parse once instead of once per verdict.
    """
    owners = document.get("owners")
    if not isinstance(owners, list):
        raise ResponsibilityError("register has no owners")
    admitted_roots: set[str] = set()
    for owner in owners:
        if isinstance(owner, dict):
            owner_root = owner.get("owner")
            roots = owner.get("consumer_roots")
            if isinstance(owner_root, str):
                admitted_roots.add(owner_root)
            if isinstance(roots, list):
                admitted_roots.update(root for root in roots if isinstance(root, str))
    admitted_paths = {WORKSPACE / path for path in admitted_roots}
    # The dependency-edge check filters every fact through
    # is_repository_production_path, so test, fuzz, and build trees under the
    # config roots can never be judged. Admit only the production paths of
    # each configured repository instead of the whole tree: the generated
    # fault-wrapper test files dwarf the sources they test, and parsing them
    # here bought nothing.
    for root_name in CONFIG_ROOTS:
        root = WORKSPACE / root_name
        if not root.exists():
            continue
        configs = (
            [root / "config.cmake"]
            if root_name == "playgrounds"
            else sorted(root.glob("*/config.cmake"))
        )
        for config in configs:
            if not config.is_file():
                continue
            repository = config.parent
            for name in ("src", "include"):
                candidate = repository / name
                if candidate.is_dir():
                    admitted_paths.add(candidate)
            admitted_paths.update(
                path
                for path in repository.iterdir()
                if path.is_file() and path.suffix in SOURCE_SUFFIXES
            )
    try:
        return acquire(WORKSPACE, sorted(admitted_paths))
    except CFactError as error:
        raise ResponsibilityError(str(error)) from error


def validate(document: dict[str, Any], facts: list[dict[str, Any]] | None = None) -> dict[str, int]:
    if document.get("schema") != "p101-source-responsibilities-v2":
        raise ResponsibilityError("unexpected source-responsibility schema")
    if not isinstance(document.get("does_not_prove"), str) or not document["does_not_prove"]:
        raise ResponsibilityError("register has no does_not_prove")
    owners = document.get("owners")
    facades = document.get("facades")
    if not isinstance(owners, list) or not owners:
        raise ResponsibilityError("register has no owners")
    if not isinstance(facades, list) or not facades:
        raise ResponsibilityError("register has no facade ratchets")

    if facts is None:
        facts = gather_facts(document)

    checked_files: set[Path] = set()
    for owner in owners:
        if not isinstance(owner, dict):
            raise ResponsibilityError("owner row is not an object")
        identifier = owner.get("id")
        owner_root = owner.get("owner")
        marker_usrs = owner.get("marker_usrs")
        roots = owner.get("consumer_roots")
        if not isinstance(identifier, str) or not identifier:
            raise ResponsibilityError("owner has no id")
        if not isinstance(owner_root, str) or not (WORKSPACE / owner_root).is_dir():
            raise ResponsibilityError(f"owner {identifier} has no owner root")
        if not isinstance(marker_usrs, list) or not marker_usrs:
            raise ResponsibilityError(f"owner {identifier} has no semantic markers")
        owner_definitions = {
            fact["usr"]
            for fact in facts
            if fact["kind"] == "FUNCTION"
            and path_is_beneath(fact["path"], owner_root)
        }
        for marker_usr in marker_usrs:
            if not isinstance(marker_usr, str) or marker_usr not in owner_definitions:
                raise ResponsibilityError(
                    f"owner {identifier} lacks declaration identity {marker_usr!r}"
                )
        if not isinstance(roots, list) or not roots:
            raise ResponsibilityError(f"owner {identifier} has no consumers")

        forbidden_definitions = owner.get("forbidden_definition_usrs", [])
        forbidden_calls = owner.get("forbidden_call_usrs", [])
        consumer_facts = [
            fact
            for fact in facts
            if any(path_is_beneath(fact["path"], root) for root in roots)
        ]
        for path in source_files(roots):
            checked_files.add(path)
        for fact in consumer_facts:
            if fact["kind"] == "FUNCTION" and fact["usr"] in forbidden_definitions:
                raise ResponsibilityError(
                    f"{Path(fact['path']).relative_to(WORKSPACE)} redefines "
                    f"owner declaration {fact['usr']}"
                )
            if fact["kind"] == "CALL" and fact["usr"] in forbidden_calls:
                raise ResponsibilityError(
                    f"{Path(fact['path']).relative_to(WORKSPACE)} bypasses "
                    f"{identifier} with declaration {fact['usr']}"
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
            repository_includes = {
                fact["value"]
                for fact in facts
                if fact["kind"] == "INCLUDE"
                and is_repository_production_path(
                    str(fact["path"]), repository
                )
            }
            declares_event = bool(
                re.search(r"(?m)^[ \t]+p101_tool_event(?:[ \t]|$)", config_text)
            )
            uses_event = any(
                str(target).startswith("p101_tool_event/")
                for target in repository_includes
            )
            uses_record = any(
                str(target).startswith("p101_record/")
                for target in repository_includes
            )
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
            included_targets = {
                str(target).split("/", 1)[0]
                for target in repository_includes
                if str(target).startswith("p101_") and "/" in str(target)
            }
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
