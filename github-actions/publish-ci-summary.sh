#!/usr/bin/env bash
# Publish the p101 acceptance receipt where GitHub users can see it without
# downloading the CI evidence artifact.

set -euo pipefail

out_dir="${1:-ci-output}"
platform="${2:-${RUNNER_OS:-unknown}}"
update_outcome="${3:-unknown}"
check_outcome="${4:-unknown}"
step_summary="${GITHUB_STEP_SUMMARY:-}"
local_summary="$out_dir/github-step-summary.md"
graph_summary="$out_dir/summary.md"
matrix_summary="$out_dir/compiler-matrix/summary.md"
matrix_tsv="$out_dir/compiler-matrix/summary.tsv"
failure_limit=240

mkdir -p "$out_dir"

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

escape_annotation_data() {
  local value="$1"
  value="${value//%/%25}"
  value="${value//$'\r'/%0D}"
  value="${value//$'\n'/%0A}"
  printf '%s' "$value"
}

escape_annotation_property() {
  local value
  value="$(escape_annotation_data "$1")"
  value="${value//:/%3A}"
  value="${value//,/%2C}"
  printf '%s' "$value"
}

annotate_error() {
  local title="$1"
  local detail="$2"
  printf '::error title=%s::%s\n' \
    "$(escape_annotation_property "$title")" \
    "$(escape_annotation_data "$detail")"
}

first_diagnostic() {
  local log="$1"
  local diagnostic

  diagnostic="$(awk '
    {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      if(line ~ /:[0-9]+:[0-9]+: (fatal )?(error|warning):/) {
        source_diagnostic = line
      } else if(line ~ /^CMake Error/ ||
                line ~ /clang-tidy failed for:/ ||
                line ~ /cppcheck reported [1-9][0-9]* diagnostics?/ ||
                line ~ /^FAIL([:(]|$)/ ||
                line ~ /Undefined symbols/ ||
                line ~ /linker command failed/) {
        tool_diagnostic = line
      }
    }
    END {
      if(source_diagnostic != "") {
        print source_diagnostic
      } else if(tool_diagnostic != "") {
        print tool_diagnostic
      }
    }
  ' "$log")"
  if [ -n "$diagnostic" ]; then
    printf '%s\n' "$diagnostic"
    return
  fi

  awk '
    {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      if(line != "" && line !~ /^\$ / && line !~ /^# retry /) {
        print line
        exit
      }
    }
  ' "$log"
}

append_failure_log() {
  local title="$1"
  local log="$2"
  local lines

  {
    printf '\n<details>\n<summary>%s</summary>\n\n' "$title"
    printf '```text\n'
    lines="$(wc -l < "$log" | tr -d '[:space:]')"
    if [ "$lines" -le "$failure_limit" ]; then
      sed -n "1,${failure_limit}p" "$log"
    else
      sed -n '1,120p' "$log"
      printf '\n... %d line(s) omitted; the complete log remains in the job console and CI artifact ...\n\n' \
        "$((lines - failure_limit))"
      tail -n 120 "$log"
    fi
    printf '```\n\n</details>\n'
  } >> "$local_summary"
}

freebsd_phase=""
if [ -f "$out_dir/freebsd-failed-phase" ]; then
  freebsd_phase="$(tr -d '[:space:]' < "$out_dir/freebsd-failed-phase")"
fi
if [ -f "$out_dir/freebsd-exit-code" ]; then
  freebsd_status="$(sed \
    -e 's/^[[:space:]]*//' \
    -e 's/[[:space:]]*$//' \
    "$out_dir/freebsd-exit-code")"
  if ! [[ "$freebsd_status" =~ ^[0-9]+$ ]] ||
     [ "$freebsd_status" -gt 255 ]; then
    printf 'Invalid FreeBSD exit-status receipt: %s\n' "$freebsd_status" >&2
    update_outcome="failure"
    check_outcome="failure"
  elif [ "$freebsd_status" -ne 0 ]; then
    case "$freebsd_phase" in
      clone|compilers|update)
        update_outcome="failure"
        check_outcome="not-run"
        ;;
      check|acceptance)
        update_outcome="success"
        check_outcome="failure"
        ;;
      *)
        check_outcome="failure"
        ;;
    esac
  fi
fi

# update-all owns the CMake acceptance target. If it failed after producing a
# governed graph summary, the repository/bootstrap portion succeeded and the
# failure belongs to acceptance rather than to both displayed phases.
if [[ "$update_outcome" =~ ^(failure|cancelled)$ ]] && [ -f "$graph_summary" ]; then
  check_outcome="$update_outcome"
  update_outcome="success"
elif [ "$update_outcome" = "success" ] && [ -f "$graph_summary" ]; then
  check_outcome="success"
fi

{
  printf '# p101 CI result — %s\n\n' "$platform"
  printf '| Phase | Outcome |\n'
  printf '| --- | --- |\n'
  printf '| Repository update/build | %s |\n' "$update_outcome"
  printf '| Governed acceptance graph | %s |\n' "$check_outcome"
  printf '\n'
  if [ -f "$graph_summary" ]; then
    cat "$graph_summary"
  else
    printf 'The governed acceptance graph did not produce a summary. '
    printf 'Inspect the failed update/build step shown in this job.\n'
  fi
  if [ -f "$matrix_summary" ]; then
    printf '\n'
    cat "$matrix_summary"
  fi
} > "$local_summary"

reported_graph_failure=0
if [ -f "$graph_summary" ]; then
  while IFS= read -r row; do
    title="$(trim "$(printf '%s\n' "$row" | cut -d '|' -f 3)")"
    log_relative="$(printf '%s\n' "$row" \
      | sed -n 's#.*\[log\](\./\(logs/[^)]*\.log\)).*#\1#p')"
    log="$out_dir/$log_relative"
    detail="The governed check failed."
    if [ -n "$log_relative" ] && [ -f "$log" ]; then
      diagnostic="$(first_diagnostic "$log")"
      if [ -n "$diagnostic" ]; then
        detail="$diagnostic"
      fi
      append_failure_log "$title" "$log"
    fi
    annotate_error "p101: $title" "$detail"
    reported_graph_failure=1
  done < <(grep -E '^\| (TOOL-ERROR|BLOCKED) \|' "$graph_summary" || true)
fi

case "$update_outcome" in
  failure|cancelled)
    update_log="$out_dir/update-all.log"
    update_title="Repository update/build failure"
    case "$freebsd_phase" in
      clone)
        update_log="$out_dir/clone.log"
        update_title="Repository clone failure"
        ;;
      compilers)
        update_log="$out_dir/compilers.log"
        update_title="Compiler discovery failure"
        ;;
    esac
    reported_matrix_failure=0
    if [ -f "$matrix_tsv" ]; then
      while IFS=$'\t' read -r _pair_id c_compiler cxx_compiler result exit_status _elapsed_seconds pair_log; do
        [ "$result" = FAIL ] || continue
        reported_matrix_failure=1
        pair_detail="Compiler pair failed with exit $exit_status."
        if [ -f "$pair_log" ]; then
          diagnostic="$(first_diagnostic "$pair_log")"
          if [ -n "$diagnostic" ]; then
            pair_detail="$diagnostic"
          fi
          append_failure_log "Compiler pair: $c_compiler : $cxx_compiler" "$pair_log"
        fi
        annotate_error "p101: $c_compiler : $cxx_compiler" "$pair_detail"
      done < <(tail -n +2 "$matrix_tsv")
    fi
    if [ "$reported_matrix_failure" -eq 0 ]; then
      update_detail="The repository update or build phase failed. The complete diagnostic is in the failed GitHub Actions step."
      if [ -f "$update_log" ]; then
        diagnostic="$(first_diagnostic "$update_log")"
        if [ -n "$diagnostic" ]; then
          update_detail="$diagnostic"
        fi
        append_failure_log "$update_title" "$update_log"
      fi
      annotate_error "p101: repository update/build" "$update_detail"
    fi
    ;;
esac

case "$check_outcome" in
  failure|cancelled)
    if [ "$reported_graph_failure" -eq 0 ]; then
      check_log="$out_dir/acceptance.log"
      if [ ! -f "$check_log" ]; then
        check_log="$out_dir/check-after-update-all.log"
      fi
      check_detail="The acceptance phase failed before it produced a governed failure receipt. Inspect the failed GitHub Actions step."
      if [ -f "$check_log" ]; then
        diagnostic="$(first_diagnostic "$check_log")"
        if [ -n "$diagnostic" ]; then
          check_detail="$diagnostic"
        fi
        append_failure_log "Governed acceptance failure" "$check_log"
      fi
      annotate_error "p101: governed acceptance graph" "$check_detail"
    fi
    ;;
esac

if [ -n "$step_summary" ]; then
  cat "$local_summary" >> "$step_summary"
fi

printf 'GitHub Actions summary: %s\n' "$local_summary"
