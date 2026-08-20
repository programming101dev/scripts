#!/usr/bin/env bash
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

tool="${1:-${P101_TEST_WRAPPER_CONFORMANCE:-}}"
receipt_tool="${2:-${P101_TEST_REPOSITORY_RECEIPT:-}}"
[ -x "$tool" ] || {
  printf '%s:1:1: error: P101_TEST_WRAPPER_CONFORMANCE is not executable\n' "$0" >&2
  exit 2
}
[ -x "$receipt_tool" ] || {
  printf '%s:1:1: error: repository receipt tool is not executable\n' "$0" >&2
  exit 2
}

work="$(mktemp -d "${TMPDIR:-/tmp}/p101-conformance-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/repo/test"

cat > "$work/repo/api-manifest.tsv" <<'EOF'
function	function_usr	current_source	native_function	native_function_usr
p101_demo	c:@F@p101_demo	src/demo.c	demo	c:@F@demo
EOF
cat > "$work/repo/test/unit-test-manifest.tsv" <<'EOF'
function	function_usr	test_kind	test_source
p101_demo	c:@F@p101_demo	fault	test/test_fault_wrappers.c
EOF
cat > "$work/repo/test/fault-outcome-manifest.tsv" <<'EOF'
function	function_usr	domain	symbol_header	linux_faults	macos_faults	freebsd_faults	posix_faults	linux_conditional	macos_conditional	freebsd_conditional
p101_demo	c:@F@p101_demo	errno	errno.h	EIO	EIO	EIO	EIO			
EOF
cat > "$work/repo/test/conformance-manifest.tsv" <<'EOF'
function	function_usr	require_arguments	require_result
p101_demo	c:@F@p101_demo	false	false
EOF
cat > "$work/instrumentation.json" <<'EOF'
{"schema":"p101-instrumentation-platform-receipt-v1","passed":true,"function_capabilities":[{"library":"lib_demo","usr":"c:@F@p101_demo","has_env":true}]}
EOF
cat > "$work/model.json" <<'EOF'
{"schema":"p101-run-model-v1","nodes":[{"domain":"call","name":"p101_demo","kind":"call-enter","arguments":"-"},{"domain":"call","name":"p101_demo","kind":"call-exit","result":"-"}]}
EOF
cat > "$work/outcomes.tsv" <<'EOF'
P101WRAPPER	1	FAULT	linux	lib_demo	p101_demo	errno	EIO	5	PASS
EOF
printf '#define EIO 5\n' > "$work/macros.txt"

common=(
  --library lib_demo
  --repo "$work/repo"
  --instrumentation "$work/instrumentation.json"
  --model "$work/model.json"
  --outcomes "$work/outcomes.tsv"
  --platform linux
  --macros "$work/macros.txt"
)

set +e
"$tool" > "$work/conformance-cli.out" 2> "$work/conformance-cli.err"
status=$?
set -e
[ "$status" -eq 2 ]
grep -q 'P101-TEST-CONFORMANCE-001' "$work/conformance-cli.err"

"$tool" "${common[@]}" --receipt "$work/pass.json"
grep -q '"passed":true' "$work/pass.json"

: > "$work/outcomes.tsv"
set +e
"$tool" "${common[@]}" --receipt "$work/fail.json" > "$work/stdout" 2> "$work/stderr"
status=$?
set -e
[ "$status" -eq 1 ]
grep -q 'missing direct platform fault outcome' "$work/stderr"
grep -q '"passed":false' "$work/fail.json"

set +e
"$receipt_tool" > "$work/receipt-cli.out" 2> "$work/receipt-cli.err"
status=$?
set -e
[ "$status" -eq 2 ]
grep -q 'P101-TEST-RECEIPT-001' "$work/receipt-cli.err"

printf 'wrapper conformance diagnostics: PASS\n'
