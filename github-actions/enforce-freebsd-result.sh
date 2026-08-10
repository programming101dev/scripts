#!/usr/bin/env bash
# Enforce the FreeBSD VM receipt while keeping the actionable failure visible
# in the GitHub Actions job log.

set -euo pipefail

out_dir="${1:-scripts/ci-output}"
status_file="$out_dir/freebsd-exit-code"
phase_file="$out_dir/freebsd-failed-phase"

if [[ ! -f "$status_file" ]]; then
  printf 'FreeBSD VM did not return an exit-status receipt.\n' >&2
  if [[ -f "$out_dir/github-step-summary.md" ]]; then
    cat "$out_dir/github-step-summary.md" >&2
  fi
  exit 2
fi

status="$(tr -d '[:space:]' < "$status_file")"
if [[ ! "$status" =~ ^[0-9]+$ ]]; then
  printf 'FreeBSD VM returned an invalid exit status: %s\n' "$status" >&2
  exit 2
fi
if [[ "$status" -gt 255 ]]; then
  printf 'FreeBSD VM returned an out-of-range exit status: %s\n' "$status" >&2
  exit 2
fi
if [[ "$status" -eq 0 ]]; then
  exit 0
fi

phase=unknown
if [[ -f "$phase_file" ]]; then
  phase="$(tr -d '[:space:]' < "$phase_file")"
fi

printf 'FreeBSD %s phase failed with exit %s.\n' "$phase" "$status" >&2

if [[ -f "$out_dir/github-step-summary.md" ]]; then
  printf '\n===== FreeBSD failure summary =====\n' >&2
  cat "$out_dir/github-step-summary.md" >&2
fi

case "$phase" in
  clone)
    phase_log="$out_dir/clone.log"
    ;;
  compilers)
    phase_log="$out_dir/compilers.log"
    ;;
  update)
    phase_log="$out_dir/update-all.log"
    ;;
  check|acceptance)
    phase_log="$out_dir/update-all.log"
    ;;
  *)
    phase_log=""
    ;;
esac

if [[ -n "$phase_log" && -f "$phase_log" ]]; then
  printf '\n===== Last 200 lines of %s =====\n' "$(basename "$phase_log")" >&2
  tail -n 200 "$phase_log" >&2
fi

exit "$status"
