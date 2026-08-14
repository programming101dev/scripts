#!/usr/bin/env bash
set -euo pipefail

tool=${P101_AUDIT_API:?P101_AUDIT_API is required}
work=$(mktemp -d "${TMPDIR:-/tmp}/p101-api-diff.XXXXXX")
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/workspace/libraries/lib_one"
manifest="$work/workspace/libraries/lib_one/api-manifest.tsv"
cat > "$manifest" <<'EOF'
function	function_usr	provenance	current_source	current_header	original_source	original_header	linux	macos	freebsd	native_function	native_function_usr
p101_keep	c:@F@p101_keep	POSIX	src/one.c	include/one.h	-	-	yes	yes	yes	keep	c:@F@keep
p101_remove	c:@F@p101_remove	POSIX	src/one.c	include/one.h	-	-	yes	yes	yes	remove	c:@F@remove
p101_platform	c:@F@p101_platform	POSIX	src/one.c	include/one.h	-	-	yes	yes	yes	platform	c:@F@platform
EOF

"$tool" snapshot "$work/workspace" "$work/old.tsv"
cat > "$manifest" <<'EOF'
function	function_usr	provenance	current_source	current_header	original_source	original_header	linux	macos	freebsd	native_function	native_function_usr
p101_keep	c:@F@p101_keep	POSIX	src/one.c	include/one.h	-	-	yes	yes	yes	keep	c:@F@keep
p101_platform	c:@F@p101_platform	POSIX	src/one.c	include/one.h	-	-	yes	no	yes	platform	c:@F@platform
p101_added	c:@F@p101_added	POSIX	src/one.c	include/one.h	-	-	yes	yes	yes	added	c:@F@added
EOF
"$tool" snapshot "$work/workspace" "$work/new.tsv"
status=0
"$tool" compare "$work/old.tsv" "$work/new.tsv" > "$work/output" 2> "$work/diagnostics" || status=$?
test "$status" -eq 1
grep -Fq 'p101 API diff: 1 additions, 2 breaking changes' "$work/output"
grep -Fq '[P101-API-001]' "$work/diagnostics"
grep -Fq '[P101-API-004]' "$work/diagnostics"
printf 'p101 API diff tests: PASS\n'
