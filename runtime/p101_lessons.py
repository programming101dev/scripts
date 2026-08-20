#!/usr/bin/env python3
"""Resolve p101 diagnostic IDs to checked teaching lessons."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "p101-finding-lesson-catalog-v3"
RECEIPT_SCHEMA = "p101-lesson-acceptance-receipt-v1"
COVERAGE_SCHEMA = "p101-lesson-acceptance-coverage-v1"
DIAGNOSTIC_PATTERN = re.compile(r"\bP101-[A-Z][A-Z0-9_-]*-[0-9]{3}\b")
SUPPORTED_PLATFORMS = frozenset({"macos", "linux", "freebsd"})
ENVIRONMENT_ARGUMENT = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


class LessonCatalogError(ValueError):
    """The lesson catalog cannot safely be used."""


@dataclass(frozen=True)
class Lesson:
    lesson_id: str
    title: str
    path: str
    url: str
    finding_ids: tuple[str, ...]
    verification: str
    order: int
    acceptance_profile: str | None = None

    def url_for(self, finding_id: str | None = None) -> str:
        return self.url + (f"#{finding_id}" if finding_id is not None else "")

    def finding_json(self, finding_id: str) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "url": self.url_for(finding_id),
            "verification": self.verification,
        }


@dataclass(frozen=True)
class AcceptanceProfile:
    profile_id: str
    kind: str
    description: str
    finding_ids: tuple[str, ...]
    command: tuple[str, ...]
    cwd: str
    evidence_paths: tuple[str, ...]
    platforms: tuple[str, ...]
    quick: bool


@dataclass(frozen=True)
class Catalog:
    path: Path
    lessons: tuple[Lesson, ...]
    ignored_diagnostic_ids: frozenset[str]
    profiles: tuple[AcceptanceProfile, ...]
    default_platforms: tuple[str, ...]
    scope: dict[str, Any]

    @property
    def by_lesson_id(self) -> dict[str, Lesson]:
        return {lesson.lesson_id: lesson for lesson in self.lessons}

    @property
    def by_finding_id(self) -> dict[str, tuple[Lesson, ...]]:
        mapped: dict[str, list[Lesson]] = {}
        for lesson in self.lessons:
            for finding_id in lesson.finding_ids:
                mapped.setdefault(finding_id, []).append(lesson)
        return {
            finding_id: tuple(sorted(items, key=lambda item: (item.order, item.lesson_id)))
            for finding_id, items in mapped.items()
        }

    @property
    def workspace(self) -> Path:
        return self.path.parent.parent.parent

    @property
    def by_profile_id(self) -> dict[str, AcceptanceProfile]:
        return {profile.profile_id: profile for profile in self.profiles}


def _read_object(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise LessonCatalogError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise LessonCatalogError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict):
        raise LessonCatalogError(f"{path} must contain a JSON object")
    return document


def _strings(value: object, field: str, source: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise LessonCatalogError(f"{source}: {field} must be an array of non-empty strings")
    return tuple(value)


def _lesson(
    raw: dict[str, Any],
    manifest_dir: Path,
    url_base: str,
    source: Path,
    order: int,
) -> Lesson:
    required = (
        "lesson_id",
        "title",
        "path",
        "finding_ids",
        "verification",
        "reference_examples",
    )
    missing = [field for field in required if field not in raw]
    if missing:
        raise LessonCatalogError(f"{source}: missing lesson fields: {', '.join(missing)}")
    scalar = {field: raw[field] for field in ("lesson_id", "title", "path", "verification")}
    if not all(isinstance(value, str) and value for value in scalar.values()):
        raise LessonCatalogError(f"{source}: lesson scalar fields must be non-empty strings")
    acceptance_profile = raw.get("acceptance_profile")
    if not isinstance(acceptance_profile, str) or not acceptance_profile:
        raise LessonCatalogError(
            f"{source}: concept lesson {scalar['lesson_id']} needs acceptance_profile"
        )
    lesson_path = (manifest_dir / scalar["path"]).resolve()
    try:
        relative_path = lesson_path.relative_to(manifest_dir.parent).as_posix()
    except ValueError as error:
        raise LessonCatalogError(f"{source}: lesson path escapes the playground: {lesson_path}") from error
    if not lesson_path.is_file():
        raise LessonCatalogError(f"{source}: lesson file does not exist: {lesson_path}")
    text = lesson_path.read_text(encoding="utf-8")
    if not text.startswith("# ") or len(text.split()) < 25:
        raise LessonCatalogError(f"{source}: lesson must have a heading and substantive guidance")
    if text.count("\n## Platform boundary\n") != 1 or text.count("\n## Verification boundary\n") != 1:
        raise LessonCatalogError(
            f"{lesson_path}: lesson needs one explicit platform boundary and one verification boundary"
        )
    finding_ids = _strings(raw["finding_ids"], "finding_ids", source)
    example_ids = tuple(
        re.findall(r"^## (P101-[A-Z][A-Z0-9_-]*-[0-9]{3})\b", text, re.MULTILINE)
    )
    anchor_ids = tuple(
        re.findall(
            r'^<a id="(P101-[A-Z][A-Z0-9_-]*-[0-9]{3})"></a>$',
            text,
            re.MULTILINE,
        )
    )
    if len(example_ids) != len(set(example_ids)):
        raise LessonCatalogError(f"{lesson_path}: duplicate diagnostic example heading")
    if set(example_ids) != set(finding_ids):
        missing_examples = sorted(set(finding_ids) - set(example_ids))
        extra_examples = sorted(set(example_ids) - set(finding_ids))
        detail: list[str] = []
        if missing_examples:
            detail.append("missing " + ", ".join(missing_examples))
        if extra_examples:
            detail.append("unregistered " + ", ".join(extra_examples))
        raise LessonCatalogError(
            f"{lesson_path}: diagnostic examples do not match the manifest: "
            + "; ".join(detail)
        )
    if anchor_ids != example_ids:
        raise LessonCatalogError(
            f"{lesson_path}: every diagnostic heading needs its matching stable anchor"
        )
    required_example_fields = (
        "Broken input:",
        "Expected diagnostic:",
        "Repaired input:",
        "Expected clean result:",
    )
    if any(text.count(f"\n{field}\n") != len(finding_ids) for field in required_example_fields):
        raise LessonCatalogError(
            f"{lesson_path}: every diagnostic example needs one broken input, "
            "expected diagnostic, repaired input, and expected clean result"
        )
    section_matches = list(
        re.finditer(
            r"^## (P101-[A-Z][A-Z0-9_-]*-[0-9]{3})\b",
            text,
            re.MULTILINE,
        )
    )
    for index, match in enumerate(section_matches):
        end = section_matches[index + 1].start() if index + 1 < len(section_matches) else len(text)
        section = text[match.start() : end]
        finding_id = match.group(1)
        expected_match = re.search(
            r"^Expected diagnostic:\n\n```text\n(.*?)\n```$",
            section,
            re.MULTILINE | re.DOTALL,
        )
        clean_match = re.search(
            r"^Expected clean result:\n\n```text\n(.*?)\n```$",
            section,
            re.MULTILINE | re.DOTALL,
        )
        if (
            expected_match is None
            or finding_id not in expected_match.group(1)
            or clean_match is None
            or finding_id not in clean_match.group(1)
        ):
            raise LessonCatalogError(
                f"{lesson_path}: {finding_id} expected diagnostic and clean result "
                "must name the same diagnostic ID"
            )
    references = raw["reference_examples"]
    if not isinstance(references, list) or not references:
        raise LessonCatalogError(
            f"{source}: concept lesson {scalar['lesson_id']} needs reference_examples"
        )
    workspace = source.parent.parent.parent
    for index, reference in enumerate(references):
        if not isinstance(reference, dict) or not all(
            isinstance(reference.get(field), str) and reference.get(field)
            for field in ("repository", "path", "url")
        ):
            raise LessonCatalogError(
                f"{source}: {scalar['lesson_id']} reference_examples[{index}] "
                "needs repository, path, and url"
            )
        reference_path = workspace / "examples" / reference["repository"] / reference["path"]
        if not reference_path.is_file():
            raise LessonCatalogError(
                f"{source}: correct reference does not exist: {reference_path}"
            )
        if reference["url"] not in text:
            raise LessonCatalogError(
                f"{lesson_path}: correct reference URL is not visible in the lesson: "
                f"{reference['url']}"
            )
    verification_command = scalar["verification"].split()[0]
    if verification_command.startswith(("./", "scripts/", "programs/")):
        verification_path = workspace / verification_command.removeprefix("./")
        if not verification_path.is_file():
            raise LessonCatalogError(
                f"{source}: verification command does not exist: {verification_path}"
            )
    return Lesson(
        lesson_id=scalar["lesson_id"],
        title=scalar["title"],
        path=relative_path,
        url=url_base + relative_path,
        finding_ids=finding_ids,
        verification=scalar["verification"],
        order=order,
        acceptance_profile=acceptance_profile,
    )


def _profile(
    raw: dict[str, Any],
    workspace: Path,
    source: Path,
    index: int,
) -> AcceptanceProfile:
    required = (
        "profile_id",
        "kind",
        "description",
        "finding_ids",
        "command",
        "cwd",
        "evidence_paths",
        "platforms",
        "quick",
    )
    missing = [field for field in required if field not in raw]
    if missing:
        raise LessonCatalogError(
            f"{source}: acceptance_profiles[{index}] is missing: {', '.join(missing)}"
        )
    scalar = {
        field: raw[field]
        for field in ("profile_id", "kind", "description", "cwd")
    }
    if not all(isinstance(value, str) and value for value in scalar.values()):
        raise LessonCatalogError(
            f"{source}: acceptance profile scalar fields must be non-empty strings"
        )
    if raw["kind"] not in {"native-tool-suite", "native-policy-suite"}:
        raise LessonCatalogError(
            f"{source}: unsupported acceptance kind {raw['kind']}"
        )
    if not isinstance(raw["quick"], bool):
        raise LessonCatalogError(
            f"{source}: acceptance profile quick must be a boolean"
        )
    command = _strings(raw["command"], "command", source)
    evidence_paths = _strings(raw["evidence_paths"], "evidence_paths", source)
    platforms = _strings(raw["platforms"], "platforms", source)
    unknown_platforms = sorted(set(platforms) - SUPPORTED_PLATFORMS)
    if unknown_platforms:
        raise LessonCatalogError(
            f"{source}: unknown platforms in {raw['profile_id']}: "
            + ", ".join(unknown_platforms)
        )
    cwd = (workspace / raw["cwd"]).resolve()
    try:
        cwd.relative_to(workspace)
    except ValueError as error:
        raise LessonCatalogError(
            f"{source}: acceptance cwd escapes workspace: {cwd}"
        ) from error
    if not cwd.is_dir():
        raise LessonCatalogError(f"{source}: acceptance cwd is missing: {cwd}")
    for relative in evidence_paths:
        evidence = (workspace / relative).resolve()
        try:
            evidence.relative_to(workspace)
        except ValueError as error:
            raise LessonCatalogError(
                f"{source}: evidence path escapes workspace: {evidence}"
            ) from error
        if not evidence.exists():
            raise LessonCatalogError(
                f"{source}: acceptance evidence is missing: {evidence}"
            )
    finding_ids = _strings(raw["finding_ids"], "finding_ids", source)
    return AcceptanceProfile(
        profile_id=scalar["profile_id"],
        kind=scalar["kind"],
        description=scalar["description"],
        finding_ids=finding_ids,
        command=command,
        cwd=raw["cwd"],
        evidence_paths=evidence_paths,
        platforms=platforms,
        quick=raw["quick"],
    )


def load_catalog(path: Path) -> Catalog:
    path = path.resolve()
    document = _read_object(path)
    if document.get("schema") != SCHEMA:
        raise LessonCatalogError(f"{path}: schema must be {SCHEMA}")
    url_base = document.get("url_base")
    if not isinstance(url_base, str) or not url_base.endswith("/"):
        raise LessonCatalogError(f"{path}: url_base must be a non-empty URL ending in /")
    ignored = frozenset(
        _strings(document.get("ignored_diagnostic_ids", []), "ignored_diagnostic_ids", path)
    )
    default_platforms = _strings(
        document.get("default_platforms", []), "default_platforms", path
    )
    unknown_platforms = sorted(set(default_platforms) - SUPPORTED_PLATFORMS)
    if unknown_platforms:
        raise LessonCatalogError(
            f"{path}: unknown default platforms: {', '.join(unknown_platforms)}"
        )
    if set(default_platforms) != set(SUPPORTED_PLATFORMS):
        raise LessonCatalogError(
            f"{path}: default_platforms must cover macos, linux, and freebsd"
        )
    manifest_dir = path.parent
    workspace = manifest_dir.parent.parent
    raw_profiles = document.get("acceptance_profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise LessonCatalogError(f"{path}: acceptance_profiles must be a non-empty array")
    profiles: list[AcceptanceProfile] = []
    for index, raw_profile in enumerate(raw_profiles):
        if not isinstance(raw_profile, dict):
            raise LessonCatalogError(
                f"{path}: acceptance_profiles[{index}] must be an object"
            )
        profiles.append(_profile(raw_profile, workspace, path, index))
    profile_ids = [profile.profile_id for profile in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise LessonCatalogError(f"{path}: duplicate acceptance profile ID")
    raw_lessons = document.get("lessons")
    if not isinstance(raw_lessons, list):
        raise LessonCatalogError(f"{path}: lessons must be an array")

    lessons: list[Lesson] = []
    for index, raw in enumerate(raw_lessons):
        if not isinstance(raw, dict):
            raise LessonCatalogError(f"{path}: lessons[{index}] must be an object")
        lessons.append(_lesson(raw, manifest_dir, url_base, path, 10000 + index))

    scope = document.get("scope")
    if not isinstance(scope, dict):
        raise LessonCatalogError(f"{path}: scope must be an object")
    example_count = scope.get("example_count")
    if not isinstance(example_count, int) or isinstance(example_count, bool):
        raise LessonCatalogError(f"{path}: scope.example_count must be an integer")
    responsibilities = scope.get("supporting_responsibilities")
    if not isinstance(responsibilities, list) or not responsibilities:
        raise LessonCatalogError(
            f"{path}: scope.supporting_responsibilities must be a non-empty array"
        )
    for index, responsibility in enumerate(responsibilities):
        if not isinstance(responsibility, dict) or not all(
            isinstance(responsibility.get(field), str)
            and responsibility.get(field)
            for field in ("id", "description")
        ):
            raise LessonCatalogError(
                f"{path}: scope.supporting_responsibilities[{index}] needs "
                "non-empty id and description strings"
            )
    excluded_content = _strings(
        scope.get("excluded_content"), "scope.excluded_content", path
    )
    if not excluded_content:
        raise LessonCatalogError(
            f"{path}: scope.excluded_content must not be empty"
        )

    lesson_ids: set[str] = set()
    finding_owners: dict[str, str] = {}
    for lesson in lessons:
        if lesson.lesson_id in lesson_ids:
            raise LessonCatalogError(f"{path}: duplicate lesson_id {lesson.lesson_id}")
        lesson_ids.add(lesson.lesson_id)
        for finding_id in lesson.finding_ids:
            if DIAGNOSTIC_PATTERN.fullmatch(finding_id) is None:
                raise LessonCatalogError(
                    f"{path}: invalid finding ID {finding_id} in {lesson.lesson_id}"
                )
            previous = finding_owners.get(finding_id)
            if previous is not None:
                raise LessonCatalogError(
                    f"{path}: finding {finding_id} appears in lessons "
                    f"{previous} and {lesson.lesson_id}"
                )
            finding_owners[finding_id] = lesson.lesson_id
        if (
            lesson.acceptance_profile is not None
            and lesson.acceptance_profile not in set(profile_ids)
        ):
            raise LessonCatalogError(
                f"{path}: {lesson.lesson_id} names unknown acceptance profile "
                f"{lesson.acceptance_profile}"
            )
    profile_findings: dict[str, str] = {}
    for profile in profiles:
        for finding_id in profile.finding_ids:
            if DIAGNOSTIC_PATTERN.fullmatch(finding_id) is None:
                raise LessonCatalogError(
                    f"{path}: invalid finding ID {finding_id} in {profile.profile_id}"
                )
            previous = profile_findings.get(finding_id)
            if previous is not None:
                raise LessonCatalogError(
                    f"{path}: finding {finding_id} appears in acceptance profiles "
                    f"{previous} and {profile.profile_id}"
                )
            profile_findings[finding_id] = profile.profile_id
    for lesson in lessons:
        if lesson.acceptance_profile is None:
            continue
        profile = next(
            item for item in profiles if item.profile_id == lesson.acceptance_profile
        )
        absent = sorted(set(lesson.finding_ids) - set(profile.finding_ids))
        if absent:
            raise LessonCatalogError(
                f"{path}: {lesson.lesson_id} findings missing from profile "
                f"{profile.profile_id}: {', '.join(absent)}"
            )
    if example_count != len(finding_owners):
        raise LessonCatalogError(
            f"{path}: scope.example_count is {example_count}, expected "
            f"{len(finding_owners)}"
        )
    catalog = Catalog(
        path,
        tuple(lessons),
        ignored,
        tuple(profiles),
        default_platforms,
        scope,
    )
    _validate_acceptance_contract(catalog)
    return catalog


def _evidence_text(workspace: Path, relative_paths: Iterable[str]) -> str:
    chunks: list[str] = []
    for relative in relative_paths:
        path = (workspace / relative).resolve()
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if (
                not candidate.is_file()
                or any(part.startswith("build-") for part in candidate.parts)
                or candidate.suffix
                not in {"", ".c", ".h", ".py", ".sh", ".json", ".md", ".txt"}
            ):
                continue
            try:
                chunks.append(candidate.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(chunks)


def _native_finding_symbol(finding_id: str) -> str:
    return "P101_TOOL_FINDING_" + finding_id.removeprefix("P101-").replace("-", "_")


def _validate_acceptance_contract(catalog: Catalog) -> None:
    profile_by_id = catalog.by_profile_id
    for profile in catalog.profiles:
        text = _evidence_text(catalog.workspace, profile.evidence_paths)
        literal_ids = set(DIAGNOSTIC_PATTERN.findall(text))
        missing = sorted(
            finding_id
            for finding_id in profile.finding_ids
            if finding_id not in literal_ids
            and _native_finding_symbol(finding_id) not in text
        )
        if missing:
            raise LessonCatalogError(
                f"{catalog.path}: {profile.profile_id} evidence does not name: "
                + ", ".join(missing)
            )

    covered: set[str] = set()
    for lesson in catalog.lessons:
        if lesson.acceptance_profile is not None:
            profile = profile_by_id[lesson.acceptance_profile]
            covered.update(set(lesson.finding_ids) & set(profile.finding_ids))
    missing = sorted(set(catalog.by_finding_id) - covered)
    if missing:
        raise LessonCatalogError(
            f"{catalog.path}: diagnostics without executable acceptance evidence: "
            + ", ".join(missing)
        )


def annotate_document(document: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    findings = document.get("findings")
    if not isinstance(findings, list):
        return document
    by_finding = catalog.by_finding_id
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_id = finding.get("id")
        if not isinstance(finding_id, str):
            continue
        lessons = by_finding.get(finding_id, ())
        if lessons:
            finding["lesson"] = {
                "primary": lessons[0].finding_json(finding_id),
                "related": [
                    lesson.finding_json(finding_id) for lesson in lessons[1:]
                ],
            }
    document["lesson_catalog"] = {
        "schema": SCHEMA,
        "mapped_findings": sum(
            1
            for finding in findings
            if isinstance(finding, dict) and isinstance(finding.get("lesson"), dict)
        ),
        "unmapped_finding_ids": sorted(
            {
                str(finding.get("id"))
                for finding in findings
                if isinstance(finding, dict)
                and isinstance(finding.get("id"), str)
                and not isinstance(finding.get("lesson"), dict)
                and finding.get("id") not in catalog.ignored_diagnostic_ids
            }
        ),
    }
    return document


def annotate_report(path: Path, catalog: Catalog) -> None:
    document = _read_object(path)
    annotate_document(document, catalog)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def catalog_digest(catalog: Catalog) -> str:
    digest = hashlib.sha256()
    digest.update(catalog.path.read_bytes())
    for lesson in sorted(catalog.lessons, key=lambda item: item.lesson_id):
        digest.update(
            json.dumps(
                {
                    "lesson_id": lesson.lesson_id,
                    "title": lesson.title,
                    "path": lesson.path,
                    "url": lesson.url,
                    "finding_ids": lesson.finding_ids,
                    "verification": lesson.verification,
                    "order": lesson.order,
                    "acceptance_profile": lesson.acceptance_profile,
                },
                sort_keys=True,
            ).encode("utf-8")
        )
        digest.update((catalog.path.parent.parent / lesson.path).read_bytes())
    for profile in sorted(catalog.profiles, key=lambda item: item.profile_id):
        digest.update(
            json.dumps(
                {
                    "profile_id": profile.profile_id,
                    "kind": profile.kind,
                    "description": profile.description,
                    "finding_ids": profile.finding_ids,
                    "command": profile.command,
                    "cwd": profile.cwd,
                    "evidence_paths": profile.evidence_paths,
                    "platforms": profile.platforms,
                    "quick": profile.quick,
                },
                sort_keys=True,
            ).encode("utf-8")
        )
    return digest.hexdigest()


def discover_diagnostic_ids(workspace: Path) -> set[str]:
    ids: set[str] = set()
    programs = workspace / "programs"
    for tool in sorted(programs.glob("p101-*")):
        candidates: list[Path] = []
        for source_name in ("src", "include"):
            source = tool / source_name
            if source.is_dir():
                candidates.extend(source.rglob("*"))
        for candidate in tool.iterdir():
            if candidate.is_file() and (
                candidate.suffix == ".py" or (not candidate.suffix and candidate.name.startswith("p101-"))
            ):
                candidates.append(candidate)
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix not in {"", ".c", ".h", ".inc", ".py"}:
                continue
            try:
                ids.update(DIAGNOSTIC_PATTERN.findall(candidate.read_text(encoding="utf-8")))
            except (OSError, UnicodeError):
                continue
    candidates = [
        workspace / "libraries" / "lib_tool_event" / "src" / "analysis.c",
        *sorted((workspace / "scripts" / "rules").glob("*.json")),
    ]
    for candidate in candidates:
        try:
            ids.update(DIAGNOSTIC_PATTERN.findall(candidate.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            continue
    return ids


def validate_coverage(catalog: Catalog, workspace: Path) -> tuple[set[str], set[str]]:
    discovered = discover_diagnostic_ids(workspace)
    mapped = set(catalog.by_finding_id)
    missing = discovered - mapped - set(catalog.ignored_diagnostic_ids)
    ignored_unknown = set(catalog.ignored_diagnostic_ids) - discovered
    return missing, ignored_unknown


def iter_evidence_paths(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.suffix in {".json", ".log", ".md", ".txt"}
            )


def finding_ids_from_evidence(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    if path.suffix == ".json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            return set(DIAGNOSTIC_PATTERN.findall(text))
        if isinstance(document, dict) and isinstance(document.get("findings"), list):
            return {
                str(finding["id"])
                for finding in document["findings"]
                if isinstance(finding, dict) and isinstance(finding.get("id"), str)
            }
    return set(DIAGNOSTIC_PATTERN.findall(text))


def host_platform() -> str:
    name = platform.system().lower()
    if name == "darwin":
        return "macos"
    if name == "freebsd":
        return "freebsd"
    if name == "linux":
        return "linux"
    return name


def _acceptance_for_lesson(
    catalog: Catalog,
    lesson: Lesson,
    finding_id: str | None,
) -> dict[str, Any]:
    if lesson.acceptance_profile is None:
        raise LessonCatalogError(f"{lesson.lesson_id} has no acceptance evidence")
    profile = catalog.by_profile_id[lesson.acceptance_profile]
    return {
        "finding_id": finding_id,
        "lesson_id": lesson.lesson_id,
        "lesson_title": lesson.title,
        "native_evidence": profile.kind,
        "native_reference": profile.profile_id,
        "broken_command": " ".join(profile.command),
        "repair_command": lesson.verification,
        "repair_oracle": "diagnostic-absent-from-student-evidence",
        "platforms": list(profile.platforms),
    }


def acceptance_for(catalog: Catalog, finding_id: str) -> dict[str, Any]:
    lessons = catalog.by_finding_id.get(finding_id, ())
    if not lessons:
        raise LessonCatalogError(f"no lesson for {finding_id}")
    return _acceptance_for_lesson(catalog, lessons[0], finding_id)


def resolve_target(
    catalog: Catalog,
    target: str,
) -> tuple[Lesson, str | None, dict[str, Any]]:
    lessons = catalog.by_finding_id.get(target)
    if lessons:
        acceptance = acceptance_for(catalog, target)
        lesson = catalog.by_lesson_id[str(acceptance["lesson_id"])]
        return lesson, target, acceptance
    lesson = catalog.by_lesson_id.get(target)
    if lesson is None:
        raise LessonCatalogError(f"no finding or lesson named {target}")
    finding_id = lesson.finding_ids[0] if lesson.finding_ids else None
    return lesson, finding_id, _acceptance_for_lesson(catalog, lesson, finding_id)


def _verified_full_platforms(
    catalog: Catalog,
    receipt_paths: Iterable[Path],
) -> set[str]:
    verified: set[str] = set()
    digest = catalog_digest(catalog)
    for path in _receipt_paths(receipt_paths):
        try:
            receipt = _read_object(path)
        except LessonCatalogError:
            continue
        summary = receipt.get("summary")
        current = receipt.get("platform")
        if (
            receipt.get("schema") == RECEIPT_SCHEMA
            and receipt.get("mode") == "full"
            and receipt.get("catalog_sha256") == digest
            and isinstance(summary, dict)
            and summary.get("result") == "PASS"
            and isinstance(current, str)
        ):
            verified.add(current)
    return verified


def coverage_document(
    catalog: Catalog,
    receipt_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    verified_platforms = _verified_full_platforms(catalog, receipt_paths)
    missing_ids, _ = validate_coverage(catalog, catalog.workspace)
    rows = [
        acceptance_for(catalog, finding_id)
        for finding_id in sorted(catalog.by_finding_id)
    ]
    for row in rows:
        row["verified_platforms"] = sorted(
            set(row["platforms"]) & verified_platforms
        )
    lesson_rows: list[dict[str, Any]] = []
    for lesson in sorted(catalog.lessons, key=lambda item: (item.order, item.lesson_id)):
        finding_id = lesson.finding_ids[0] if lesson.finding_ids else None
        acceptance = _acceptance_for_lesson(catalog, lesson, finding_id)
        lesson_rows.append(
            {
                "lesson_id": lesson.lesson_id,
                "title": lesson.title,
                "finding_ids": list(lesson.finding_ids),
                "native_evidence": acceptance["native_evidence"],
                "native_reference": acceptance["native_reference"],
                "repair_oracle": acceptance["repair_oracle"],
                "platforms": acceptance["platforms"],
                "verified_platforms": sorted(
                    set(acceptance["platforms"]) & verified_platforms
                ),
            }
        )
    return {
        "schema": COVERAGE_SCHEMA,
        "catalog_schema": SCHEMA,
        "catalog_sha256": catalog_digest(catalog),
        "generated_unix_ns": time.time_ns(),
        "summary": {
            "lessons": len(catalog.lessons),
            "lessons_with_acceptance": len(lesson_rows),
            "diagnostic_ids": len(rows),
            "native_case_ids": 0,
            "native_suite_ids": len(rows),
            "protocol_pairs": len(rows),
            "uncovered_ids": len(missing_ids),
            "platform_contract": list(catalog.default_platforms),
            "platforms_verified_by_full_receipt": sorted(verified_platforms),
        },
        "lessons": lesson_rows,
        "diagnostics": rows,
    }


def coverage_markdown(document: dict[str, Any]) -> str:
    summary = document["summary"]
    lines = [
        "# p101 executable lesson coverage",
        "",
        f"- Lessons: {summary['lessons']}",
        f"- Lessons with acceptance evidence: {summary['lessons_with_acceptance']}",
        f"- Diagnostic IDs: {summary['diagnostic_ids']}",
        f"- Diagnostic IDs backed by native owning-tool suites: {summary['native_suite_ids']}",
        f"- Broken/repaired protocol pairs: {summary['protocol_pairs']}",
        f"- Uncovered IDs: {summary['uncovered_ids']}",
        f"- Platform contract: {', '.join(summary['platform_contract'])}",
        "- Platforms verified by supplied full receipts: "
        + (
            ", ".join(summary["platforms_verified_by_full_receipt"])
            or "none"
        ),
        "",
        "## Lesson coverage",
        "",
        "| Lesson | Findings | Native evidence | Repair oracle | Contract | Verified |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in document["lessons"]:
        findings = ", ".join(f"`{item}`" for item in row["finding_ids"]) or "—"
        lines.append(
            f"| `{row['lesson_id']}` — {row['title']} | {findings} | "
            f"{row['native_evidence']}: `{row['native_reference']}` | "
            f"{row['repair_oracle']} | {', '.join(row['platforms'])} | "
            f"{', '.join(row['verified_platforms']) or '—'} |"
        )
    lines.extend(
        [
        "",
        "## Diagnostic coverage",
        "",
        "| Finding | Lesson | Native evidence | Repair oracle | Contract | Verified |",
        "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in document["diagnostics"]:
        lines.append(
            f"| `{row['finding_id']}` | `{row['lesson_id']}` — "
            f"{row['lesson_title']} | {row['native_evidence']}: "
            f"`{row['native_reference']}` | {row['repair_oracle']} | "
            f"{', '.join(row['platforms'])} | "
            f"{', '.join(row['verified_platforms']) or '—'} |"
        )
    return "\n".join(lines) + "\n"


def protocol_pair(catalog: Catalog, finding_id: str) -> dict[str, Any]:
    broken: dict[str, Any] = {
        "schema": "p101-lesson-fixture-v1",
        "findings": [
            {
                "id": finding_id,
                "severity": "error",
                "location": {
                    "file": "student.c",
                    "line": 1,
                    "function": "lesson_fixture",
                },
                "message": "canonical broken-state lesson fixture",
            }
        ],
    }
    fixed: dict[str, Any] = {
        "schema": "p101-lesson-fixture-v1",
        "findings": [],
    }
    annotate_document(broken, catalog)
    annotate_document(fixed, catalog)
    lesson = broken["findings"][0].get("lesson")
    if not isinstance(lesson, dict) or not isinstance(lesson.get("primary"), dict):
        raise LessonCatalogError(f"{finding_id}: broken fixture was not mapped")
    if fixed.get("lesson_catalog", {}).get("mapped_findings") != 0:
        raise LessonCatalogError(f"{finding_id}: repaired fixture still has findings")
    return {
        "finding_id": finding_id,
        "broken": broken,
        "repaired": fixed,
        "lesson_id": lesson["primary"]["lesson_id"],
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value)


def _output_directory(path: Path | None, prefix: str) -> Path:
    if path is None:
        return Path(tempfile.mkdtemp(prefix=prefix))
    resolved = path.resolve()
    if resolved.exists():
        raise LessonCatalogError(f"output path already exists: {resolved}")
    resolved.mkdir(parents=True)
    return resolved


def _write_json(path: Path, document: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _run_logged(
    command: list[str],
    cwd: Path,
    output: Path,
    label: str,
    timeout_seconds: int = 900,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    log = output / f"{_safe_name(label)}.log"
    started = time.time_ns()
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
            env={
                **os.environ,
                "PYTHONPYCACHEPREFIX": str(output / "pycache"),
                **(environment or {}),
            },
        )
        text, _ = process.communicate(timeout=timeout_seconds)
        code = process.returncode
    except subprocess.TimeoutExpired as error:
        code = 2
        captured = error.stdout or ""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", errors="replace")
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                completed_text, _ = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                completed_text, _ = process.communicate()
            if completed_text:
                captured = completed_text
        text = str(captured) + f"\ncommand timed out after {timeout_seconds} seconds\n"
    except (OSError, UnicodeError) as error:
        code = 2
        text = f"could not run {' '.join(command)}: {error}\n"
    log.write_text("$ " + " ".join(command) + "\n\n" + text, encoding="utf-8")
    return {
        "label": label,
        "command": command,
        "cwd": str(cwd),
        "exit": code,
        "status": "PASS" if code == 0 else "FAIL",
        "duration_ns": time.time_ns() - started,
        "log": log.name,
    }


def _run_profile(
    catalog: Catalog,
    profile: AcceptanceProfile,
    output: Path,
) -> dict[str, Any]:
    current = host_platform()
    if current not in profile.platforms:
        return {
            "label": profile.profile_id,
            "status": "SKIP",
            "reason": f"{current} is not in the profile platform contract",
        }
    command: list[str] = []
    for argument in profile.command:
        match = ENVIRONMENT_ARGUMENT.fullmatch(argument)
        if match is None:
            command.append(argument)
            continue
        name = match.group(1)
        value = os.environ.get(name, "")
        if not value:
            return {
                "label": profile.profile_id,
                "status": "SKIP",
                "reason": f"qualified tool environment is missing: {name}",
            }
        command.append(value)
    repository = (catalog.workspace / profile.cwd).resolve()
    return _run_logged(
        command,
        repository,
        output,
        "profile-" + profile.profile_id,
        environment={
            "P101_REPOSITORY_ROOT": str(repository),
            # Native tools discover workspace contracts from their build-tree
            # ancestry.  Keep lesson builds below the declared repository even
            # when a surrounding CI campaign exports a shared test cache.
            "P101_TEST_BUILD_CACHE": "",
        },
    )


def _run_profiles(
    catalog: Catalog,
    profiles: list[AcceptanceProfile],
    output: Path,
    jobs: int,
) -> list[dict[str, Any]]:
    representatives: list[AcceptanceProfile] = []
    representative_by_key: dict[
        tuple[tuple[str, ...], str], AcceptanceProfile
    ] = {}
    for profile in profiles:
        key = (profile.command, profile.cwd)
        if key not in representative_by_key:
            representative_by_key[key] = profile
            representatives.append(profile)
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        executed = list(
            executor.map(
                lambda profile: _run_profile(catalog, profile, output),
                representatives,
            )
        )
    if len(representatives) != len(executed):
        raise LessonCatalogError(
            "lesson acceptance executor returned an incomplete result set"
        )
    result_by_key = {
        (profile.command, profile.cwd): result
        for profile, result in zip(representatives, executed)
    }
    results: list[dict[str, Any]] = []
    for profile in profiles:
        key = (profile.command, profile.cwd)
        original = result_by_key[key]
        representative = representative_by_key[key]
        if profile is representative:
            results.append(original)
        else:
            results.append(
                {
                    **original,
                    "label": "profile-" + profile.profile_id,
                    "duration_ns": 0,
                    "shared_with": original["label"],
                }
            )
    return results


def _write_unit_test_evidence(
    catalog: Catalog,
    profiles: list[AcceptanceProfile],
    results: list[dict[str, Any]],
    output: Path,
) -> None:
    """Publish reusable repository test results from native profiles."""
    statuses: dict[str, str] = {}
    for profile, result in zip(profiles, results):
        if not profile.command:
            continue
        executable = (
            catalog.workspace / profile.cwd / profile.command[0]
        ).resolve()
        if executable.name != "test.sh" or not executable.is_file():
            continue
        try:
            executable.relative_to(catalog.workspace.resolve())
        except ValueError:
            continue
        status = str(result.get("status", ""))
        if status not in {"PASS", "FAIL"}:
            continue
        repository = executable.parent.name
        previous = statuses.get(repository)
        statuses[repository] = (
            "FAIL" if "FAIL" in {previous, status} else "PASS"
        )
    # Keep the established generic two-column receipt spelling accepted by
    # check-repository-tests.sh; rows may now name tools as well as libraries.
    text = "library\tstatus\n" + "".join(
        f"{repository}\t{statuses[repository]}\n"
        for repository in sorted(statuses)
    )
    (output / "unit-tests.tsv").write_text(text, encoding="utf-8")


def _write_protocol_fixtures(
    catalog: Catalog,
    output: Path,
    finding_ids: Iterable[str],
) -> list[dict[str, Any]]:
    pairs = [protocol_pair(catalog, finding_id) for finding_id in finding_ids]
    _write_json(
        output / "protocol-pairs.json",
        {
            "schema": "p101-lesson-protocol-pairs-v1",
            "pairs": pairs,
        },
    )
    return pairs


def _receipt(
    catalog: Catalog,
    mode: str,
    results: list[dict[str, Any]],
    protocol_pairs: int,
) -> dict[str, Any]:
    failures = sum(result.get("status") == "FAIL" for result in results)
    completed = sum(
        result.get("status") in {"PASS", "FAIL"} for result in results
    )
    receipt_result = (
        "FAIL"
        if failures != 0 or (mode == "full" and completed == 0)
        else "PASS"
    )
    return {
        "schema": RECEIPT_SCHEMA,
        "mode": mode,
        "platform": host_platform(),
        "catalog_sha256": catalog_digest(catalog),
        "completed_unix_ns": time.time_ns(),
        "protocol_pairs": protocol_pairs,
        "native_results": results,
        "summary": {
            "native_passed": sum(
                result.get("status") == "PASS" for result in results
            ),
            "native_failed": failures,
            "native_skipped": sum(
                result.get("status") == "SKIP" for result in results
            ),
            "result": receipt_result,
        },
    }


def _print_native_failures(
    results: list[dict[str, Any]], output: Path
) -> None:
    printed_logs: set[str] = set()
    for result in results:
        if result.get("status") != "FAIL":
            continue
        label = str(result.get("label", "unknown"))
        command = result.get("command", [])
        print(f"\n--- native lesson failure: {label} ---")
        if isinstance(command, list):
            print("$ " + " ".join(str(argument) for argument in command))
        print(f"cwd: {result.get('cwd', '?')}")
        print(f"exit: {result.get('exit', '?')}")
        log_name = result.get("log")
        if not isinstance(log_name, str) or log_name in printed_logs:
            continue
        printed_logs.add(log_name)
        log_path = output / log_name
        try:
            log_text = log_path.read_text(encoding="utf-8")
        except OSError as error:
            print(f"could not read failure log {log_path}: {error}")
            continue
        print(f"log: {log_path}")
        print(log_text, end="" if log_text.endswith("\n") else "\n")
        print(f"--- end native lesson failure: {label} ---")


def command_check(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        missing, ignored_unknown = validate_coverage(catalog, args.workspace.resolve())
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    if missing:
        print("p101 lessons: diagnostics without lessons:", file=sys.stderr)
        for finding_id in sorted(missing):
            print(f"  {finding_id}", file=sys.stderr)
        return 1
    if ignored_unknown:
        print("p101 lessons: stale ignored diagnostic IDs:", file=sys.stderr)
        for finding_id in sorted(ignored_unknown):
            print(f"  {finding_id}", file=sys.stderr)
        return 1
    print(
        f"p101 lesson catalog: {len(catalog.lessons)} lessons, "
        f"{len(catalog.by_finding_id)} diagnostic IDs, 0 unmapped tool diagnostics"
    )
    return 0


def command_show(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        selected, _, acceptance = resolve_target(catalog, args.finding_id)
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    lessons = catalog.by_finding_id.get(args.finding_id, (selected,))
    for index, lesson in enumerate(lessons):
        label = "Primary lesson" if index == 0 else "Related lesson"
        print(f"{label}: {lesson.lesson_id} — {lesson.title}")
        print(f"  {lesson.url_for(args.finding_id if args.finding_id in lesson.finding_ids else None)}")
        print(f"  verify: {lesson.verification}")
    print(
        "Acceptance: "
        f"{acceptance['native_evidence']} ({acceptance['native_reference']})"
    )
    print(f"  repair oracle: {acceptance['repair_oracle']}")
    print(f"  platforms: {', '.join(acceptance['platforms'])}")
    return 0


def command_list(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    for finding_id, lessons in sorted(catalog.by_finding_id.items()):
        print(
            f"{finding_id}\t{lessons[0].lesson_id}\t{lessons[0].title}\t"
            f"{lessons[0].url_for(finding_id)}"
        )
    return 0


def command_guide(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        finding_ids: set[str] = set()
        for path in iter_evidence_paths(args.paths):
            finding_ids.update(finding_ids_from_evidence(path))
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    if not finding_ids:
        if args.markdown:
            print("# p101 lesson guide\n\nNo open findings in the supplied reports.")
        else:
            print("p101 lessons: no open findings in the supplied reports")
        return 0
    by_finding = catalog.by_finding_id
    unmapped = sorted(finding_ids - set(by_finding) - set(catalog.ignored_diagnostic_ids))
    if args.markdown:
        print("# p101 lesson guide")
        print()
        print("These lessons correspond to the findings in this report. Fix the evidence at the cited source location, then run the listed verification command.")
        print()
    for finding_id in sorted(finding_ids):
        lessons = by_finding.get(finding_id)
        if lessons:
            if args.markdown:
                print(f"## {finding_id}: {lessons[0].title}")
                print()
                print(
                    f"- Lesson: [{lessons[0].lesson_id}]"
                    f"({lessons[0].url_for(finding_id)})"
                )
                print(f"- Verify: `{lessons[0].verification}`")
                print()
            else:
                print(f"{finding_id}: {lessons[0].title}")
                print(f"  {lessons[0].url_for(finding_id)}")
                print(f"  verify: {lessons[0].verification}")
    if unmapped:
        print("Unmapped findings: " + ", ".join(unmapped), file=sys.stderr)
        return 1
    return 0


def command_coverage(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        document = coverage_document(catalog, args.receipts)
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    text = (
        json.dumps(document, indent=2, sort_keys=True) + "\n"
        if args.json
        else coverage_markdown(document)
    )
    if args.output is None:
        print(text, end="")
    else:
        args.output.write_text(text, encoding="utf-8")
        print(args.output)
    return 0


def command_run(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        lesson, finding_id, acceptance = resolve_target(catalog, args.finding_id)
        output = _output_directory(
            args.output,
            f"p101-lesson-{_safe_name(args.finding_id)}.",
        )
        pairs = (
            _write_protocol_fixtures(catalog, output, [finding_id])
            if finding_id is not None
            else []
        )
        results: list[dict[str, Any]] = []
        if not args.protocol_only:
            profile_id = str(acceptance["native_reference"])
            results.append(
                _run_profile(catalog, catalog.by_profile_id[profile_id], output)
            )
        receipt = _receipt(catalog, "lesson-run", results, len(pairs))
        receipt["target"] = args.finding_id
        receipt["lesson_id"] = lesson.lesson_id
        if finding_id is not None:
            receipt["finding_id"] = finding_id
        receipt["acceptance"] = acceptance
        _write_json(output / "receipt.json", receipt)
        (output / "README.md").write_text(
            f"# {args.finding_id}: {acceptance['lesson_title']}\n\n"
            f"- Lesson: `{acceptance['lesson_id']}`\n"
            f"- Native evidence: {acceptance['native_evidence']} "
            f"(`{acceptance['native_reference']}`)\n"
            f"- Repair oracle: {acceptance['repair_oracle']}\n"
            f"- Student verification: `{acceptance['repair_command']}`\n"
            f"- Receipt: [receipt.json](./receipt.json)\n"
            + (
                "- Canonical broken/repaired pair: "
                "[protocol-pairs.json](./protocol-pairs.json)\n"
                if pairs
                else "- Protocol pair: not applicable; this lesson has no diagnostic ID\n"
            ),
            encoding="utf-8",
        )
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    print(f"p101 lesson exercise: {output}")
    print(f"Receipt: {output / 'receipt.json'}")
    return 0 if receipt["summary"]["result"] == "PASS" else 1


def command_verify_one(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        lesson, finding_id, acceptance = resolve_target(catalog, args.finding_id)
        output = _output_directory(
            args.output,
            f"p101-lesson-verify-{_safe_name(args.finding_id)}.",
        )
        results: list[dict[str, Any]] = []
        if args.paths:
            if finding_id is None:
                raise LessonCatalogError(
                    f"{lesson.lesson_id} has no diagnostic ID; verify its native case instead"
                )
            observed: set[str] = set()
            for path in iter_evidence_paths(args.paths):
                observed.update(finding_ids_from_evidence(path))
            present = finding_id in observed
            results.append(
                {
                    "label": "student-evidence",
                    "status": "FAIL" if present else "PASS",
                    "finding_id": finding_id,
                    "observed_finding_ids": sorted(observed),
                    "reason": (
                        "target finding is still present"
                        if present
                        else "target finding is absent"
                    ),
                }
            )
        else:
            raise LessonCatalogError(
                "lesson verification requires one or more report paths; "
                f"run `{acceptance['repair_command']}` and pass its JSON report"
            )
        receipt = _receipt(catalog, "student-verify", results, 0)
        receipt["target"] = args.finding_id
        receipt["lesson_id"] = lesson.lesson_id
        if finding_id is not None:
            receipt["finding_id"] = finding_id
        receipt["acceptance"] = acceptance
        _write_json(output / "receipt.json", receipt)
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    print(f"p101 lesson verification: {receipt['summary']['result']}")
    print(f"Receipt: {output / 'receipt.json'}")
    return 0 if receipt["summary"]["result"] == "PASS" else 1


def command_verify_all(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        output = _output_directory(args.output, "p101-lessons-verify.")
        finding_ids = sorted(catalog.by_finding_id)
        pairs = _write_protocol_fixtures(catalog, output, finding_ids)
        results: list[dict[str, Any]] = []
        jobs = max(1, int(getattr(args, "jobs", 1)))
        if args.quick or args.full:
            profiles = [
                profile
                for profile in catalog.profiles
                if args.full or profile.quick
            ]
            native_results = _run_profiles(catalog, profiles, output, jobs)
            results.extend(native_results)
            _write_unit_test_evidence(
                catalog,
                profiles,
                native_results[: len(profiles)],
                output,
            )
        mode = "full" if args.full else ("quick" if args.quick else "structural")
        receipt = _receipt(catalog, mode, results, len(pairs))
        receipt["jobs"] = jobs
        _write_json(output / "receipt.json", receipt)
        coverage = coverage_document(catalog, [output])
        _write_json(output / "coverage.json", coverage)
        (output / "coverage.md").write_text(
            coverage_markdown(coverage), encoding="utf-8"
        )
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    print(
        f"p101 lesson acceptance: {len(pairs)} protocol pairs, "
        f"{receipt['summary']['native_passed']} native passed, "
        f"{receipt['summary']['native_failed']} native failed"
    )
    print(f"Output: {output}")
    print(f"Coverage: {output / 'coverage.md'}")
    _print_native_failures(results, output)
    return 0 if receipt["summary"]["result"] == "PASS" else 1


def _receipt_paths(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file() and path.name == "receipt.json":
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("receipt.json"))


def progress_document(catalog: Catalog, paths: Iterable[Path]) -> dict[str, Any]:
    completed_lessons: set[str] = set()
    completed_findings: set[str] = set()
    stale_receipts: list[str] = []
    invalid_receipts: list[str] = []
    digest = catalog_digest(catalog)
    receipt_count = 0
    for path in _receipt_paths(paths):
        receipt_count += 1
        try:
            receipt = _read_object(path)
        except LessonCatalogError:
            invalid_receipts.append(str(path))
            continue
        if receipt.get("schema") != RECEIPT_SCHEMA:
            continue
        if receipt.get("catalog_sha256") != digest:
            stale_receipts.append(str(path))
            continue
        summary = receipt.get("summary")
        if (
            receipt.get("mode") != "student-verify"
            or not isinstance(summary, dict)
            or summary.get("result") != "PASS"
        ):
            continue
        acceptance = receipt.get("acceptance")
        finding_id = receipt.get("finding_id")
        lesson_id = receipt.get("lesson_id")
        if not isinstance(lesson_id, str) and isinstance(acceptance, dict):
            lesson_id = acceptance.get("lesson_id")
        if isinstance(lesson_id, str) and lesson_id in catalog.by_lesson_id:
            completed_lessons.add(lesson_id)
        if isinstance(finding_id, str):
            completed_findings.add(finding_id)

    lesson_by_id = catalog.by_lesson_id
    unresolved = sorted(set(lesson_by_id) - completed_lessons)
    return {
        "schema": "p101-lesson-progress-v1",
        "catalog_sha256": digest,
        "summary": {
            "lessons_completed": len(completed_lessons),
            "lessons_verified": len(completed_lessons),
            "lessons_total": len(catalog.lessons),
            "findings_resolved": len(completed_findings),
            "receipts_read": receipt_count,
            "stale_receipts": len(stale_receipts),
            "invalid_receipts": len(invalid_receipts),
        },
        "completed_lesson_ids": sorted(completed_lessons),
        "completed_finding_ids": sorted(completed_findings),
        "unresolved_lesson_ids": unresolved,
        "stale_receipt_paths": stale_receipts,
        "invalid_receipt_paths": invalid_receipts,
        "lessons": {
            lesson_id: {
                "title": lesson_by_id[lesson_id].title,
                "url": lesson_by_id[lesson_id].url,
            }
            for lesson_id in sorted(lesson_by_id)
        },
    }


def progress_markdown(document: dict[str, Any]) -> str:
    summary = document["summary"]
    lessons = document["lessons"]
    lines = [
        "# p101 lesson progress",
        "",
        f"- Progress: {summary['lessons_completed']}/{summary['lessons_total']} lessons completed",
        f"- Repairs verified: {summary['lessons_verified']}",
        f"- Diagnostic repairs verified: {summary['findings_resolved']}",
        f"- Receipts read: {summary['receipts_read']}",
        f"- Stale receipts: {summary['stale_receipts']}",
        f"- Invalid receipts: {summary['invalid_receipts']}",
        "",
        "## Unresolved lesson groups",
        "",
    ]
    unresolved = document["unresolved_lesson_ids"]
    if not unresolved:
        lines.append("No unresolved lesson groups remain.")
    else:
        for lesson_id in unresolved:
            lesson = lessons[lesson_id]
            lines.append(f"- [{lesson_id}: {lesson['title']}]({lesson['url']})")
    lines.extend(["", "## Verified repairs", ""])
    completed = document["completed_lesson_ids"]
    if not completed:
        lines.append("No verified lesson receipts yet.")
    else:
        for lesson_id in completed:
            lesson = lessons[lesson_id]
            lines.append(f"- `{lesson_id}` — {lesson['title']}")
    return "\n".join(lines) + "\n"


def command_progress(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        document = progress_document(catalog, args.paths)
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    text = (
        json.dumps(document, indent=2, sort_keys=True) + "\n"
        if args.json
        else progress_markdown(document)
    )
    if args.output is None:
        print(text, end="")
    else:
        args.output.write_text(text, encoding="utf-8")
        print(args.output)
    return 1 if document["summary"]["invalid_receipts"] else 0


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    default_catalog = Path(__file__).resolve().parents[2] / "playgrounds" / "lessons" / "manifest.json"
    parser = argparse.ArgumentParser(
        prog="p101_lessons.py",
        description="Resolve diagnostics to lessons and verify catalog completeness.",
    )
    parser.add_argument("--catalog", type=Path, default=default_catalog)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="validate lessons and emitted diagnostic coverage")
    check.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    check.set_defaults(function=command_check)
    show = subparsers.add_parser("show", help="show one finding or lesson")
    show.add_argument("finding_id", metavar="FINDING_OR_LESSON")
    show.set_defaults(function=command_show)
    listing = subparsers.add_parser("list", help="list diagnostic-to-lesson mappings")
    listing.set_defaults(function=command_list)
    guide = subparsers.add_parser("guide", help="show lessons for findings in report JSON")
    guide.add_argument("--markdown", action="store_true", help="write a linked Markdown guide")
    guide.add_argument("paths", nargs="+", type=Path)
    guide.set_defaults(function=command_guide)
    run = subparsers.add_parser(
        "run",
        help="materialize one lesson exercise and run its native broken evidence",
    )
    run.add_argument("finding_id", metavar="FINDING_OR_LESSON")
    run.add_argument("-o", "--output", type=Path)
    run.add_argument(
        "--protocol-only",
        action="store_true",
        help="write the canonical broken/repaired report pair without running a tool",
    )
    run.set_defaults(function=command_run)
    verify_one = subparsers.add_parser(
        "verify-one",
        help="verify that one finding is absent from student evidence",
    )
    verify_one.add_argument("finding_id", metavar="FINDING_OR_LESSON")
    verify_one.add_argument("paths", nargs="*", type=Path)
    verify_one.add_argument("-o", "--output", type=Path)
    verify_one.set_defaults(function=command_verify_one)
    verify = subparsers.add_parser(
        "verify",
        help="verify executable acceptance evidence for the complete lesson catalog",
    )
    modes = verify.add_mutually_exclusive_group()
    modes.add_argument(
        "--quick",
        action="store_true",
        help="run representative native evidence in addition to every protocol pair",
    )
    modes.add_argument(
        "--full",
        action="store_true",
        help="run every owning-tool suite",
    )
    verify.add_argument("-o", "--output", type=Path)
    verify.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=min(4, max(1, os.cpu_count() or 1)),
        help="maximum parallel native acceptance checks (default: up to 4)",
    )
    verify.set_defaults(function=command_verify_all)
    coverage = subparsers.add_parser(
        "coverage",
        help="write the diagnostic, lesson, evidence, repair, and platform matrix",
    )
    coverage.add_argument("-j", "--json", action="store_true")
    coverage.add_argument("-o", "--output", type=Path)
    coverage.add_argument(
        "--receipts",
        action="append",
        type=Path,
        default=[],
        help="full acceptance receipt file or directory; may be repeated",
    )
    coverage.set_defaults(function=command_coverage)
    progress = subparsers.add_parser(
        "progress",
        help="summarize verified diagnostic-repair receipts",
    )
    progress.add_argument("paths", nargs="+", type=Path)
    progress.add_argument("-j", "--json", action="store_true")
    progress.add_argument("-o", "--output", type=Path)
    progress.set_defaults(function=command_progress)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    return int(args.function(args))


if __name__ == "__main__":  # pragma: no cover - exercised through the p101 dispatcher
    raise SystemExit(main())
