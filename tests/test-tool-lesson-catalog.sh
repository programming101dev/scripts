#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
sandbox=$(mktemp -d "${TMPDIR:-/tmp}/p101-lesson-catalog.XXXXXX")
cleanup()
{
  rm -rf "$sandbox"
}
trap cleanup EXIT HUP INT TERM

lessons=$sandbox/playgrounds/lessons
catalog=$lessons/manifest.json
header=$sandbox/lesson_catalog.h
source=$sandbox/lesson_catalog.c
mkdir -p "$lessons"
printf '# Sample\n\nSubstantive guidance.\n' > "$lessons/sample.md"

write_catalog()
{
  duplicate=$1
  {
    printf '%s\n' '{"schema":"p101-finding-lesson-catalog-v3","url_base":"https://example.test/","lessons":['
    printf '%s' '{"lesson_id":"P101-LESSON-SAMPLE","path":"sample.md","finding_ids":["P101-SAMPLE-001"]}'
    if [ "$duplicate" = yes ]; then
      printf '%s' ',{"lesson_id":"P101-LESSON-SECOND","path":"sample.md","finding_ids":["P101-SAMPLE-001"]}'
    fi
    printf '%s\n' ']}'
  } > "$catalog"
}

run_generator()
{
  "$root/generators/generate-tool-lesson-catalog.sh" \
    --catalog "$catalog" --header "$header" --source "$source" "$@"
}

write_catalog no
run_generator >/dev/null
grep -q 'P101_TOOL_FINDING_SAMPLE_001' "$header"
grep -q '"P101-SAMPLE-001"' "$source"
grep -q '"P101-LESSON-SAMPLE"' "$source"
grep -q '"lessons/sample.md"' "$source"
grep -q '"https://example.test/lessons/sample.md#P101-SAMPLE-001"' "$source"
run_generator --check >/dev/null
printf 'drift\n' >> "$source"
status=0
run_generator --check >"$sandbox/drift.out" 2>"$sandbox/drift.err" || status=$?
[ "$status" -eq 1 ]
grep -q 'generated lesson catalog drift' "$sandbox/drift.err"

write_catalog yes
status=0
run_generator >"$sandbox/duplicate.out" 2>"$sandbox/duplicate.err" || status=$?
[ "$status" -eq 2 ]
grep -q 'malformed catalog' "$sandbox/duplicate.err"

# Tool sources name findings, not playground internals. Routes come from the
# generated lib_tool_support catalog, and repair guidance stays in lesson.md.
workspace=$(dirname -- "$root")
if rg -n \
  --glob '*.c' --glob '*.h' --glob '!**/test/**' \
  --glob '!**/lib_tool_support/src/lesson_catalog.c' \
  --glob '!**/lib_tool_support/include/p101_tool_support/lesson_catalog.h' \
  '(playgrounds/(lessons|corpus|tracks)|programming101dev/playgrounds/(blob|tree))' \
  "$workspace/programs" "$workspace/libraries" > "$sandbox/private-routes.txt"; then
  cat "$sandbox/private-routes.txt" >&2
  printf '%s\n' 'tool source embeds a private playground route' >&2
  exit 1
fi
if rg -n \
  --glob '*.c' --glob '!**/test/**' \
  '"[^"\n]*\[P101-([A-Z]+-)+[0-9][0-9][0-9]\]' \
  "$workspace/programs" "$workspace/libraries" > "$sandbox/raw-diagnostics.txt"; then
  cat "$sandbox/raw-diagnostics.txt" >&2
  printf '%s\n' 'tool source bypasses the shared diagnostic/lesson lookup' >&2
  exit 1
fi

printf 'PASS: native lesson catalog generation, drift, duplicate rejection, and centralized routes\n'
