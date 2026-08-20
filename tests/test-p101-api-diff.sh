#!/usr/bin/env bash
set -euo pipefail

tool=${1:-${P101_AUDIT_API:-}}
[ -x "$tool" ] || {
  printf '%s:1:1: error: audit-api executable is required [P101-API-TEST]\n' "$0" >&2
  exit 2
}
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
cat > "$work/old-facts.tsv" <<'EOF'
P101FACT	8	FUNCTION	include/one.h	one	1	1	p101_keep	0	1	c:@F@p101_keep	0	10	int (void)	int	0
P101FACT	8	NOTE	include/one.h	one	1	1	SEMANTIC_ROLE:p101:ownership:resource:acquire	p101_keep	0	c:@F@p101_keep	0	10
P101FACT	8	FUNCTION	include/one.h	one	1	2	p101_remove	0	1	c:@F@p101_remove	11	20	int (void)	int	0
P101FACT	8	FUNCTION	include/one.h	one	1	3	p101_platform	0	1	c:@F@p101_platform	21	30	int (void)	int	0
EOF
printf 'P101FACT\t8\tNOTE\t%s\tone\t1\t4\tAPI_TYPE_LAYOUT:16:8\tp101_public\t1\tc:@S@p101_public\t31\t40\n' "$work/workspace/libraries/lib_one/include/one.h" >> "$work/old-facts.tsv"
printf 'P101FACT\t8\tNOTE\t%s\tone\t1\t5\tAPI_ENUMERATOR_VALUE:2\tP101_READY\t1\tc:@E@p101_mode@P101_READY\t41\t50\n' "$work/workspace/libraries/lib_one/include/one.h" >> "$work/old-facts.tsv"
printf 'P101FACT\t8\tNOTE\t%s\tone\t1\t6\tAPI_MACRO_VALUE:64\tP101_LIMIT\t1\tmacro:%s:P101_LIMIT\t51\t60\n' "$work/workspace/libraries/lib_one/include/one.h" "$work/workspace/libraries/lib_one/include/one.h" >> "$work/old-facts.tsv"

"$tool" snapshot "$work/workspace" "$work/old-facts.tsv" "$work/old.tsv"
cat > "$manifest" <<'EOF'
function	function_usr	provenance	current_source	current_header	original_source	original_header	linux	macos	freebsd	native_function	native_function_usr
p101_keep	c:@F@p101_keep	POSIX	src/one.c	include/one.h	-	-	yes	yes	yes	keep	c:@F@keep
p101_platform	c:@F@p101_platform	POSIX	src/one.c	include/one.h	-	-	yes	no	yes	platform	c:@F@platform
p101_added	c:@F@p101_added	POSIX	src/one.c	include/one.h	-	-	yes	yes	yes	added	c:@F@added
EOF
cat > "$work/new-facts.tsv" <<'EOF'
P101FACT	8	FUNCTION	include/one.h	one	1	1	p101_keep	0	1	c:@F@p101_keep	0	10	long (void)	long	0
P101FACT	8	NOTE	include/one.h	one	1	1	SEMANTIC_ROLE:p101:ownership:resource:release	p101_keep	0	c:@F@p101_keep	0	10
P101FACT	8	FUNCTION	include/one.h	one	1	2	p101_platform	0	1	c:@F@p101_platform	11	20	int (void)	int	0
P101FACT	8	FUNCTION	include/one.h	one	1	3	p101_added	0	1	c:@F@p101_added	21	30	int (void)	int	0
EOF
printf 'P101FACT\t8\tNOTE\t%s\tone\t1\t4\tAPI_TYPE_LAYOUT:24:8\tp101_public\t1\tc:@S@p101_public\t31\t40\n' "$work/workspace/libraries/lib_one/include/one.h" >> "$work/new-facts.tsv"
printf 'P101FACT\t8\tNOTE\t%s\tone\t1\t5\tAPI_ENUMERATOR_VALUE:3\tP101_READY\t1\tc:@E@p101_mode@P101_READY\t41\t50\n' "$work/workspace/libraries/lib_one/include/one.h" >> "$work/new-facts.tsv"
printf 'P101FACT\t8\tNOTE\t%s\tone\t1\t6\tAPI_MACRO_VALUE:32\tP101_LIMIT\t1\tmacro:%s:P101_LIMIT\t51\t60\n' "$work/workspace/libraries/lib_one/include/one.h" "$work/workspace/libraries/lib_one/include/one.h" >> "$work/new-facts.tsv"
"$tool" snapshot "$work/workspace" "$work/new-facts.tsv" "$work/new.tsv"
{
  printf 'P101API\t1\n'
  tail -n +2 "$work/old.tsv" | cut -f1-8
} > "$work/old-v1.tsv"
status=0
"$tool" compare "$work/old-v1.tsv" "$work/new.tsv" > "$work/v1-output" 2> "$work/v1-diagnostics" || status=$?
test "$status" -eq 1
grep -Fq 'p101 API diff: 1 additions, 2 breaking changes' "$work/v1-output"
! grep -Fq '[P101-API-005]' "$work/v1-diagnostics"
status=0
"$tool" compare "$work/old.tsv" "$work/new.tsv" > "$work/output" 2> "$work/diagnostics" || status=$?
test "$status" -eq 1
grep -Fq 'p101 API diff: 1 additions, 7 breaking changes' "$work/output"
grep -Fq '[P101-API-001]' "$work/diagnostics"
grep -Fq '[P101-API-004]' "$work/diagnostics"
grep -Fq '[P101-API-005]' "$work/diagnostics"
grep -Fq '[P101-API-006]' "$work/diagnostics"
grep -Fq '[P101-API-007]' "$work/diagnostics"
grep -Fq '[P101-API-008]' "$work/diagnostics"
grep -Fq '[P101-API-009]' "$work/diagnostics"
printf 'p101 API diff tests: PASS\n'
