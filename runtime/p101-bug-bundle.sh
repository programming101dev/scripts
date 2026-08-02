#!/usr/bin/env bash
# p101-bug-bundle.sh — package a p101 report directory for bug reports.

set -euo pipefail
unset CDPATH

usage() {
  cat <<'USAGE'
Usage: p101-bug-bundle.sh [-o <bundle.tar.gz>] <observe-report-dir>

Creates a reproducible bug bundle containing the p101 observe/report artifacts
plus a small host/tool manifest. The source tree is not copied automatically;
students should attach the relevant assignment files separately when requested.
USAGE
}

out_file=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o) out_file="${2:?}"; shift 2 ;;
    *) break ;;
  esac
done

if [ "$#" -ne 1 ]; then
  usage >&2
  exit 2
fi

report_dir="$1"
if [ ! -d "$report_dir" ]; then
  echo "p101-bug-bundle: report directory does not exist: $report_dir" >&2
  exit 2
fi

report_dir="$(cd -- "$report_dir" && pwd)"
if [ -z "$out_file" ]; then
  out_file="${TMPDIR:-/tmp}/$(basename "$report_dir")-bug-bundle.tar.gz"
fi

if [ -e "$out_file" ] || [ -L "$out_file" ]; then
  echo "p101-bug-bundle: output already exists: $out_file" >&2
  exit 2
fi

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-bug-bundle.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM
mkdir -p -- "$work_dir/bundle"

cp -R "$report_dir" "$work_dir/bundle/report"

{
  echo "p101 bug bundle"
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host=$(uname -a)"
  for tool in cc c++ clang clang++ gcc g++ python3; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf '%s=%s\n' "$tool" "$(command -v "$tool")"
      "$tool" --version 2>/dev/null | sed "s/^/${tool}_version=/;q"
    fi
  done
} > "$work_dir/bundle/environment.txt"

mkdir -p -- "$(dirname -- "$out_file")"
tmp_out="$work_dir/bundle.tar.gz"
tar -czf "$tmp_out" -C "$work_dir" bundle
mv "$tmp_out" "$out_file"
echo "$out_file"
