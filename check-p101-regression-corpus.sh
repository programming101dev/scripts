#!/usr/bin/env bash
# check-p101-regression-corpus.sh — run known p101 playground defects and check expected findings.

set -euo pipefail
unset CDPATH
CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

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
out_dir="$(mkdir -p "$out_dir" && CDPATH= cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"
summary="$out_dir/summary.md"

find_tool() {
  env_name="$1"
  shift

  eval "configured=\${$env_name:-}"
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

json_number() {
  file="$1"
  key="$2"
  python3 -c 'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(int(data.get(sys.argv[2], 0)))' "$file" "$key"
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
  if "$observe" -o "$case_dir" -r "$tracker" -t "$trace" -p "$report" -- "$playground" -s "$scenario" -o "$out_dir/$name-output.txt" > "$log" 2>&1; then
    rc=0
  else
    rc=$?
  fi

  json="$case_dir/resource-report.json"
  if [ ! -f "$json" ]; then
    echo "    FAIL: missing resource-report.json"
    printf '| FAIL | %s | missing resource-report.json; [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
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
    printf 'P101FD\t2\t123\t1\t100\t200\tOPEN\t3\t10\tmain\tbad-release.c\n'
    printf 'P101FD\t2\t123\t2\t110\t210\tCLOSE\t3\t11\tmain\tbad-release.c\n'
    printf 'P101FD\t2\t123\t3\t120\t220\tCLOSE\t3\t12\tmain\tbad-release.c\n'
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi

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

check_raw_exec_inherit() {
  name="synthetic-exec-inherit"
  raw_log="$out_dir/$name.log"
  json="$out_dir/$name.json"
  log="$log_dir/$name.log"

  echo "==> $name"
  rm -f "$raw_log" "$json"
  {
    printf 'P101FD\t2\t123\t1\t100\t200\tOPEN\t3\t10\tmain\texec-leak.c\n'
    printf 'P101EXEC\t2\t123\t2\t110\t210\t3\t0\t20\tp101_execvp\tunistd.c\t/bin/echo\n'
    printf 'P101FD\t2\t123\t3\t120\t220\tCLOSE\t3\t30\tmain\texec-leak.c\n'
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi

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
    printf 'P101FD\t2\t123\t1\t100\t200\tOPEN\t3\t10\tmain\texec-ok.c\n'
    printf 'P101EXEC\t2\t123\t2\t110\t210\t3\t1\t20\tp101_execvp\tunistd.c\t/bin/echo\n'
  } > "$raw_log"

  if "$tracker" -j "$raw_log" > "$json" 2> "$log"; then
    rc=0
  else
    rc=$?
  fi

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
      python3 -c 'from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b"P101FD\t2\t123\t1\t100\t200\tOPEN\x00\t3\t10\tmain\tbad.c\n")' "$raw_log"
      ;;
    overlong)
      python3 -c 'from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b"P101FD\t2\t123\t1\t100\t200\tOPEN\t3\t10\tmain\t" + (b"a" * 3000) + b"\n")' "$raw_log"
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

  malformed="$(json_number "$json" malformed)"
  fd="$(json_number "$json" fd_leaks)"
  alloc="$(json_number "$json" allocation_leaks)"
  bad="$(json_number "$json" bad_releases)"
  exec_inherit="$(json_number "$json" exec_inheritances)"

  if [ "$rc" -eq 0 ] && [ "$malformed" -eq 1 ] && [ "$fd" -eq 0 ] && [ "$alloc" -eq 0 ] && [ "$bad" -eq 0 ] && [ "$exec_inherit" -eq 0 ]; then
    echo "    PASS"
    printf '| PASS | %s | rc=%s malformed=%s fd=%s alloc=%s bad=%s exec=%s |\n' "$name" "$rc" "$malformed" "$fd" "$alloc" "$bad" "$exec_inherit" >> "$summary"
    return 0
  fi

  echo "    FAIL: got rc=$rc malformed=$malformed fd=$fd alloc=$alloc bad=$bad exec=$exec_inherit"
  printf '| FAIL | %s | expected rc=0 malformed=1 fd=0 alloc=0 bad=0 exec=0; got rc=%s malformed=%s fd=%s alloc=%s bad=%s exec=%s; [log](./logs/%s) |\n' "$name" "$rc" "$malformed" "$fd" "$alloc" "$bad" "$exec_inherit" "$(basename "$log")" >> "$summary"
  return 1
}

observe="$(find_tool P101_OBSERVE ../programs/p101-observe/build-clang-22/p101-observe ../programs/p101-observe/build-clang/p101-observe p101-observe)"
tracker="$(find_tool P101_RESOURCE_TRACKER ../programs/p101-resource-tracker/build-clang-22/p101-resource-tracker ../programs/p101-resource-tracker/build-clang/p101-resource-tracker p101-resource-tracker)"
trace="$(find_tool P101_TRACE ../programs/p101-trace/build-clang-22/p101-trace ../programs/p101-trace/build-clang/p101-trace p101-trace)"
report="$(find_tool P101_REPORT ../programs/p101-report/build-clang-22/p101-report ../programs/p101-report/build-clang/p101-report p101-report)"
playground="$(find_tool P101_TOOL_PLAYGROUND ../playgrounds/build-clang-22/p101-tool-playground ../playgrounds/build-clang/p101-tool-playground p101-tool-playground)"

cat > "$summary" <<'EOF'
# p101 regression corpus

| Status | Case | Result |
| --- | --- | --- |
EOF

failures=0
check_case clean clean-file 0 0 0 0 || failures=1
check_case fd-leak fd-leak 1 0 0 0 1 || failures=1
check_case alloc-leak alloc-leak 0 1 0 0 1 || failures=1
check_case double-close-error-path double-close 0 0 0 0 1 || failures=1
check_raw_bad_release || failures=1
check_raw_exec_inherit || failures=1
check_raw_exec_cloexec_ok || failures=1
check_raw_malformed_line synthetic-embedded-nul embedded-nul || failures=1
check_raw_malformed_line synthetic-overlong-line overlong || failures=1

echo "p101 regression corpus output: $out_dir"
echo "Summary: $summary"

if [ "$failures" -ne 0 ]; then
  exit 1
fi
