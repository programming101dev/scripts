#!/usr/bin/env python3
"""Resolve p101 diagnostic IDs to checked teaching lessons."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "p101-finding-lesson-catalog-v1"
DIAGNOSTIC_PATTERN = re.compile(r"\bP101-[A-Z][A-Z0-9_-]*-[0-9]{3}\b")


class LessonCatalogError(ValueError):
    """The lesson catalog cannot safely be used."""


@dataclass(frozen=True)
class Lesson:
    lesson_id: str
    title: str
    path: str
    url: str
    track: str
    prerequisites: tuple[str, ...]
    finding_ids: tuple[str, ...]
    verification: str
    order: int

    def finding_json(self) -> dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "url": self.url,
            "track": self.track,
            "prerequisites": list(self.prerequisites),
            "verification": self.verification,
        }


@dataclass(frozen=True)
class Catalog:
    path: Path
    lessons: tuple[Lesson, ...]
    ignored_diagnostic_ids: frozenset[str]

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
    required = ("lesson_id", "title", "path", "track", "prerequisites", "finding_ids", "verification")
    missing = [field for field in required if field not in raw]
    if missing:
        raise LessonCatalogError(f"{source}: missing lesson fields: {', '.join(missing)}")
    scalar = {field: raw[field] for field in ("lesson_id", "title", "path", "track", "verification")}
    if not all(isinstance(value, str) and value for value in scalar.values()):
        raise LessonCatalogError(f"{source}: lesson scalar fields must be non-empty strings")
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
    return Lesson(
        lesson_id=scalar["lesson_id"],
        title=scalar["title"],
        path=relative_path,
        url=url_base + relative_path,
        track=scalar["track"],
        prerequisites=_strings(raw["prerequisites"], "prerequisites", source),
        finding_ids=_strings(raw["finding_ids"], "finding_ids", source),
        verification=scalar["verification"],
        order=order,
    )


def load_catalog(path: Path) -> Catalog:
    path = path.resolve()
    document = _read_object(path)
    if document.get("schema") != SCHEMA:
        raise LessonCatalogError(f"{path}: schema must be {SCHEMA}")
    url_base = document.get("url_base")
    case_glob = document.get("case_glob")
    if not isinstance(url_base, str) or not url_base.endswith("/"):
        raise LessonCatalogError(f"{path}: url_base must be a non-empty URL ending in /")
    if not isinstance(case_glob, str) or not case_glob:
        raise LessonCatalogError(f"{path}: case_glob must be a non-empty string")
    ignored = frozenset(
        _strings(document.get("ignored_diagnostic_ids", []), "ignored_diagnostic_ids", path)
    )
    if document.get("case_prerequisite_mode") != "previous-in-track":
        raise LessonCatalogError(
            f"{path}: case_prerequisite_mode must be previous-in-track"
        )
    manifest_dir = path.parent
    raw_lessons = document.get("lessons")
    if not isinstance(raw_lessons, list):
        raise LessonCatalogError(f"{path}: lessons must be an array")

    lessons: list[Lesson] = []
    for index, raw in enumerate(raw_lessons):
        if not isinstance(raw, dict):
            raise LessonCatalogError(f"{path}: lessons[{index}] must be an object")
        lessons.append(_lesson(raw, manifest_dir, url_base, path, 10000 + index))

    case_paths = sorted(manifest_dir.glob(case_glob))
    if not case_paths:
        raise LessonCatalogError(f"{path}: case_glob matched no expected.json files")
    cases: list[tuple[Path, dict[str, Any]]] = []
    for case_path in case_paths:
        case_path = case_path.resolve()
        cases.append((case_path, _read_object(case_path)))
    cases.sort(
        key=lambda item: (
            int(item[1].get("lab_order", 1000)),
            str(item[1].get("name", "")),
        )
    )
    previous_by_track: dict[str, str] = {}
    for case_path, case in cases:
        lesson_id = case.get("issue_id")
        title = case.get("title")
        name = case.get("name")
        tracks = case.get("tracks")
        if not isinstance(lesson_id, str) or not lesson_id:
            raise LessonCatalogError(f"{case_path}: issue_id must be a non-empty string")
        if not isinstance(title, str) or not title or not isinstance(name, str) or not name:
            raise LessonCatalogError(f"{case_path}: name and title must be non-empty strings")
        if not isinstance(tracks, list) or not tracks or not all(isinstance(item, str) and item for item in tracks):
            raise LessonCatalogError(f"{case_path}: tracks must be a non-empty string array")
        finding_ids = list(_strings(case.get("expected_findings", []), "expected_findings", case_path))
        finding_ids.extend(
            _strings(
                case.get("lesson_finding_ids", []),
                "lesson_finding_ids",
                case_path,
            )
        )
        logic_id = case.get("logic_issue_id", "")
        if logic_id:
            if not isinstance(logic_id, str):
                raise LessonCatalogError(f"{case_path}: logic_issue_id must be a string")
            finding_ids.append(logic_id)
        lesson_path = case_path.parent / "lesson.md"
        if not lesson_path.is_file():
            raise LessonCatalogError(f"{case_path}: missing lesson.md")
        relative = lesson_path.relative_to(manifest_dir.parent).as_posix()
        text = lesson_path.read_text(encoding="utf-8")
        if not text.startswith("# ") or len(text.split()) < 10:
            raise LessonCatalogError(f"{lesson_path}: lesson must have a heading and guidance")
        prerequisites: tuple[str, ...] = ()
        if lesson_id != "P101-LAB-ORIENTATION":
            prerequisites = tuple(
                dict.fromkeys(
                    previous_by_track.get(track, "P101-LAB-ORIENTATION")
                    for track in tracks
                )
            )
        lessons.append(
            Lesson(
                lesson_id=lesson_id,
                title=title,
                path=relative,
                url=url_base + relative,
                track=tracks[0],
                prerequisites=prerequisites,
                finding_ids=tuple(finding_ids),
                verification=f"./lab.sh --case {name} --require-all-fixed",
                order=int(case.get("lab_order", 1000)),
            )
        )
        for track in tracks:
            previous_by_track[track] = lesson_id

    lesson_ids: set[str] = set()
    for lesson in lessons:
        if lesson.lesson_id in lesson_ids:
            raise LessonCatalogError(f"{path}: duplicate lesson_id {lesson.lesson_id}")
        lesson_ids.add(lesson.lesson_id)
        for finding_id in lesson.finding_ids:
            if DIAGNOSTIC_PATTERN.fullmatch(finding_id) is None:
                raise LessonCatalogError(
                    f"{path}: invalid finding ID {finding_id} in {lesson.lesson_id}"
                )
    for lesson in lessons:
        missing = sorted(set(lesson.prerequisites) - lesson_ids)
        if missing:
            raise LessonCatalogError(
                f"{path}: {lesson.lesson_id} has unknown prerequisites: {', '.join(missing)}"
            )
    return Catalog(path, tuple(lessons), ignored)


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
                "primary": lessons[0].finding_json(),
                "related": [lesson.finding_json() for lesson in lessons[1:]],
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
                    "track": lesson.track,
                    "prerequisites": lesson.prerequisites,
                    "finding_ids": lesson.finding_ids,
                    "verification": lesson.verification,
                    "order": lesson.order,
                },
                sort_keys=True,
            ).encode("utf-8")
        )
        digest.update((catalog.path.parent.parent / lesson.path).read_bytes())
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
            if not candidate.is_file() or candidate.suffix not in {"", ".c", ".h", ".py"}:
                continue
            try:
                ids.update(DIAGNOSTIC_PATTERN.findall(candidate.read_text(encoding="utf-8")))
            except (OSError, UnicodeError):
                continue
    scripts = workspace / "scripts"
    for candidate in (
        scripts / "p101_runtime.py",
        scripts / "rules" / "resource-clean.json",
    ):
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
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    lessons = catalog.by_finding_id.get(args.finding_id, ())
    if not lessons:
        print(f"p101 lessons: no lesson for {args.finding_id}", file=sys.stderr)
        return 1
    for index, lesson in enumerate(lessons):
        label = "Primary lesson" if index == 0 else "Related lesson"
        print(f"{label}: {lesson.lesson_id} — {lesson.title}")
        print(f"  {lesson.url}")
        print(f"  track: {lesson.track}")
        print(f"  prerequisites: {', '.join(lesson.prerequisites) or 'none'}")
        print(f"  verify: {lesson.verification}")
    return 0


def command_list(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
    except LessonCatalogError as error:
        print(f"p101 lessons: {error}", file=sys.stderr)
        return 2
    for finding_id, lessons in sorted(catalog.by_finding_id.items()):
        print(f"{finding_id}\t{lessons[0].lesson_id}\t{lessons[0].title}\t{lessons[0].url}")
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
                print(f"- Lesson: [{lessons[0].lesson_id}]({lessons[0].url})")
                print(f"- Track: {lessons[0].track}")
                print(f"- Prerequisites: {', '.join(lessons[0].prerequisites) or 'none'}")
                print(f"- Verify: `{lessons[0].verification}`")
                print()
            else:
                print(f"{finding_id}: {lessons[0].title}")
                print(f"  {lessons[0].url}")
                print(f"  verify: {lessons[0].verification}")
    if unmapped:
        print("Unmapped findings: " + ", ".join(unmapped), file=sys.stderr)
        return 1
    return 0


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    default_catalog = Path(__file__).resolve().parent.parent / "playgrounds" / "lessons" / "manifest.json"
    parser = argparse.ArgumentParser(
        prog="p101 lessons",
        description="Resolve diagnostics to lessons and verify curriculum completeness.",
    )
    parser.add_argument("--catalog", type=Path, default=default_catalog)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="validate lessons and emitted diagnostic coverage")
    check.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parent.parent)
    check.set_defaults(function=command_check)
    show = subparsers.add_parser("show", help="show the lesson for one finding ID")
    show.add_argument("finding_id")
    show.set_defaults(function=command_show)
    listing = subparsers.add_parser("list", help="list diagnostic-to-lesson mappings")
    listing.set_defaults(function=command_list)
    guide = subparsers.add_parser("guide", help="show lessons for findings in report JSON")
    guide.add_argument("--markdown", action="store_true", help="write a linked Markdown guide")
    guide.add_argument("paths", nargs="+", type=Path)
    guide.set_defaults(function=command_guide)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
