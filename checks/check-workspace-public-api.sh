#!/usr/bin/env bash
# Closed-workspace public API audit using Clang facts from every built consumer.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# shellcheck source=shared/artifacts.sh
. ./shared/artifacts.sh

out_dir=""
fail_findings=0
allow_incomplete=0
facts_cache="${P101_C_FACTS_CACHE_DIR:-}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      echo "Usage: ./check-workspace-public-api.sh [-o <dir>] [--facts-cache <dir>] [--fail-findings] [--allow-incomplete]"
      exit 0
      ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    --facts-cache) facts_cache="${2:?}"; shift 2 ;;
    --fail-findings) fail_findings=1; shift ;;
    --allow-incomplete) allow_incomplete=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

workspace="$(CDPATH='' cd .. && pwd -P)"
[ -n "$out_dir" ] || out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-workspace-api.XXXXXX")"
out_dir="$(mkdir -p "$out_dir/facts" && CDPATH='' cd -P "$out_dir" && pwd -P)"
audit="${P101_AUDIT_WRAPPERS:-$workspace/programs/p101-audit/audit-wrappers}"
facts_cache_tool="$workspace/scripts/checks/p101-facts-cache.py"
scope_exclusions="$workspace/scripts/contracts/workspace-public-api-excludes.txt"

scope_exclusion_reason() {
  candidate="$1"
  while IFS='|' read -r excluded reason || [ -n "${excluded:-}" ]; do
    [ "$excluded" = "$candidate" ] || continue
    printf '%s\n' "$reason"
    return
  done < "$scope_exclusions"
}

find_tool() { p101_find_built_tool "$1" "$2"; }
find_db() { p101_find_compile_database "$1"; }

module_map="${P101_AUDIT_MODULES:-}"
[ -n "$module_map" ] || module_map="$(find_tool "$workspace/programs/p101-audit" audit-modules || true)"
[ -x "$audit" ] && [ -x "$module_map" ] || { echo "Build audit-wrappers and audit-modules first." >&2; exit 2; }

combined="$out_dir/workspace-facts.tsv"
: > "$combined"
count=0
expected=0
excluded=0
missing=()
while IFS='|' read -r _url relative _language || [ -n "${relative:-}" ]; do
  [ -n "${relative:-}" ] || continue
  [ "$_language" = "c" ] || [ "$_language" = "cxx" ] || continue
  exclusion_reason="$(scope_exclusion_reason "$relative")"
  if [ -n "$exclusion_reason" ]; then
    printf '%s | %s\n' "$relative" "$exclusion_reason" >> "$out_dir/excluded-repositories.txt"
    excluded=$((excluded + 1))
    continue
  fi
  expected=$((expected + 1))
  repo="$(CDPATH='' cd "$relative" 2>/dev/null && pwd -P || true)"
  if [ -z "$repo" ]; then
    missing+=("$relative (repository missing)")
    continue
  fi
  db="$(find_db "$repo" || true)"
  if [ -z "$db" ]; then
    missing+=("$relative (compile_commands.json missing)")
    continue
  fi
  name="$(basename "$repo")"
  facts="$out_dir/facts/$name.tsv"
  args=(--compile-db "$db" --compile-db-only --facts-output "$facts")
  [ -f "$repo/.audit-wrappers-allow" ] && args+=(--allow-file "$repo/.audit-wrappers-allow")
  [ -f "$repo/.audit-wrappers-allow.$(uname -s)" ] && args+=(--allow-file "$repo/.audit-wrappers-allow.$(uname -s)")
  if [[ "$relative" == ../libraries/* ]]; then
    # Libraries own public headers, including declarations that no current
    # translation unit happens to reference. Parse those interfaces directly.
    paths=("$repo/src" "$repo/include")
    cache_namespace="library-full"
  else
    # Consumers contribute only facts reachable from compiled translation
    # units. This preserves the language/defines from the compile database and
    # avoids treating unrelated test headers as standalone C.
    args+=(--active-headers-only)
    paths=("$repo")
    cache_namespace="consumer-active"
  fi
  cache_args=(
    --cache "$facts_cache"
    --namespace "$cache_namespace"
    --producer "$audit"
    --compile-db "$db"
    --dependency-root "$workspace/libraries"
    --artifact "facts=$facts"
  )
  for path in "${paths[@]}"; do
    cache_args+=(--path "$path")
  done
  rc=1
  if [ -n "$facts_cache" ]; then
    set +e
    "$facts_cache_tool" restore "${cache_args[@]}" > "$out_dir/facts/$name.audit.txt" 2>&1
    rc=$?
    set -e
    [ "$rc" -le 1 ] || { cat "$out_dir/facts/$name.audit.txt" >&2; exit 2; }
  fi
  if [ "$rc" -ne 0 ]; then
    set +e
    "$audit" "${args[@]}" "${paths[@]}" > "$out_dir/facts/$name.audit.txt" 2>&1
    rc=$?
    set -e
    if [ "$rc" -le 1 ] && [ -s "$facts" ] && [ -n "$facts_cache" ]; then
      "$facts_cache_tool" store "${cache_args[@]}" >> "$out_dir/facts/$name.audit.txt" 2>&1 || {
        cat "$out_dir/facts/$name.audit.txt" >&2
        exit 2
      }
    fi
  fi
  [ "$rc" -le 1 ] && [ -s "$facts" ] || { echo "Fact extraction failed for $name" >&2; exit 2; }
  # Qualify modules by repository. Local includes stay in that repository;
  # p101_<library>/... includes resolve to the corresponding lib_<library>
  # module. The @ prefix tells audit-modules the target is already resolved.
  awk -F '\t' -v repo="$name" '
    BEGIN { OFS=FS }
    function module_name(value, base, parts, count) {
      count=split(value, parts, "/")
      base=parts[count]
      sub(/\.[^.]*$/, "", base)
      sub(/^p101_/, "", base)
      return base
    }
    {
      $5=repo "/" module_name($5)
      if ($3 == "INCLUDE") {
        target_module=module_name($8)
        if ($9 == "1") {
          $8="@" repo "/" target_module
        } else if (index($8, "p101_") == 1 && index($8, "/") > 0) {
          split($8, path_parts, "/")
          target_repo=path_parts[1]
          sub(/^p101_/, "lib_", target_repo)
          $8="@" target_repo "/" target_module
          $9="1"
        }
      }
      print
    }' "$facts" >> "$combined"
  count=$((count + 1))
done < repos.txt

[ "$count" -gt 0 ] || { echo "No built repositories supplied facts." >&2; exit 2; }
if [ "${#missing[@]}" -gt 0 ]; then
  {
    printf 'Workspace API evidence is incomplete: admitted %s of %s C/C++ repositories.\n' "$count" "$expected"
    printf 'Run update-all.sh first so every consumer has a compile database:\n'
    printf '  - %s\n' "${missing[@]}"
  } >&2
  [ "$allow_incomplete" -eq 1 ] || exit 2
fi
set +e
"$module_map" -i "$combined" -o "$out_dir/module-map-full.md" "$workspace/libraries" "$workspace/programs" "$workspace/playgrounds" "$workspace/templates" "$workspace/examples" > "$out_dir/module-map.stdout.txt" 2> "$out_dir/module-map.stderr.txt"
rc=$?
set -e
[ "$rc" -le 1 ] || { cat "$out_dir/module-map.stderr.txt" >&2; exit 2; }

extract_public_api_candidates() {
  # Match the rule semantics emitted by the live module-map tool, not its
  # diagnostic numbering. Renumbering a finding must not silently turn this
  # workspace gate into a zero-finding parser.
  grep -E '^- P101-MOD-[0-9]+:' "$1" |
    grep -E 'non-static but does not appear to be part|declared here, but no matching non-static definition|declared here, but no other module includes|Macro .* is exposed|Type .* is exposed' |
    grep '/libraries/' || true
}

parser_fixture="$out_dir/public-api-parser-fixture.md"
cat > "$parser_fixture" <<'EOF'
- P101-MOD-106: /workspace/libraries/lib_demo/include/demo.h: `demo` is non-static but does not appear to be part of a used module interface.
- P101-MOD-210: /workspace/libraries/lib_demo/src/demo.c: Type `demo` is exposed, but no other module includes its interface.
- P101-MOD-005: /workspace/libraries/lib_demo/src/not-public.c: unrelated teaching note.
EOF
fixture_findings="$(extract_public_api_candidates "$parser_fixture" | wc -l | tr -d '[:space:]')"
if [ "$fixture_findings" -ne 2 ]; then
  printf 'Public-API finding parser rejected its known-positive fixture.\n' >&2
  exit 2
fi

{
  printf '# p101 workspace public API candidates\n\n'
  printf '> These are deterministic candidates, not proof of dead API. Function use is\n'
  printf '> derived from Clang call facts. Type and macro findings are module-level:\n'
  printf '> they mean no workspace consumer included the declaring interface.\n\n'
  printf '## Candidates\n\n'
  extract_public_api_candidates "$out_dir/module-map-full.md"
} > "$out_dir/public-api.md"

findings="$(grep -c '^- P101-' "$out_dir/public-api.md" || true)"
printf 'Workspace API audit: %s fact sets, %s public API candidate(s)\n' "$count" "$findings"
printf 'Scope: %s C/C++ repositories admitted, %s explicitly excluded\n' "$expected" "$excluded"
if [ "${#missing[@]}" -gt 0 ]; then
  printf 'WARNING: incomplete evidence (%s of %s C/C++ repositories admitted)\n' "$count" "$expected"
fi
printf 'Report: %s\n' "$out_dir/public-api.md"
if [ "$fail_findings" -eq 1 ] && [ "$findings" -gt 0 ]; then
  exit 1
fi
exit 0
