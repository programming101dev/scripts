#!/usr/bin/env python3
"""Render a p101 capture/analysis directory as one self-contained HTML file."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a self-contained HTML summary for a p101 runtime directory.")
    parser.add_argument("report_dir", type=Path, help="p101 capture or analysis directory")
    parser.add_argument("-o", "--output", type=Path, help="HTML output path; default: <report-dir>/index.html")
    return parser.parse_args(argv)


def read_text(path: Path, limit: int = 20000) -> str:
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


def finding_rows(data: dict[str, Any]) -> str:
    findings = data.get("findings")
    if not isinstance(findings, list) or not findings:
        return "<p>No correlated runtime findings.</p>"

    rows = [
        "<table>",
        "<thead><tr><th>ID</th><th>Kind</th><th>Resource</th><th>Site</th></tr></thead>",
        "<tbody>",
    ]
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        site_value = finding.get("location", finding.get("site"))
        site = site_value if isinstance(site_value, dict) else {}
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        if "fd" in finding or "fd" in evidence:
            resource = f"fd {finding.get('fd', evidence.get('fd'))}"
        elif "ptr" in finding or "ptr" in evidence:
            resource = f"ptr {finding.get('ptr', evidence.get('ptr'))}"
        else:
            resource = str(evidence.get("identity", evidence.get("node", "-")))
        location = f"{site.get('file', '?')}:{site.get('line', '?')} in {site.get('function', '?')}"
        kind = finding.get("kind", finding.get("policy", "?"))
        rows.append(
            "<tr>"
            f"<td><code>{esc(finding.get('id', '?'))}</code></td>"
            f"<td>{esc(kind)}</td>"
            f"<td>{esc(resource)}</td>"
            f"<td><code>{esc(location)}</code></td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def render(report_dir: Path) -> str:
    summary = read_text(report_dir / "summary.md") or read_text(report_dir / "summary.txt")
    manifest = read_text(report_dir / "manifest.txt")
    stdout_text = read_text(report_dir / "stdout.txt")
    stderr_text = read_text(report_dir / "stderr.txt")
    resource_report = read_text(report_dir / "resource-report.txt")
    trace_summary = read_text(report_dir / "trace-summary.txt")
    correlated = read_json(report_dir / "correlated-report.json")
    mermaid = read_text(report_dir / "resource-lifetimes.md")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>p101 report — {esc(report_dir.name)}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.45; margin: 2rem; color: #1f2933; }}
    h1, h2 {{ color: #102a43; }}
    code, pre {{ background: #f0f4f8; border-radius: 0.35rem; }}
    pre {{ overflow: auto; padding: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #bcccdc; padding: 0.45rem 0.6rem; text-align: left; vertical-align: top; }}
    th {{ background: #d9e2ec; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr)); gap: 1rem; }}
    .card {{ border: 1px solid #d9e2ec; border-radius: 0.7rem; padding: 1rem; background: #fff; }}
  </style>
</head>
<body>
  <h1>p101 report: {esc(report_dir.name)}</h1>
  <div class="grid">
    <section class="card"><h2>Summary</h2><pre>{esc(summary)}</pre></section>
    <section class="card"><h2>Manifest</h2><pre>{esc(manifest)}</pre></section>
  </div>
  <h2>Findings</h2>
  {finding_rows(correlated)}
  <div class="grid">
    <section class="card"><h2>Resource tracker</h2><pre>{esc(resource_report)}</pre></section>
    <section class="card"><h2>Trace summary</h2><pre>{esc(trace_summary)}</pre></section>
  </div>
  <h2>Program output</h2>
  <div class="grid">
    <section class="card"><h3>stdout</h3><pre>{esc(stdout_text)}</pre></section>
    <section class="card"><h3>stderr</h3><pre>{esc(stderr_text)}</pre></section>
  </div>
  <h2>Resource lifetime Mermaid source</h2>
  <pre>{esc(mermaid)}</pre>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    report_dir = args.report_dir.resolve()
    if not report_dir.is_dir():
        print(f"p101-html-report: not a directory: {report_dir}", file=sys.stderr)
        return 2
    output = args.output or (report_dir / "index.html")
    if output.exists() or output.is_symlink():
        print(f"p101-html-report: output already exists: {output}", file=sys.stderr)
        return 2
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render(report_dir), encoding="utf-8")
    except OSError as exc:
        print(f"p101-html-report: could not write {output}: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
