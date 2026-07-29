#!/usr/bin/env bash
# Closed-workspace public API audit using Clang facts from every built consumer.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

out_dir=""
fail_findings=0
allow_incomplete=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      echo "Usage: ./check-workspace-public-api.sh [-o <dir>] [--fail-findings] [--allow-incomplete]"
      exit 0
      ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    --fail-findings) fail_findings=1; shift ;;
    --allow-incomplete) allow_incomplete=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

workspace="$(CDPATH='' cd .. && pwd -P)"
[ -n "$out_dir" ] || out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-workspace-api.XXXXXX")"
out_dir="$(mkdir -p "$out_dir/facts" && CDPATH='' cd -P "$out_dir" && pwd -P)"
audit="$workspace/programs/p101-wrapper-audit/p101-wrapper-audit"
scope_exclusions="$workspace/scripts/workspace-public-api-excludes.txt"

scope_exclusion_reason() {
  candidate="$1"
  while IFS='|' read -r excluded reason || [ -n "${excluded:-}" ]; do
    [ "$excluded" = "$candidate" ] || continue
    printf '%s\n' "$reason"
    return
  done < "$scope_exclusions"
}

find_tool() {
  repo="$1"
  name="$2"
  if [ -f "$repo/.last-build-dir" ] && [ -x "$repo/$(cat "$repo/.last-build-dir")/$name" ]; then
    printf '%s\n' "$repo/$(cat "$repo/.last-build-dir")/$name"
    return
  fi
  find "$repo" -maxdepth 2 -type f -name "$name" -perm -111 -print -quit
}

find_db() {
  repo="$1"
  if [ -f "$repo/.last-build-dir" ] && [ -f "$repo/$(cat "$repo/.last-build-dir")/compile_commands.json" ]; then
    printf '%s\n' "$repo/$(cat "$repo/.last-build-dir")/compile_commands.json"
    return
  fi
  find "$repo" -maxdepth 2 -type f -name compile_commands.json -path '*/build*/*' -print -quit
}

module_map="$(find_tool "$workspace/programs/p101-module-map" p101-module-map)"
[ -x "$audit" ] && [ -x "$module_map" ] || { echo "Build p101-wrapper-audit and p101-module-map first." >&2; exit 2; }

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
  db="$(find_db "$repo")"
  if [ -z "$db" ]; then
    missing+=("$relative (compile_commands.json missing)")
    continue
  fi
  name="$(basename "$repo")"
  facts="$out_dir/facts/$name.tsv"
  args=(--compile-db "$db" --compile-db-only --facts-output "$facts")
  [ -f "$repo/.p101-wrapper-audit-allow" ] && args+=(--allow-file "$repo/.p101-wrapper-audit-allow")
  if [[ "$relative" == ../libraries/* ]]; then
    # Libraries own public headers, including declarations that no current
    # translation unit happens to reference. Parse those interfaces directly.
    paths=("$repo/src" "$repo/include")
  else
    # Consumers contribute only facts reachable from compiled translation
    # units. This preserves the language/defines from the compile database and
    # avoids treating unrelated test headers as standalone C.
    args+=(--active-headers-only)
    paths=("$repo")
  fi
  set +e
  "$audit" "${args[@]}" "${paths[@]}" > "$out_dir/facts/$name.audit.txt" 2>&1
  rc=$?
  set -e
  [ "$rc" -le 1 ] && [ -s "$facts" ] || { echo "Fact extraction failed for $name" >&2; exit 2; }
  # Qualify modules by repository. Local includes stay in that repository;
  # p101_<library>/... includes resolve to the corresponding lib_<library>
  # module. The @ prefix tells p101-module-map the target is already resolved.
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

{
  printf '# p101 workspace public API candidates\n\n'
  printf '> These are deterministic candidates, not proof of dead API. Function use is\n'
  printf '> derived from Clang call facts. Type and macro findings are module-level:\n'
  printf '> they mean no workspace consumer included the declaring interface.\n\n'
  printf '## Candidates\n\n'
  grep -E '^- P101-MOD-00[6-9]:|^- P101-MOD-010:' "$out_dir/module-map-full.md" | grep '/libraries/' || true
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
