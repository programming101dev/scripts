#!/usr/bin/env python3
"""Shared bounded fingerprint mechanics for p101 run and analysis receipts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_RECORDS = 1_000_000
FNV1A64_OFFSET = 14_695_981_039_346_656_037
FNV1A64_PRIME = 1_099_511_628_211
FNV1A64_MASK = (1 << 64) - 1

CAPTURE_FILES = {
    "manifest": "manifest.txt",
    "command": "command.txt",
    "stdout": "stdout.txt",
    "stderr": "stderr.txt",
    "resources": "resources.log",
    "calls": "calls.log",
    "resource_report": "resource-report.txt",
    "resource_json": "resource-report.json",
    "resource_tools_stderr": "resource-tools.stderr.txt",
    "concurrency_report": "concurrency-report.txt",
    "concurrency_json": "concurrency-report.json",
    "concurrency_tools_stderr": "concurrency-tools.stderr.txt",
    "trace_tree": "trace-tree.txt",
    "trace_summary": "trace-summary.txt",
    "trace_tools_stderr": "trace-tools.stderr.txt",
    "correlated_report": "correlated-report.txt",
    "correlated_json": "correlated-report.json",
    "resource_lifetimes_graph": "resource-lifetimes.md",
    "run_model": "run-model.json",
    "report_driver_output": "report-driver.stdout.txt",
    "report_tools_stderr": "report-tools.stderr.txt",
    "summary": "summary.txt",
}

ANALYSIS_FILES = {
    "resource_report": "resource-report.txt",
    "resource_json": "resource-report.json",
    "resource_tools_stderr": "resource-tools.stderr.txt",
    "concurrency_report": "concurrency-report.txt",
    "concurrency_json": "concurrency-report.json",
    "concurrency_tools_stderr": "concurrency-tools.stderr.txt",
    "trace_tree": "trace-tree.txt",
    "trace_summary": "trace-summary.txt",
    "trace_tools_stderr": "trace-tools.stderr.txt",
    "correlated_report": "correlated-report.txt",
    "correlated_json": "correlated-report.json",
    "resource_lifetimes_graph": "resource-lifetimes.md",
    "run_model": "run-model.json",
    "report_driver_output": "report-driver.stdout.txt",
    "report_tools_stderr": "report-tools.stderr.txt",
    "summary": "summary.md",
}


class ReceiptError(Exception):
    """A receipt or one of its admitted artifacts is invalid."""


@dataclass(frozen=True)
class Fingerprint:
    bytes: int
    records: int
    final_newline: int
    fnv1a64: int


def fingerprint_file(
    path: Path,
    *,
    maximum_bytes: int | None = None,
    maximum_records: int | None = None,
) -> Fingerprint:
    byte_count = 0
    newline_count = 0
    final_newline = 0
    value = FNV1A64_OFFSET

    if path.is_symlink() or not path.is_file():
        raise ReceiptError(f"not a regular file: {path}")

    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(4096)
                if not block:
                    break
                byte_count += len(block)
                if maximum_bytes is not None and byte_count > maximum_bytes:
                    raise ReceiptError(
                        f"file exceeds {maximum_bytes} bytes: {path}"
                    )
                for byte in block:
                    value ^= byte
                    value = (value * FNV1A64_PRIME) & FNV1A64_MASK
                    if byte == 0x0A:
                        newline_count += 1
                        if (
                            maximum_records is not None
                            and newline_count > maximum_records
                        ):
                            raise ReceiptError(
                                f"file exceeds {maximum_records} records: {path}"
                            )
                final_newline = int(block[-1] == 0x0A)
    except OSError as error:
        raise ReceiptError(f"cannot fingerprint {path}: {error}") from error

    if byte_count > 0 and not final_newline:
        newline_count += 1
        if maximum_records is not None and newline_count > maximum_records:
            raise ReceiptError(f"file exceeds {maximum_records} records: {path}")

    return Fingerprint(byte_count, newline_count, final_newline, value)


def parse_nonnegative(text: str, field: str) -> int:
    if not text.isdigit():
        raise ReceiptError(f"invalid {field}: {text!r}")
    return int(text, 10)


def parse_fingerprint_line(
    line: str, line_number: int, prefix: str, allowed_roles: set[str]
) -> tuple[str, Fingerprint]:
    fields: dict[str, str] = {}
    for item in line.split("\t"):
        if "=" not in item:
            raise ReceiptError(f"malformed receipt line {line_number}")
        key, value = item.split("=", 1)
        if not key or key in fields:
            raise ReceiptError(
                f"duplicate receipt field on line {line_number}"
            )
        fields[key] = value
    if set(fields) != {
        prefix,
        "bytes",
        "records",
        "final_newline",
        "fnv1a64",
    }:
        raise ReceiptError(
            f"unexpected {prefix} fields on line {line_number}"
        )
    role = fields[prefix]
    if role not in allowed_roles:
        raise ReceiptError(f"unsupported {prefix} role: {role}")
    if fields["final_newline"] not in {"0", "1"}:
        raise ReceiptError(f"invalid final_newline on line {line_number}")
    hash_text = fields["fnv1a64"]
    if len(hash_text) != 16:
        raise ReceiptError(f"invalid fnv1a64 on line {line_number}")
    try:
        hash_value = int(hash_text, 16)
    except ValueError as error:
        raise ReceiptError(f"invalid fnv1a64 on line {line_number}") from error
    return role, Fingerprint(
        parse_nonnegative(fields["bytes"], "bytes"),
        parse_nonnegative(fields["records"], "records"),
        int(fields["final_newline"], 10),
        hash_value,
    )


def fingerprint_fields(prefix: str, role: str, value: Fingerprint) -> str:
    return (
        f"{prefix}={role}\tbytes={value.bytes}\trecords={value.records}"
        f"\tfinal_newline={value.final_newline}\tfnv1a64={value.fnv1a64:016x}"
    )
