#!/usr/bin/env python3
"""Replay p101 analysis over an immutable p101-observe capture."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from p101_receipt import (
    ANALYSIS_FILES,
    CAPTURE_FILES,
    Fingerprint,
    ReceiptError,
    fingerprint_fields,
    fingerprint_file,
    parse_fingerprint_line,
    parse_nonnegative,
)
from p101_lessons import (
    Catalog,
    LessonCatalogError,
    annotate_report,
    catalog_digest,
    load_catalog,
)
from p101_runtime import RuntimeModelError, analyze_model, load_model, write_analysis

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_TROUBLE = 2

MAX_CAPTURE_BYTES = 64 * 1024 * 1024
MAX_CAPTURE_RECORDS = 1_000_000

REQUIRED_CAPTURE_ROLES = {
    "manifest",
    "command",
    "stdout",
    "stderr",
    "resources",
    "calls",
    "summary",
}
COMPLETED_STATUS_ROLES = {
    "command",
    "resource_tracker",
    "resource_tracker_json",
    "concurrency",
    "concurrency_json",
    "trace_tree",
    "trace_summary",
    "report",
    "report_json",
    "report_mermaid",
}

OUTPUT_FILES = ANALYSIS_FILES


CaptureInvalid = ReceiptError


@dataclass(frozen=True)
class Tool:
    role: str
    path: Path
    fingerprint: Fingerprint


@dataclass(frozen=True)
class RunResult:
    role: str
    status: int
    signal: int | None = None


def parse_artifact_line(line: str, line_number: int) -> tuple[str, Fingerprint]:
    try:
        return parse_fingerprint_line(
            line, line_number, "artifact", set(CAPTURE_FILES)
        )
    except ReceiptError as error:
        raise CaptureInvalid(str(error)) from error


def parse_capture_receipt(receipt_path: Path) -> dict[str, Fingerprint]:
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise CaptureInvalid(f"missing regular receipt: {receipt_path}")
    try:
        lines = receipt_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise CaptureInvalid(f"cannot read receipt: {error}") from error
    if not lines or lines[0] != "p101-observe receipt":
        raise CaptureInvalid("receipt header is not 'p101-observe receipt'")

    scalar: dict[str, str] = {}
    artifacts: dict[str, Fingerprint] = {}
    status_roles: set[str] = set()
    for line_number, line in enumerate(lines[1:], 2):
        if line.startswith("artifact="):
            role, expected = parse_artifact_line(line, line_number)
            if role in artifacts:
                raise CaptureInvalid(f"duplicate artifact role: {role}")
            artifacts[role] = expected
        elif line.startswith("status="):
            status_fields: dict[str, str] = {}
            for item in line.split("\t"):
                if "=" not in item:
                    raise CaptureInvalid(f"malformed status on line {line_number}")
                key, value = item.split("=", 1)
                if not key or key in status_fields:
                    raise CaptureInvalid(
                        f"duplicate status field on line {line_number}"
                    )
                status_fields[key] = value
            if "status" not in status_fields or len(status_fields) != 2:
                raise CaptureInvalid(f"malformed status on line {line_number}")
            status_value_keys = set(status_fields) - {"status"}
            if status_value_keys not in ({"exit"}, {"signal"}, {"raw"}):
                raise CaptureInvalid(f"malformed status on line {line_number}")
            status_value_key = next(iter(status_value_keys))
            parse_nonnegative(status_fields[status_value_key], status_value_key)
            status_role = status_fields["status"]
            if status_role not in COMPLETED_STATUS_ROLES:
                raise CaptureInvalid(f"unsupported receipt status: {status_role}")
            if status_role in status_roles:
                raise CaptureInvalid(f"duplicate receipt status: {status_role}")
            status_roles.add(status_role)
        elif "=" in line:
            key, value = line.split("=", 1)
            if key in scalar:
                raise CaptureInvalid(f"duplicate receipt key: {key}")
            scalar[key] = value
        else:
            raise CaptureInvalid(f"malformed receipt line {line_number}")

    required_scalars = {
        "schema": "p101-run-receipt-v1",
        "event_schema": "p101-tool-event-format-v4",
        "event_log_version": "4",
        "ordering": "per-context-sequence",
        "durability": "buffered-until-close",
        "fingerprint": "fnv1a64",
        "fingerprint_security": "change-detection-only",
        "does_not_prove": (
            "complete instrumentation, external truth, global process ordering, "
            "or cryptographic authenticity"
        ),
    }
    for key, expected in required_scalars.items():
        if scalar.get(key) != expected:
            raise CaptureInvalid(
                f"receipt {key} must be {expected!r}, got {scalar.get(key)!r}"
            )
    analysis = scalar.get("analysis")
    if analysis not in {"deferred", "completed"}:
        raise CaptureInvalid("receipt analysis must be 'deferred' or 'completed'")
    if not scalar.get("run_id"):
        raise CaptureInvalid("receipt run_id must not be empty")
    expected_status_roles = (
        {"command"} if analysis == "deferred" else COMPLETED_STATUS_ROLES
    )
    if status_roles != expected_status_roles:
        missing_statuses = sorted(expected_status_roles - status_roles)
        extra_statuses = sorted(status_roles - expected_status_roles)
        detail = []
        if missing_statuses:
            detail.append("missing " + ", ".join(missing_statuses))
        if extra_statuses:
            detail.append("unexpected " + ", ".join(extra_statuses))
        raise CaptureInvalid("receipt statuses are incomplete: " + "; ".join(detail))
    expected_artifact_roles = (
        REQUIRED_CAPTURE_ROLES if analysis == "deferred" else set(CAPTURE_FILES)
    )
    if artifacts.keys() != expected_artifact_roles:
        missing = sorted(expected_artifact_roles - artifacts.keys())
        extra = sorted(artifacts.keys() - expected_artifact_roles)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise CaptureInvalid(
            "receipt artifact fingerprints are incomplete: " + "; ".join(detail)
        )
    return artifacts


def verify_capture(capture_dir: Path) -> dict[str, Fingerprint]:
    receipt_path = capture_dir / "receipt.txt"
    receipt_fingerprint = fingerprint_file(
        receipt_path,
        maximum_bytes=MAX_CAPTURE_BYTES,
        maximum_records=MAX_CAPTURE_RECORDS,
    )
    if receipt_fingerprint.final_newline != 1:
        raise CaptureInvalid("receipt has no final newline")
    expected_artifacts = parse_capture_receipt(receipt_path)
    actual: dict[str, Fingerprint] = {"receipt": receipt_fingerprint}
    for role, expected in expected_artifacts.items():
        path = capture_dir / CAPTURE_FILES[role]
        observed = fingerprint_file(
            path,
            maximum_bytes=MAX_CAPTURE_BYTES,
            maximum_records=MAX_CAPTURE_RECORDS,
        )
        if observed != expected:
            raise CaptureInvalid(f"artifact fingerprint mismatch: {path}")
        actual[role] = observed
    return actual


def snapshot_capture(capture_dir: Path) -> dict[str, Fingerprint]:
    snapshot: dict[str, Fingerprint] = {}
    for role, filename in {"receipt": "receipt.txt", **CAPTURE_FILES}.items():
        path = capture_dir / filename
        if path.exists() or path.is_symlink():
            snapshot[role] = fingerprint_file(
                path,
                maximum_bytes=MAX_CAPTURE_BYTES,
                maximum_records=MAX_CAPTURE_RECORDS,
            )
    return snapshot


def last_build_candidate(repo: Path, executable: str) -> list[Path]:
    marker = repo / ".last-runtime-build-dir"
    if not marker.is_file():
        marker = repo / ".last-build-dir"
    if not marker.is_file():
        return []
    build_text = marker.read_text(encoding="utf-8").strip()
    if not build_text:
        return []
    build_dir = Path(build_text)
    if not build_dir.is_absolute():
        build_dir = repo / build_dir
    return [build_dir / executable]


def resolve_tool(
    role: str,
    override: str | None,
    environment_name: str,
    repo: Path,
    executable: str,
) -> Tool:
    configured = override or os.environ.get(environment_name)
    candidates: list[Path | str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(last_build_candidate(repo, executable))
    candidates.extend(
        [
            repo / "build-clang-22" / executable,
            repo / "build-clang" / executable,
            executable,
        ]
    )
    for candidate in candidates:
        candidate_text = os.fspath(candidate)
        found = (
            candidate_text
            if os.path.sep in candidate_text
            else shutil.which(candidate_text)
        )
        if found is None:
            continue
        path = Path(found).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            path = path.resolve()
            return Tool(
                role=role,
                path=path,
                fingerprint=fingerprint_file(path),
            )
    raise CaptureInvalid(
        f"{executable} not found; build it or set {environment_name}"
    )


def run_tool(
    role: str,
    argv: list[str],
    stdout_path: Path,
    stderr_path: Path,
) -> RunResult:
    with stdout_path.open("wb") as stdout_stream, stderr_path.open(
        "ab"
    ) as stderr_stream:
        try:
            completed = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                check=False,
            )
        except OSError as error:
            stderr_stream.write(
                f"p101 analyze: could not execute {argv[0]}: {error}\n".encode(
                    "utf-8", errors="replace"
                )
            )
            return RunResult(role, EXIT_TROUBLE)
    if completed.returncode < 0:
        return RunResult(role, 128 - completed.returncode, -completed.returncode)
    return RunResult(role, completed.returncode)


def status_word(status: int) -> str:
    if status == EXIT_CLEAN:
        return "CLEAN"
    if status == EXIT_FINDINGS:
        return "FINDINGS"
    return "TROUBLE"


def write_summary(
    path: Path,
    capture_dir: Path,
    verification: str,
    results: Iterable[RunResult],
    overall_status: int,
) -> None:
    rows = list(results)
    text = [
        "# p101 replay analysis",
        "",
        f"Capture: `{capture_dir}`",
        "",
        f"Capture verification: **{verification.upper()}**",
        "",
        "| Analysis | Result | Exit |",
        "| --- | --- | ---: |",
    ]
    for result in rows:
        text.append(
            f"| {result.role} | {status_word(result.status)} | {result.status} |"
        )
    text.extend(
        [
            "",
            f"Overall result: **{status_word(overall_status)}**",
            "",
            "This report is bounded by the captured p101 wrapper events. "
            "It cannot see direct libc calls, third-party internals, or events "
            "that were never emitted.",
            "",
        ]
    )
    path.write_text("\n".join(text), encoding="utf-8")


def write_analysis_receipt(
    path: Path,
    capture_dir: Path,
    verification: str,
    verification_detail: str,
    input_snapshot: dict[str, Fingerprint],
    tools: Iterable[Tool],
    results: Iterable[RunResult],
    output_dir: Path,
    overall_status: int,
    lesson_catalog_path: Path | None,
    lesson_catalog_digest: str | None,
) -> None:
    lines = [
        "p101 analysis receipt",
        "schema=p101-analysis-receipt-v1",
        f"capture_dir_json={json.dumps(os.fspath(capture_dir))}",
        f"capture_verification={verification}",
        f"capture_verification_detail_json={json.dumps(verification_detail)}",
        "fingerprint=fnv1a64",
        "fingerprint_security=change-detection-only",
    ]
    for role in sorted(input_snapshot):
        lines.append(fingerprint_fields("input", role, input_snapshot[role]))
    for tool in tools:
        fingerprint = tool.fingerprint
        lines.append(
            f"tool={tool.role}\tpath_json={json.dumps(os.fspath(tool.path))}"
            f"\tversion=binary-fnv1a64:{fingerprint.fnv1a64:016x}"
            f"\tbytes={fingerprint.bytes}"
        )
    if lesson_catalog_path is not None and lesson_catalog_digest is not None:
        lines.append(
            f"lesson_catalog_path_json={json.dumps(str(lesson_catalog_path))}"
        )
        lines.append(f"lesson_catalog_sha256={lesson_catalog_digest}")
    for result in results:
        if result.signal is None:
            lines.append(f"status={result.role}\texit={result.status}")
        else:
            lines.append(
                f"status={result.role}\texit={result.status}\tsignal={result.signal}"
            )
    for role, filename in OUTPUT_FILES.items():
        artifact_path = output_dir / filename
        if artifact_path.exists():
            lines.append(
                fingerprint_fields("artifact", role, fingerprint_file(artifact_path))
            )
        else:
            lines.append(f"artifact_missing={role}")
    lines.extend(
        [
            f"result={status_word(overall_status).lower()}",
            "does_not_prove=complete instrumentation, external truth, "
            "global process ordering, or cryptographic authenticity",
            "",
        ]
    )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, path)


def overall_result(results: Iterable[RunResult]) -> int:
    statuses = [result.status for result in results]
    if any(status not in {EXIT_CLEAN, EXIT_FINDINGS} for status in statuses):
        return EXIT_TROUBLE
    if any(status == EXIT_FINDINGS for status in statuses):
        return EXIT_FINDINGS
    return EXIT_CLEAN


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="p101 analyze",
        description=(
            "Verify a p101-observe capture, build one shared run model, and "
            "apply the runtime policy modules. The capture is never modified."
        ),
    )
    parser.add_argument("capture_dir", help="p101-observe run directory")
    parser.add_argument(
        "-o",
        "--output",
        help="new analysis directory (default: <capture-dir>.analysis)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "analyze despite a missing, incomplete, or modified capture receipt; "
            "the override is recorded"
        ),
    )
    parser.add_argument(
        "--model-tool",
        help="p101-event-model executable (default: discover workspace/install)",
    )
    parser.add_argument(
        "--lesson-catalog",
        help=(
            "finding-to-lesson manifest; default: P101_LESSON_CATALOG or the "
            "workspace playground catalog when present"
        ),
    )
    return parser.parse_args(argv)


def invocation_path(text: str, invocation_dir: Path) -> Path:
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = invocation_dir / path
    return path.resolve(strict=False)


def resolve_lesson_catalog(
    configured: str | None,
    invocation_dir: Path,
    workspace: Path,
) -> tuple[Catalog | None, Path | None]:
    requested = configured or os.environ.get("P101_LESSON_CATALOG")
    explicit = requested is not None
    path = (
        invocation_path(requested, invocation_dir)
        if requested is not None
        else workspace / "playgrounds" / "lessons" / "manifest.json"
    )
    if not path.is_file():
        if explicit:
            raise LessonCatalogError(f"lesson catalog not found: {path}")
        return None, None
    return load_catalog(path), path


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    script_dir = Path(__file__).resolve().parent
    workspace = script_dir.parent
    invocation_dir = Path(os.environ.get("P101_DISPATCH_CWD", os.getcwd()))
    capture_dir = invocation_path(args.capture_dir, invocation_dir)
    output_dir = (
        invocation_path(args.output, invocation_dir)
        if args.output
        else capture_dir.with_name(capture_dir.name + ".analysis")
    )
    try:
        lesson_catalog, lesson_catalog_path = resolve_lesson_catalog(
            args.lesson_catalog, invocation_dir, workspace
        )
        lesson_digest = (
            catalog_digest(lesson_catalog) if lesson_catalog is not None else None
        )
    except LessonCatalogError as error:
        print(f"p101 analyze: {error}", file=sys.stderr)
        return EXIT_TROUBLE

    if not capture_dir.is_dir():
        print(f"p101 analyze: capture directory not found: {capture_dir}", file=sys.stderr)
        return EXIT_TROUBLE
    if output_dir.exists() or output_dir.is_symlink():
        print(f"p101 analyze: output path already exists: {output_dir}", file=sys.stderr)
        return EXIT_TROUBLE
    if output_dir == capture_dir or capture_dir in output_dir.parents:
        print(
            "p101 analyze: output must be separate from and outside the capture directory",
            file=sys.stderr,
        )
        return EXIT_TROUBLE

    verification = "verified"
    verification_detail = "capture receipt and artifact fingerprints matched"
    try:
        input_snapshot = verify_capture(capture_dir)
        stability_snapshot = snapshot_capture(capture_dir)
        for role, expected in input_snapshot.items():
            if stability_snapshot.get(role) != expected:
                raise CaptureInvalid(
                    "capture changed during initial verification: "
                    + CAPTURE_FILES.get(role, "receipt.txt")
                )
    except CaptureInvalid as error:
        if not args.force:
            print(f"p101 analyze: capture refused: {error}", file=sys.stderr)
            print("p101 analyze: use --force to record and override this check", file=sys.stderr)
            return EXIT_TROUBLE
        verification = "overridden"
        verification_detail = str(error)
        try:
            input_snapshot = snapshot_capture(capture_dir)
            stability_snapshot = input_snapshot
        except CaptureInvalid as snapshot_error:
            print(
                f"p101 analyze: cannot snapshot overridden capture: {snapshot_error}",
                file=sys.stderr,
            )
            return EXIT_TROUBLE

    try:
        tools = [
            resolve_tool(
                "event_model",
                args.model_tool,
                "P101_EVENT_MODEL",
                workspace / "libraries/lib_tool_event",
                "p101-event-model",
            ),
            Tool(
                role="analyze_driver",
                path=Path(__file__).resolve(),
                fingerprint=fingerprint_file(Path(__file__).resolve()),
            ),
        ]
    except CaptureInvalid as error:
        print(f"p101 analyze: {error}", file=sys.stderr)
        return EXIT_TROUBLE

    event_snapshot = tempfile.TemporaryDirectory(prefix="p101-analysis-input.")
    event_snapshot_dir = Path(event_snapshot.name)
    snapshot_paths: dict[str, Path] = {}
    for role in ("resources", "calls"):
        source = capture_dir / CAPTURE_FILES[role]
        destination = event_snapshot_dir / CAPTURE_FILES[role]
        snapshot_paths[role] = destination
        if not source.is_file() or source.is_symlink():
            if not args.force:
                event_snapshot.cleanup()
                print(
                    f"p101 analyze: capture refused: missing regular {role} log",
                    file=sys.stderr,
                )
                return EXIT_TROUBLE
            continue
        try:
            shutil.copyfile(source, destination)
            copied_fingerprint = fingerprint_file(
                destination,
                maximum_bytes=MAX_CAPTURE_BYTES,
                maximum_records=MAX_CAPTURE_RECORDS,
            )
        except (CaptureInvalid, OSError) as error:
            event_snapshot.cleanup()
            print(f"p101 analyze: could not snapshot {role} log: {error}", file=sys.stderr)
            return EXIT_TROUBLE
        expected_fingerprint = input_snapshot.get(role)
        if copied_fingerprint != expected_fingerprint:
            if not args.force:
                event_snapshot.cleanup()
                print(
                    f"p101 analyze: capture refused: {role} log changed "
                    "while the analysis snapshot was made",
                    file=sys.stderr,
                )
                return EXIT_TROUBLE
            verification = "overridden"
            verification_detail = (
                f"{role} log changed while the analysis snapshot was made"
            )
            input_snapshot = dict(input_snapshot)
            input_snapshot[role] = copied_fingerprint

    output_dir.mkdir(parents=True)
    resources = snapshot_paths["resources"]
    calls = snapshot_paths["calls"]
    tool_by_role = {tool.role: tool for tool in tools}
    for role in (
        "resource_tools_stderr",
        "concurrency_tools_stderr",
        "trace_tools_stderr",
        "report_tools_stderr",
        "report_driver_output",
    ):
        (output_dir / OUTPUT_FILES[role]).write_text("", encoding="utf-8")
    model_result = run_tool(
        "event_model",
        [
            os.fspath(tool_by_role["event_model"].path),
            "-r",
            os.fspath(resources),
            "-c",
            os.fspath(calls),
            "-o",
            os.fspath(output_dir / OUTPUT_FILES["run_model"]),
        ],
        output_dir / OUTPUT_FILES["report_driver_output"],
        output_dir / OUTPUT_FILES["report_tools_stderr"],
    )
    results = [model_result]
    if model_result.status == EXIT_CLEAN:
        try:
            runtime_analysis = analyze_model(
                load_model(output_dir / OUTPUT_FILES["run_model"])
            )
            write_analysis(output_dir, runtime_analysis)
            if lesson_catalog is not None:
                for role in ("resource_json", "concurrency_json", "correlated_json"):
                    annotate_report(output_dir / OUTPUT_FILES[role], lesson_catalog)
            results.extend(
                [
                    RunResult("resource_policy", runtime_analysis.resource.status),
                    RunResult(
                        "sync_policy", runtime_analysis.synchronization.status
                    ),
                    RunResult("trace_policy", runtime_analysis.trace.status),
                    RunResult("report_renderer", runtime_analysis.status),
                ]
            )
        except (OSError, RuntimeModelError, ValueError) as error:
            with (output_dir / OUTPUT_FILES["report_tools_stderr"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(f"p101 analyze: runtime analysis failed: {error}\n")
            results.extend(
                [
                    RunResult("resource_policy", EXIT_TROUBLE),
                    RunResult("sync_policy", EXIT_TROUBLE),
                    RunResult("trace_policy", EXIT_TROUBLE),
                    RunResult("report_renderer", EXIT_TROUBLE),
                ]
            )
    else:
        results.extend(
            [
                RunResult("resource_policy", EXIT_TROUBLE),
                RunResult("sync_policy", EXIT_TROUBLE),
                RunResult("trace_policy", EXIT_TROUBLE),
                RunResult("report_renderer", EXIT_TROUBLE),
            ]
        )

    tool_changed = False
    for tool in tools:
        try:
            current_tool_fingerprint = fingerprint_file(tool.path)
        except CaptureInvalid:
            current_tool_fingerprint = None
        if current_tool_fingerprint != tool.fingerprint:
            tool_changed = True
    if tool_changed:
        results.append(RunResult("tool_stability", EXIT_TROUBLE))
    if lesson_catalog_path is not None and lesson_digest is not None:
        try:
            current_lesson_digest = catalog_digest(load_catalog(lesson_catalog_path))
        except (LessonCatalogError, OSError):
            current_lesson_digest = None
        if current_lesson_digest != lesson_digest:
            results.append(RunResult("lesson_catalog_stability", EXIT_TROUBLE))

    try:
        post_snapshot = snapshot_capture(capture_dir)
    except CaptureInvalid:
        post_snapshot = None
    if post_snapshot != stability_snapshot:
        verification = "failed-after-analysis"
        verification_detail = "capture files changed while analysis was running"
        results.append(RunResult("capture_stability", EXIT_TROUBLE))

    status = overall_result(results)
    write_summary(
        output_dir / OUTPUT_FILES["summary"],
        capture_dir,
        verification,
        results,
        status,
    )
    write_analysis_receipt(
        output_dir / "analysis-receipt.txt",
        capture_dir,
        verification,
        verification_detail,
        input_snapshot,
        tools,
        results,
        output_dir,
        status,
        lesson_catalog_path,
        lesson_digest,
    )
    event_snapshot.cleanup()
    print(f"p101 analyze: {status_word(status).lower()}: {output_dir}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
