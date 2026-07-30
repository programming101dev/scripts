#!/usr/bin/env bash
# check-p101-regression-corpus.sh — run known p101 playground defects and check expected findings.

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

out_dir=""

usage() {
  cat <<'USAGE'
Usage: ./check-p101-regression-corpus.sh [-o <dir>]

Runs a small regression corpus against p101-tool-playground through p101-observe.
Each case has a known finding shape. This is intentionally smaller than the full
stack check; it is a behavior ratchet for the resource/trace/report pipeline.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-regression-corpus.XXXXXX")"
fi
out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"
summary="$out_dir/summary.md"

find_tool() {
  env_name="$1"
  shift

  configured="${!env_name:-}"
  if [ -n "$configured" ]; then
    printf '%s\n' "$configured"
    return 0
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

last_build_tool() {
  repo="$1"
  name="$2"
  marker="$repo/.last-build-dir"

  if [ -f "$marker" ]; then
    IFS= read -r build_dir < "$marker"
    candidate="$repo/$build_dir/$name"
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi
  return 1
}

json_number() {
  file="$1"
  key="$2"
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(int(data.get(sys.argv[2], 0)))' "$file" "$key"
}

assert_resource_model_parity() {
  tracker_json="$1"
  report_json="$2"

  python3 -c '
import collections, json, sys
tracker = json.load(open(sys.argv[1], encoding="utf-8"))
report = json.load(open(sys.argv[2], encoding="utf-8"))
ids = collections.Counter(item["id"] for item in report.get("findings", []))
actual = {
    "fd_leaks": ids["P101-FD-001"],
    "allocation_leaks": ids["P101-ALLOC-001"],
    "bad_releases": sum(ids[key] for key in ("P101-FD-002", "P101-FD-003", "P101-ALLOC-002", "P101-ALLOC-003", "P101-ALLOC-004")),
    "exec_inheritances": ids["P101-FD-004"],
    "generic_resource_leaks": ids["P101-RESOURCE-001"],
    "generic_bad_releases": sum(ids[key] for key in ("P101-RESOURCE-002", "P101-RESOURCE-003", "P101-RESOURCE-004", "P101-RESOURCE-005")),
}
expected = {key: int(tracker.get(key, 0)) for key in actual}
if actual != expected:
    raise SystemExit("resource model mismatch: tracker=%r report=%r" % (expected, actual))
' "$tracker_json" "$report_json"
}

assert_observe_receipt() {
  case_dir="$1"
  manifest="$case_dir/manifest.txt"
  receipt="$case_dir/receipt.txt"

  [ -f "$manifest" ] || return 1
  [ -f "$receipt" ] || return 1
  manifest_run_id="$(awk -F= '$1 == "run_id" { print substr($0, index($0, "=") + 1); exit }' "$manifest")"
  receipt_run_id="$(awk -F= '$1 == "run_id" { print substr($0, index($0, "=") + 1); exit }' "$receipt")"
  [ -n "$manifest_run_id" ] || return 1
  [ "$manifest_run_id" = "$receipt_run_id" ] || return 1
  grep -q '^schema=p101-run-receipt-v1$' "$receipt" || return 1
  grep -q '^fingerprint_security=change-detection-only$' "$receipt" || return 1
  grep -q '^artifact=resources	' "$receipt" || return 1
  grep -q '^artifact=calls	' "$receipt" || return 1
}

check_raw_model_parity() {
  raw_log="$1"
  tracker_json="$2"
  label="$3"
  maximum_report_status="${4:-1}"
  report_json="$out_dir/$label-report.json"
  empty_calls="$out_dir/empty-calls.log"

  printf 'P101COMPLETE\t4\t999\t1\t1\t100\t200\t0\t0\t0\n' > "$empty_calls"
  if "$report" -j -r "$raw_log" -c "$empty_calls" > "$report_json" 2>> "$log_dir/$label.log"; then
    report_rc=0
  else
    report_rc=$?
  fi
  [ "$report_rc" -le "$maximum_report_status" ] || return 1
  assert_resource_model_parity "$tracker_json" "$report_json"
}

check_case() {
  name="$1"
  scenario="$2"
  expected_fd="$3"
  expected_alloc="$4"
  expected_bad="$5"
  expected_exec="$6"
  expected_rc="${7:-0}"

  case_dir="$out_dir/$name"
  log="$log_dir/$name.log"
  echo "==> $name"
  rm -rf "$case_dir"
  if "$observe" -o "$case_dir" -r "$tracker" -d "$concurrency" -t "$trace" -p "$report" -- "$playground" -s "$scenario" -o "$out_dir/$name-output.txt" > "$log" 2>&1; then
    rc=0
  else
    rc=$?
  fi

  json="$case_dir/resource-report.json"
  correlated_json="$case_dir/correlated-report.json"
  if [ ! -f "$json" ]; then
    echo "    FAIL: missing resource-report.json"
    printf '| FAIL | %s | missing resource-report.json; [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
    return 1
  fi
  if [ ! -f "$correlated_json" ] || ! assert_resource_model_parity "$json" "$correlated_json"; then
    echo "    FAIL: resource tracker/report model mismatch"
    printf '| FAIL | %s | resource tracker/report model mismatch; [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
    return 1
  fi
  if ! assert_observe_receipt "$case_dir"; then
    echo "    FAIL: missing or inconsistent run receipt"
    printf '| FAIL | %s | missing or inconsistent run receipt; [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
    return 1
  fi

  fd="$(json_number "$json" fd_leaks)"
  alloc="$(json_number "$json" allocation_leaks)"
  bad="$(json_number "$json" bad_releases)"
  exec_inherit="$(json_number "$json" exec_inheritances)"

  if [ "$fd" -eq "$expected_fd" ] && [ "$alloc" -eq "$expected_alloc" ] && [ "$bad" -eq "$expected_bad" ] && [ "$exec_inherit" -eq "$expected_exec" ] && [ "$rc" -eq "$expected_rc" ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s fd=%s alloc=%s bad=%s exec=%s |\n' "$name" "$rc" "$fd" "$alloc" "$bad" "$exec_inherit" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc fd=$fd alloc=$alloc bad=$bad exec=$exec_inherit"
  printf '| FAIL | %s | expected rc=%s fd=%s alloc=%s bad=%s exec=%s; got rc=%s fd=%s alloc=%s bad=%s exec=%s; [log](./logs/%s) |\n' "$name" "$expected_rc" "$expected_fd" "$expected_alloc" "$expected_bad" "$expected_exec" "$rc" "$fd" "$alloc" "$bad" "$exec_inherit" "$(basename "$log")" >> "$summary"
  return 1
}

check_raw_bad_release() {
  name="synthetic-bad-release"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  rm -f "$raw_log" "$json"
  {
    printf 'P101FD\t4\t123\t7\t1\t100\t200\tOPEN\t3\t10\tmain\tbad-release.c\n'
    printf 'P101FD\t4\t123\t7\t2\t110\t210\tCLOSE\t3\t11\tmain\tbad-release.c\n'
    printf 'P101FD\t4\t123\t7\t3\t120\t220\tCLOSE\t3\t12\tmain\tbad-release.c\n'
    printf 'P101COMPLETE\t4\t123\t7\t4\t130\t230\t3\t0\t0\n'
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi
  check_raw_model_parity "$raw_log" "$json" "$name" || return 1

  bad="$(json_number "$json" bad_releases)"
  if [ "$rc" -eq 1 ] && [ "$bad" -eq 1 ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s bad=%s |\n' "$name" "$rc" "$bad" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc bad=$bad"
  printf '| FAIL | %s | expected rc=1 bad=1; got rc=%s bad=%s; [log](./logs/%s) |\n' "$name" "$rc" "$bad" "$(basename "$log")" >> "$summary"
  return 1
}

check_raw_generic_resource() {
  name="$1"
  fixture="$2"
  expected_leaks="$3"
  expected_bad="$4"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  rm -f "$raw_log" "$json"
  case "$fixture" in
    leak)
      {
        printf 'P101RESOURCE\t4\t123\t7\t1\t100\t200\tACQUIRE\tmapping\t0x1000\t-\t4096\tprivate\t10\tmap_file\tmapping.c\n'
        printf 'P101COMPLETE\t4\t123\t7\t2\t110\t210\t1\t0\t0\n'
      } > "$raw_log"
      ;;
    bad-replace)
      {
        printf 'P101RESOURCE\t4\t123\t7\t1\t100\t200\tACQUIRE\tmapping\t0x1000\t-\t4096\tprivate\t10\tmap_file\tmapping.c\n'
        printf 'P101RESOURCE\t4\t123\t7\t2\t110\t210\tREPLACE\tmapping\t0x1000\t-\t8192\tprivate\t11\tgrow_map\tmapping.c\n'
        printf 'P101RESOURCE\t4\t123\t7\t3\t120\t220\tRELEASE\tmapping\t0x1000\t-\t0\t-\t12\tunmap_file\tmapping.c\n'
        printf 'P101COMPLETE\t4\t123\t7\t4\t130\t230\t3\t0\t0\n'
      } > "$raw_log"
      ;;
    duplicate-acquire)
      {
        printf 'P101RESOURCE\t4\t123\t7\t1\t100\t200\tACQUIRE\tmapping\t0x1000\t-\t4096\tprivate\t10\tmap_file\tmapping.c\n'
        printf 'P101RESOURCE\t4\t123\t7\t2\t110\t210\tACQUIRE\tmapping\t0x1000\t-\t4096\tprivate\t11\tmap_file_again\tmapping.c\n'
        printf 'P101RESOURCE\t4\t123\t7\t3\t120\t220\tRELEASE\tmapping\t0x1000\t-\t0\t-\t12\tunmap_file\tmapping.c\n'
        printf 'P101RESOURCE\t4\t123\t7\t4\t130\t230\tRELEASE\tmapping\t0x1000\t-\t0\t-\t13\tunmap_file_again\tmapping.c\n'
        printf 'P101COMPLETE\t4\t123\t7\t5\t140\t240\t4\t0\t0\n'
      } > "$raw_log"
      ;;
    *)
      echo "internal error: unknown generic fixture: $fixture" >&2
      return 1
      ;;
  esac

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi
  check_raw_model_parity "$raw_log" "$json" "$name" || return 1

  leaks="$(json_number "$json" generic_resource_leaks)"
  bad="$(json_number "$json" generic_bad_releases)"
  if [ "$rc" -eq 1 ] && [ "$leaks" -eq "$expected_leaks" ] && [ "$bad" -eq "$expected_bad" ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s generic_leaks=%s generic_bad=%s |\n' "$name" "$rc" "$leaks" "$bad" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc generic_leaks=$leaks generic_bad=$bad"
  printf '| FAIL | %s | expected rc=1 generic_leaks=%s generic_bad=%s; got rc=%s generic_leaks=%s generic_bad=%s; [log](./logs/%s) |\n' "$name" "$expected_leaks" "$expected_bad" "$rc" "$leaks" "$bad" "$(basename "$log")" >> "$summary"
  return 1
}

check_raw_exec_inherit() {
  name="synthetic-exec-inherit"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  rm -f "$raw_log" "$json"
  {
    printf 'P101FD\t4\t123\t7\t1\t100\t200\tOPEN\t3\t10\tmain\texec-leak.c\n'
    printf 'P101EXEC\t4\t123\t7\t2\t110\t210\t3\t0\t20\tp101_execvp\tunistd.c\t/bin/echo\n'
    printf 'P101FD\t4\t123\t7\t3\t120\t220\tCLOSE\t3\t30\tmain\texec-leak.c\n'
    printf 'P101COMPLETE\t4\t123\t7\t4\t130\t230\t3\t0\t0\n'
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi
  check_raw_model_parity "$raw_log" "$json" "$name" || return 1

  exec_inherit="$(json_number "$json" exec_inheritances)"
  fd="$(json_number "$json" fd_leaks)"
  if [ "$rc" -eq 1 ] && [ "$exec_inherit" -eq 1 ] && [ "$fd" -eq 0 ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s exec=%s fd=%s |\n' "$name" "$rc" "$exec_inherit" "$fd" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc exec=$exec_inherit fd=$fd"
  printf '| FAIL | %s | expected rc=1 exec=1 fd=0; got rc=%s exec=%s fd=%s; [log](./logs/%s) |\n' "$name" "$rc" "$exec_inherit" "$fd" "$(basename "$log")" >> "$summary"
  return 1
}

check_raw_exec_cloexec_ok() {
  name="synthetic-exec-cloexec-ok"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  rm -f "$raw_log" "$json"
  {
    printf 'P101FD\t4\t123\t7\t1\t100\t200\tOPEN\t3\t10\tmain\texec-ok.c\n'
    printf 'P101EXEC\t4\t123\t7\t2\t110\t210\t3\t1\t20\tp101_execvp\tunistd.c\t/bin/echo\n'
    printf 'P101COMPLETE\t4\t123\t7\t3\t120\t220\t2\t0\t0\n'
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi
  check_raw_model_parity "$raw_log" "$json" "$name" || return 1

  exec_inherit="$(json_number "$json" exec_inheritances)"
  fd="$(json_number "$json" fd_leaks)"
  if [ "$rc" -eq 0 ] && [ "$exec_inherit" -eq 0 ] && [ "$fd" -eq 0 ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s exec=%s fd=%s |\n' "$name" "$rc" "$exec_inherit" "$fd" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc exec=$exec_inherit fd=$fd"
  printf '| FAIL | %s | expected rc=0 exec=0 fd=0; got rc=%s exec=%s fd=%s; [log](./logs/%s) |\n' "$name" "$rc" "$exec_inherit" "$fd" "$(basename "$log")" >> "$summary"
  return 1
}

check_raw_exec_failure_ok() {
  name="synthetic-exec-failure-ok"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  rm -f "$raw_log" "$json"
  {
    printf 'P101FD\t4\t123\t7\t1\t100\t200\tOPEN\t3\t10\tmain\texec-fail.c\n'
    printf 'P101EXEC\t4\t123\t7\t2\t110\t210\t3\t0\t20\tp101_execvp\tunistd.c\t/missing\n'
    printf 'P101EXECFAIL\t4\t123\t7\t3\t120\t220\t20\tp101_execvp\tunistd.c\t/missing\n'
    printf 'P101FD\t4\t123\t7\t4\t130\t230\tCLOSE\t3\t30\tmain\texec-fail.c\n'
    printf 'P101COMPLETE\t4\t123\t7\t5\t140\t240\t4\t0\t0\n'
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi
  check_raw_model_parity "$raw_log" "$json" "$name" || return 1

  exec_inherit="$(json_number "$json" exec_inheritances)"
  fd="$(json_number "$json" fd_leaks)"
  if [ "$rc" -eq 0 ] && [ "$exec_inherit" -eq 0 ] && [ "$fd" -eq 0 ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s exec=%s fd=%s |\n' "$name" "$rc" "$exec_inherit" "$fd" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc exec=$exec_inherit fd=$fd"
  printf '| FAIL | %s | expected rc=0 exec=0 fd=0; got rc=%s exec=%s fd=%s; [log](./logs/%s) |\n' "$name" "$rc" "$exec_inherit" "$fd" "$(basename "$log")" >> "$summary"
  return 1
}

check_v4_completion_receipt() {
  name="$1"
  include_receipt="$2"
  expected_rc="$3"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  {
    printf 'P101RESOURCE\t4\t123\t7\t1\t100\t200\tACQUIRE\tmapping\t0x1000\t-\t4096\tprivate\t10\tmap_file\tmapping.c\n'
    printf 'P101RESOURCE\t4\t123\t7\t2\t110\t210\tRELEASE\tmapping\t0x1000\t-\t0\t-\t11\tunmap_file\tmapping.c\n'
    if [ "$include_receipt" -eq 1 ]; then
      printf 'P101COMPLETE\t4\t123\t7\t3\t120\t220\t2\t0\t0\n'
    fi
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi

  complete=false
  if grep -q '"complete": true' "$json"; then
    complete=true
  fi
  expected_complete=false
  if [ "$include_receipt" -eq 1 ]; then
    expected_complete=true
  fi

  if [ "$rc" -eq "$expected_rc" ] && [ "$complete" = "$expected_complete" ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s complete=%s |\n' "$name" "$rc" "$complete" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc complete=$complete"
  printf '| FAIL | %s | expected rc=%s complete=%s; got rc=%s complete=%s; [log](./logs/%s) |\n' "$name" "$expected_rc" "$expected_complete" "$rc" "$complete" "$(basename "$log")" >> "$summary"
  return 1
}

check_raw_malformed_line() {
  name="$1"
  kind="$2"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  rm -f "$raw_log" "$json"

  case "$kind" in
    embedded-nul)
      python3 -c 'from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b"P101FD\t4\t123\t7\t1\t100\t200\tOPEN\x00\t3\t10\tmain\tbad.c\n")' "$raw_log"
      ;;
    overlong)
      python3 -c 'from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b"P101FD\t4\t123\t7\t1\t100\t200\tOPEN\t3\t10\tmain\t" + (b"a" * 5000) + b"\n")' "$raw_log"
      ;;
    *)
      echo "internal error: unknown malformed fixture kind: $kind" >&2
      return 1
      ;;
  esac

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi
  check_raw_model_parity "$raw_log" "$json" "$name" 2 || return 1

  malformed="$(json_number "$json" malformed)"
  fd="$(json_number "$json" fd_leaks)"
  alloc="$(json_number "$json" allocation_leaks)"
  bad="$(json_number "$json" bad_releases)"
  exec_inherit="$(json_number "$json" exec_inheritances)"

  if [ "$rc" -eq 2 ] && [ "$malformed" -eq 1 ] && [ "$fd" -eq 0 ] && [ "$alloc" -eq 0 ] && [ "$bad" -eq 0 ] && [ "$exec_inherit" -eq 0 ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s malformed=%s fd=%s alloc=%s bad=%s exec=%s |\n' "$name" "$rc" "$malformed" "$fd" "$alloc" "$bad" "$exec_inherit" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc malformed=$malformed fd=$fd alloc=$alloc bad=$bad exec=$exec_inherit"
  printf '| FAIL | %s | expected rc=2 malformed=1 fd=0 alloc=0 bad=0 exec=0; got rc=%s malformed=%s fd=%s alloc=%s bad=%s exec=%s; [log](./logs/%s) |\n' "$name" "$rc" "$malformed" "$fd" "$alloc" "$bad" "$exec_inherit" "$(basename "$log")" >> "$summary"
  return 1
}

observe="$(find_tool P101_OBSERVE "$(last_build_tool ../programs/p101-observe p101-observe || true)" ../programs/p101-observe/build-clang-22/p101-observe ../programs/p101-observe/build-clang/p101-observe p101-observe)"
tracker="$(find_tool P101_RESOURCE_TRACKER "$(last_build_tool ../programs/p101-resource-tracker p101-resource-tracker || true)" ../programs/p101-resource-tracker/build-clang-22/p101-resource-tracker ../programs/p101-resource-tracker/build-clang/p101-resource-tracker p101-resource-tracker)"
concurrency="$(find_tool P101_SYNC_CHECK "$(last_build_tool ../programs/p101-sync-check p101-sync-check || true)" ../programs/p101-sync-check/build-clang-22/p101-sync-check ../programs/p101-sync-check/build-clang/p101-sync-check p101-sync-check)"
trace="$(find_tool P101_TRACE "$(last_build_tool ../programs/p101-trace p101-trace || true)" ../programs/p101-trace/build-clang-22/p101-trace ../programs/p101-trace/build-clang/p101-trace p101-trace)"
report="$(find_tool P101_REPORT "$(last_build_tool ../programs/p101-report p101-report || true)" ../programs/p101-report/build-clang-22/p101-report ../programs/p101-report/build-clang/p101-report p101-report)"
playground="$(find_tool P101_TOOL_PLAYGROUND "$(last_build_tool ../playgrounds p101-tool-playground || true)" ../playgrounds/build-clang-22/p101-tool-playground ../playgrounds/build-clang/p101-tool-playground p101-tool-playground)"

cat > "$summary" <<'EOF'
# p101 regression corpus

| Status | Case | Result |
| --- | --- | --- |
EOF

failures=0
check_case clean clean-file 0 0 0 0 || failures=1
check_case fd-leak fd-leak 1 0 0 0 1 || failures=1
check_case alloc-leak alloc-leak 0 1 0 0 1 || failures=1
check_case double-close-error-path double-close 0 0 1 0 1 || failures=1
check_raw_bad_release || failures=1
check_raw_generic_resource synthetic-generic-leak leak 1 0 || failures=1
check_raw_generic_resource synthetic-generic-bad-replace bad-replace 0 1 || failures=1
check_raw_generic_resource synthetic-generic-duplicate-acquire duplicate-acquire 0 1 || failures=1
check_raw_exec_inherit || failures=1
check_raw_exec_cloexec_ok || failures=1
check_raw_exec_failure_ok || failures=1
check_v4_completion_receipt synthetic-v4-complete 1 0 || failures=1
check_v4_completion_receipt synthetic-v4-truncated 0 2 || failures=1
check_raw_malformed_line synthetic-embedded-nul embedded-nul || failures=1
check_raw_malformed_line synthetic-overlong-line overlong || failures=1

echo "p101 regression corpus output: $out_dir"
echo "Summary: $summary"

if [ "$failures" -ne 0 ]; then
  exit 1
fi
