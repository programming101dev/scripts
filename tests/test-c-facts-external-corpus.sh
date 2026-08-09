#!/usr/bin/env bash

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

./checks/check-c-facts-external-corpus.sh --validate-only

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT INT TERM
cat > "$tmp_dir/v7.tsv" <<'EOF'
P101FACT	7	FILE	src/main.c	main	0	1	0
P101FACT	7	FUNCTION	src/main.c	main	0	1	main	c:@F@main	0	0	0	0	0	0	0	0	0	0
EOF
output="$(./checks/check-c-facts-external-corpus.sh --verify-facts "$tmp_dir/v7.tsv")"
[ "$output" = "version=7 facts=2" ] || {
  echo "current producer output was not counted: $output" >&2
  exit 1
}

cat > "$tmp_dir/mixed.tsv" <<'EOF'
P101FACT	7	FILE	src/main.c	main	0	1	0
P101FACT	6	FUNCTION	src/main.c	main	0	1	main	c:@F@main	0	0	0	0	0	0	0	0	0	0
EOF
if ./checks/check-c-facts-external-corpus.sh --verify-facts "$tmp_dir/mixed.tsv" >/dev/null 2>&1; then
  echo "mixed producer versions were accepted" >&2
  exit 1
fi

manifest="corpora/c-facts-external.tsv"
actual="$(awk -F '\t' 'NR > 1 { count[$1]++ } END {
  printf "good-c=%d good-cxx=%d poor-c=%d poor-cxx=%d ioccc=%d",
    count["good-c"], count["good-cxx"], count["poor-c"],
    count["poor-cxx"], count["ioccc"]
}' "$manifest")"
expected="good-c=10 good-cxx=10 poor-c=10 poor-cxx=10 ioccc=10"
[ "$actual" = "$expected" ] || {
  echo "unexpected cohort inventory: $actual" >&2
  exit 1
}

if awk -F '\t' 'NR > 1 { key = $4 "\t" $5; seen[key] = 1 } END {
  count = 0; for (key in seen) count++; exit count == 22 ? 0 : 1
}' "$manifest"; then
  :
else
  echo "expected 22 distinct pinned upstream trees" >&2
  exit 1
fi

echo "c-facts external corpus tests: PASS"
