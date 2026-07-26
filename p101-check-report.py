#!/usr/bin/env python3
"""Render a p101 check directory as one student-facing HTML file."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a self-contained HTML summary for a p101 check directory.")
    parser.add_argument("check_dir", type=Path, help="p101-check report directory")
    parser.add_argument("-o", "--output", type=Path, help="HTML output path; default: <check-dir>/index.html")
    return parser.parse_args(argv)


def read_text(path: Path, limit: int = 24000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n...[truncated]...\n"
    return text


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def rel_link(root: Path, path: Path, label: str | None = None) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    text = label or str(rel)
    return f'<a href="{esc(rel)}">{esc(text)}</a>'


def status_class(status: object) -> str:
    try:
        value = int(status)
    except (TypeError, ValueError):
        return "unknown"
    if value == 0:
        return "pass"
    if value == 1:
        return "findings"
    return "trouble"


def status_word(status: object) -> str:
    try:
        value = int(status)
    except (TypeError, ValueError):
        return "unknown"
    if value == 0:
        return "clean"
    if value == 1:
        return "findings"
    return "trouble"


def doctor_status_table(doctor: dict[str, Any]) -> str:
    statuses = doctor.get("statuses")
    if not isinstance(statuses, dict) or not statuses:
        return "<p>No doctor status JSON was available.</p>"

    rows = [
        "<table>",
        "<thead><tr><th>Check</th><th>Status</th></tr></thead>",
        "<tbody>",
    ]
    for name, value in statuses.items():
        rows.append(f'<tr><td><code>{esc(name)}</code></td><td class="{status_class(value)}">{esc(status_word(value))} ({esc(value)})</td></tr>')
    rows.append("</tbody></table>")
    return "\n".join(rows)


def finding_rows(data: dict[str, Any]) -> str:
    findings = data.get("findings")
    if not isinstance(findings, list) or not findings:
        return "<p>No correlated resource findings in the observed run.</p>"

    rows = [
        "<table>",
        "<thead><tr><th>ID</th><th>Kind</th><th>Resource</th><th>Site</th></tr></thead>",
        "<tbody>",
    ]
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        site = finding.get("site") if isinstance(finding.get("site"), dict) else {}
        if "fd" in finding:
            resource = f"fd {finding.get('fd')}"
        else:
            resource = f"ptr {finding.get('ptr', '-')}"
        location = f"{site.get('file', '?')}:{site.get('line', '?')} in {site.get('function', '?')}"
        rows.append(
            "<tr>"
            f"<td><code>{esc(finding.get('id', '?'))}</code></td>"
            f"<td>{esc(finding.get('kind', '?'))}</td>"
            f"<td>{esc(resource)}</td>"
            f"<td><code>{esc(location)}</code></td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def artifact_list(root: Path) -> str:
    links: list[tuple[Path, str]] = [
        (root / "summary.md", "check summary"),
        (root / "logs" / "quality-check.log", "quality check log"),
        (root / "logs" / "coverage.log", "coverage log"),
        (root / "doctor" / "summary.md", "doctor summary"),
        (root / "doctor" / "module-map.md", "module map"),
        (root / "doctor" / "observe" / "index.html", "observed-run HTML"),
        (root / "doctor" / "observe" / "correlated-report.txt", "correlated resource report"),
        (root / "doctor" / "observe" / "resource-lifetimes.md", "resource lifetime Mermaid"),
        (root / "doctor" / "fault-walk", "fault walk cases"),
        (root / "bug-bundle.tar.gz", "bug bundle"),
    ]
    items = []
    for path, label in links:
        if path.exists():
            items.append(f"<li>{rel_link(root, path, label)}</li>")
    if not items:
        return "<p>No artifacts found.</p>"
    return "<ul>\n" + "\n".join(items) + "\n</ul>"


def render(check_dir: Path) -> str:
    doctor_dir = check_dir / "doctor"
    observe_dir = doctor_dir / "observe"

    summary = read_text(check_dir / "summary.md")
    doctor_summary = read_text(doctor_dir / "summary.md")
    module_map = read_text(doctor_dir / "module-map.md")
    quality_log = read_text(check_dir / "logs" / "quality-check.log", 16000)
    coverage_log = read_text(check_dir / "logs" / "coverage.log", 16000)
    observe_summary = read_text(observe_dir / "summary.txt")
    trace_summary = read_text(observe_dir / "trace-summary.txt")
    lifetimes = read_text(observe_dir / "resource-lifetimes.md")
    doctor_json = read_json(doctor_dir / "doctor.json")
    correlated = read_json(observe_dir / "correlated-report.json")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>p101 check — {esc(check_dir.name)}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.45; margin: 2rem; color: #1f2933; }}
    h1, h2, h3 {{ color: #102a43; }}
    code, pre {{ background: #f0f4f8; border-radius: 0.35rem; }}
    pre {{ overflow: auto; padding: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #bcccdc; padding: 0.45rem 0.6rem; text-align: left; vertical-align: top; }}
    th {{ background: #d9e2ec; }}
    a {{ color: #0b63ce; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr)); gap: 1rem; }}
    .card {{ border: 1px solid #d9e2ec; border-radius: 0.7rem; padding: 1rem; background: #fff; }}
    .pass {{ color: #166534; font-weight: 700; }}
    .findings {{ color: #92400e; font-weight: 700; }}
    .trouble, .unknown {{ color: #991b1b; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>p101 check: {esc(check_dir.name)}</h1>
  <p>This is the one-page course feedback report: quality receipts, wrapper use, module shape, observed resources, call tracing, and fault-injected error paths.</p>

  <section class="card">
    <h2>Outcome</h2>
    <pre>{esc(summary)}</pre>
  </section>

  <section>
    <h2>Doctor status</h2>
    {doctor_status_table(doctor_json)}
  </section>

  <section>
    <h2>Correlated findings</h2>
    {finding_rows(correlated)}
  </section>

  <section>
    <h2>Artifacts</h2>
    {artifact_list(check_dir)}
  </section>

  <div class="grid">
    <section class="card"><h2>Quality check</h2><pre>{esc(quality_log or "Skipped or unavailable.")}</pre></section>
    <section class="card"><h2>Coverage</h2><pre>{esc(coverage_log or "Skipped or unavailable.")}</pre></section>
  </div>

  <section class="card">
    <h2>Doctor summary</h2>
    <pre>{esc(doctor_summary)}</pre>
  </section>

  <div class="grid">
    <section class="card"><h2>Observed run</h2><pre>{esc(observe_summary)}</pre></section>
    <section class="card"><h2>Trace summary</h2><pre>{esc(trace_summary)}</pre></section>
  </div>

  <section class="card">
    <h2>Module map</h2>
    <pre>{esc(module_map)}</pre>
  </section>

  <section class="card">
    <h2>Resource lifetime Mermaid source</h2>
    <pre>{esc(lifetimes)}</pre>
  </section>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    check_dir = args.check_dir.resolve()
    if not check_dir.is_dir():
        print(f"p101-check-report: not a directory: {check_dir}", file=sys.stderr)
        return 2
    output = args.output or (check_dir / "index.html")
    output.write_text(render(check_dir), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
