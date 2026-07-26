#!/usr/bin/env bash
# p101-check.sh — one-command student feedback workflow.

set -u
set -o pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
invoke_cwd="${P101_DISPATCH_CWD:-$(pwd)}"

out_dir=""
source_path="."
project_dir="$invoke_cwd"
fault_count=16
skip_wrapper=0
skip_quality=0
run_coverage=0
skip_html=0
skip_bundle=0

usage() {
  cat <<'USAGE'
Usage: p101 check [options] [<source-path>] -- <command> [args...]
       p101 check [options] <command> [args...]

Run the golden-path p101 teaching workflow:
  1. project quality gate via ./check.sh when present;
  2. p101-doctor, including wrapper audit, module map, observation, resource
     tracking, call tracing, correlated report, and error-path walking;
  3. optional coverage receipt;
  4. one top-level HTML report and bug bundle.

Options:
  -o <dir>          Output directory. Default: ./p101-check-<pid>
  -s <path>         Source path passed to p101-doctor. Default: .
  -p <dir>          Project directory for check.sh/coverage-report.sh. Default: caller cwd.
  -n <count>        Fault-injection cases for p101-error-path-walk. Default: 16
  -x                Skip static wrapper audit inside p101-doctor.
  --skip-quality    Do not run ./check.sh before doctor.
  --coverage        Also run ./coverage-report.sh --no-open when available.
  --skip-html       Do not render HTML reports.
  --skip-bundle     Do not create bug-bundle.tar.gz.
  -h, --help        Show this help.

Tool locations may be overridden with the usual P101_* environment variables.

Examples:
  p101 check -s src -- ./build-clang/my-program input.txt
  p101 check ./src -- ./build-clang/my-program
  p101 check --skip-quality -n 32 -- ./my-program
USAGE
}

find_tool() {
  env_name="$1"
  shift

  eval "configured=\${$env_name:-}"
  if [ -n "$configured" ]; then
    if [ -x "$configured" ] || command -v "$configured" >/dev/null 2>&1; then
      printf '%s\n' "$configured"
      return 0
    fi
  fi

  for candidate in "$@"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  return 1
}

quote_command() {
  for arg in "$@"; do
    printf '%q ' "$arg"
  done
  printf '\n'
}

relpath() {
  path="$1"
  case "$path" in
    "$out_dir"/*) printf '%s\n' "${path#"$out_dir"/}" ;;
    *) printf '%s\n' "$path" ;;
  esac
}

run_logged() {
  title="$1"
  log="$2"
  shift 2

  printf '==> %s\n' "$title"
  {
    printf '$'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n\n'
  } > "$log"

  set +e
  "$@" >> "$log" 2>&1
  rc=$?
  set +e
  printf '    exit %s\n' "$rc"
  return "$rc"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -s) source_path="${2:?}"; shift 2 ;;
    -p) project_dir="${2:?}"; shift 2 ;;
    -n) fault_count="${2:?}"; shift 2 ;;
    -x) skip_wrapper=1; shift ;;
    --skip-quality) skip_quality=1; shift ;;
    --coverage) run_coverage=1; shift ;;
    --skip-html) skip_html=1; shift ;;
    --skip-bundle) skip_bundle=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if [ "$#" -gt 1 ] && [ -d "$1" ] && [ "$2" = "--" ]; then
  source_path="$1"
  shift 2
fi

if [ "$#" -eq 0 ]; then
  usage >&2
  exit 2
fi

project_dir="$(cd "$project_dir" && pwd)" || {
  echo "p101 check: project directory does not exist: $project_dir" >&2
  exit 2
}
if [ -z "$out_dir" ]; then
  out_dir="$project_dir/p101-check-$$"
fi
case "$out_dir" in
  /*) ;;
  *) out_dir="$project_dir/$out_dir" ;;
esac

if [ -e "$out_dir" ]; then
  echo "p101 check: output path already exists: $out_dir" >&2
  exit 2
fi

mkdir -p "$out_dir/logs"
out_dir="$(cd "$out_dir" && pwd)"
log_dir="$out_dir/logs"
summary="$out_dir/summary.md"

doctor_tool="$(find_tool P101_DOCTOR "$script_dir/../programs/p101-doctor/build-clang-22/p101-doctor" "$script_dir/../programs/p101-doctor/build-clang/p101-doctor" p101-doctor)" || { echo "p101 check: p101-doctor not found" >&2; exit 2; }
wrapper_tool="$(find_tool P101_WRAPPER_AUDIT "$script_dir/../programs/p101-wrapper-audit/p101-wrapper-audit" p101-wrapper-audit)" || { echo "p101 check: p101-wrapper-audit not found" >&2; exit 2; }
module_tool="$(find_tool P101_MODULE_MAP "$script_dir/../programs/p101-module-map/build-clang-22/p101-module-map" "$script_dir/../programs/p101-module-map/build-clang/p101-module-map" p101-module-map)" || { echo "p101 check: p101-module-map not found" >&2; exit 2; }
observe_tool="$(find_tool P101_OBSERVE "$script_dir/../programs/p101-observe/build-clang-22/p101-observe" "$script_dir/../programs/p101-observe/build-clang/p101-observe" p101-observe)" || { echo "p101 check: p101-observe not found" >&2; exit 2; }
walk_tool="$(find_tool P101_ERROR_PATH_WALK "$script_dir/../programs/p101-error-path-walk/build-clang-22/p101-error-path-walk" "$script_dir/../programs/p101-error-path-walk/build-clang/p101-error-path-walk" p101-error-path-walk)" || { echo "p101 check: p101-error-path-walk not found" >&2; exit 2; }
tracker_tool="$(find_tool P101_RESOURCE_TRACKER "$script_dir/../programs/p101-resource-tracker/build-clang-22/p101-resource-tracker" "$script_dir/../programs/p101-resource-tracker/build-clang/p101-resource-tracker" p101-resource-tracker)" || { echo "p101 check: p101-resource-tracker not found" >&2; exit 2; }
trace_tool="$(find_tool P101_TRACE "$script_dir/../programs/p101-trace/build-clang-22/p101-trace" "$script_dir/../programs/p101-trace/build-clang/p101-trace" p101-trace)" || { echo "p101 check: p101-trace not found" >&2; exit 2; }
report_tool="$(find_tool P101_REPORT "$script_dir/../programs/p101-report/build-clang-22/p101-report" "$script_dir/../programs/p101-report/build-clang/p101-report" p101-report)" || { echo "p101 check: p101-report not found" >&2; exit 2; }

quality_status=0
quality_state="SKIP"
coverage_status=0
coverage_state="SKIP"
doctor_status=2
html_status=0
html_state="PASS"
bundle_status=0
bundle_state="PASS"

printf 'p101 check output: %s\n' "$out_dir"

if [ "$skip_quality" -eq 0 ] && [ -x "$project_dir/check.sh" ]; then
  (cd "$project_dir" && run_logged "project quality gate" "$log_dir/quality-check.log" ./check.sh)
  quality_status=$?
  if [ "$quality_status" -eq 0 ]; then quality_state="PASS"; else quality_state="FAIL"; fi
elif [ "$skip_quality" -eq 0 ]; then
  printf '==> project quality gate\n    SKIP (no executable check.sh in %s)\n' "$project_dir"
  printf 'SKIP: no executable check.sh in %s\n' "$project_dir" > "$log_dir/quality-check.log"
else
  printf '==> project quality gate\n    SKIP\n'
  printf 'SKIP: --skip-quality\n' > "$log_dir/quality-check.log"
fi

doctor_args=(-o "$out_dir/doctor" -s "$source_path" -n "$fault_count" -A "$wrapper_tool" -M "$module_tool" -O "$observe_tool" -W "$walk_tool" -r "$tracker_tool" -t "$trace_tool" -p "$report_tool")
if [ "$skip_wrapper" -eq 1 ]; then
  doctor_args=(-x "${doctor_args[@]}")
fi
doctor_args+=(-- "$@")

(cd "$project_dir" && run_logged "p101 doctor" "$log_dir/doctor.log" "$doctor_tool" "${doctor_args[@]}")
doctor_status=$?

if [ "$run_coverage" -eq 1 ] && [ -x "$project_dir/coverage-report.sh" ]; then
  (cd "$project_dir" && run_logged "project coverage receipt" "$log_dir/coverage.log" ./coverage-report.sh --no-open -- "$@")
  coverage_status=$?
  if [ "$coverage_status" -eq 0 ]; then coverage_state="PASS"; else coverage_state="FAIL"; fi
elif [ "$run_coverage" -eq 1 ]; then
  printf '==> project coverage receipt\n    SKIP (no executable coverage-report.sh in %s)\n' "$project_dir"
  printf 'SKIP: no executable coverage-report.sh in %s\n' "$project_dir" > "$log_dir/coverage.log"
else
  printf '==> project coverage receipt\n    SKIP\n'
  printf 'SKIP: --coverage not requested\n' > "$log_dir/coverage.log"
fi

if [ "$skip_html" -eq 0 ]; then
  if [ -d "$out_dir/doctor/observe" ]; then
    python3 "$script_dir/p101-html-report.py" "$out_dir/doctor/observe" -o "$out_dir/doctor/observe/index.html" > "$log_dir/observe-html.log" 2>&1
    html_status=$?
  fi
  python3 "$script_dir/p101-check-report.py" "$out_dir" -o "$out_dir/index.html" > "$log_dir/check-html.log" 2>&1
  html_status=$((html_status + $?))
else
  html_state="SKIP"
  printf 'SKIP: --skip-html\n' > "$log_dir/check-html.log"
fi

if [ "$skip_bundle" -eq 0 ] && [ -d "$out_dir/doctor/observe" ]; then
  "$script_dir/p101-bug-bundle.sh" -o "$out_dir/bug-bundle.tar.gz" "$out_dir/doctor/observe" > "$log_dir/bug-bundle.log" 2>&1
  bundle_status=$?
elif [ "$skip_bundle" -eq 1 ]; then
  bundle_state="SKIP"
  printf 'SKIP: --skip-bundle\n' > "$log_dir/bug-bundle.log"
else
  bundle_state="SKIP"
  printf 'SKIP: no observe directory available\n' > "$log_dir/bug-bundle.log"
fi

cat > "$summary" <<EOF
# p101 check

Project: \`${project_dir}\`

Source path: \`${source_path}\`

Command: \`$(quote_command "$@")\`

## Results

| Step | Status | Artifact |
| --- | --- | --- |
| Project quality gate | ${quality_state} (${quality_status}) | [log](./$(relpath "$log_dir/quality-check.log")) |
| p101 doctor | $([ "$doctor_status" -eq 0 ] && printf 'PASS' || { [ "$doctor_status" -eq 1 ] && printf 'FINDINGS' || printf 'TROUBLE'; }) (${doctor_status}) | [doctor](./doctor/) |
| Project coverage | ${coverage_state} (${coverage_status}) | [log](./$(relpath "$log_dir/coverage.log")) |
| HTML report | $([ "$html_state" = "SKIP" ] && printf 'SKIP' || { [ "$html_status" -eq 0 ] && printf 'PASS' || printf 'FAIL'; }) (${html_status}) | [index.html](./index.html) |
| Bug bundle | $([ "$bundle_state" = "SKIP" ] && printf 'SKIP' || { [ "$bundle_status" -eq 0 ] && printf 'PASS' || printf 'FAIL'; }) (${bundle_status}) | [bug-bundle.tar.gz](./bug-bundle.tar.gz) |

## Main artifacts

- Student report: [index.html](./index.html)
- Doctor summary: [doctor/summary.md](./doctor/summary.md)
- Module map: [doctor/module-map.md](./doctor/module-map.md)
- Observed run: [doctor/observe](./doctor/observe/)
- Observed run HTML: [doctor/observe/index.html](./doctor/observe/index.html)
- Error-path walk cases: [doctor/fault-walk](./doctor/fault-walk/)
- Bug bundle: [bug-bundle.tar.gz](./bug-bundle.tar.gz)

## How to read this

\`p101 check\` is a teaching workflow, not an OS-level tracer. It can only grade
what the p101 wrappers, the selected run, and the available project scripts make
visible. Use wrapper-audit findings first: direct libc calls can hide resource
activity from the runtime tools.
EOF

# Re-render now that summary.md exists.
if [ "$skip_html" -eq 0 ]; then
  python3 "$script_dir/p101-check-report.py" "$out_dir" -o "$out_dir/index.html" > "$log_dir/check-html.log" 2>&1
  html_status=$?
fi

printf 'p101 check report: %s\n' "$out_dir/index.html"
printf 'p101 check summary: %s\n' "$summary"

if [ "$doctor_status" -eq 2 ] || [ "$html_status" -ne 0 ] || [ "$bundle_status" -ne 0 ]; then
  exit 2
fi

if [ "$quality_status" -ne 0 ] || [ "$doctor_status" -ne 0 ] || [ "$coverage_status" -ne 0 ]; then
  exit 1
fi

exit 0
