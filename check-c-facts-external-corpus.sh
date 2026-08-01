#!/usr/bin/env bash
# Reproducible external stress corpus for lib_c_facts/p101-c-facts.

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

manifest="corpora/c-facts-external.tsv"
context_manifest="corpora/c-facts-external-cflags.tsv"
cache_dir="${P101_EXTERNAL_CORPUS_CACHE:-${XDG_CACHE_HOME:-${HOME}/.cache}/p101/c-facts-external}"
out_dir=""
facts_tool=""
facts_clang="${P101_C_FACTS_CLANG:-}"
cohort_filter=""
case_filter=""
offline=0
validate_only=0

usage() {
  cat <<'USAGE'
Usage: ./check-c-facts-external-corpus.sh [options]

Fetch pinned external C/C++ sources and stress p101-c-facts over five cohorts:
10 mature C projects, 10 mature C++ projects, 10 intentionally poor C cases,
10 intentionally poor C++ cases, and 10 IOCCC entries.

Options:
  -o, --output DIR       artifact directory (default: a temporary directory)
  --cache DIR            persistent source cache
  --facts-tool PATH      p101-c-facts executable or launcher
  --clang PATH           Clang driver used to derive builtin/system context
  --cohort NAME          run only one cohort
  --case NAME            run only one case
  --offline              never fetch; require every pinned revision in cache
  --validate-only        validate the manifest without fetching or parsing
  -h, --help             show this help

Every case must emit semantic facts and must not crash. Parser status 2 is
reported as PASS-PARTIAL so missing generated/build context remains visible.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o|--output) out_dir="${2:?}"; shift 2 ;;
    --cache) cache_dir="${2:?}"; shift 2 ;;
    --facts-tool) facts_tool="${2:?}"; shift 2 ;;
    --clang) facts_clang="${2:?}"; shift 2 ;;
    --cohort) cohort_filter="${2:?}"; shift 2 ;;
    --case) case_filter="${2:?}"; shift 2 ;;
    --offline) offline=1; shift ;;
    --validate-only) validate_only=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

validate_manifest() {
  awk -F '\t' '
    NR == 1 {
      expected = "cohort\tcase_id\trepository\turl\trevision\tlanguage\tmode\tselection\tmax_sources\tlicense"
      if ($0 != expected) {
        print "invalid manifest header" > "/dev/stderr"
        failed = 1
      }
      next
    }
    {
      rows++
      cohort[$1]++
      if ($1 !~ /^(good-c|good-cxx|poor-c|poor-cxx|ioccc)$/) {
        print "invalid cohort at line " NR ": " $1 > "/dev/stderr"; failed = 1
      }
      if ($2 !~ /^[a-z0-9][a-z0-9-]*$/ || seen_case[$2]++) {
        print "invalid or duplicate case at line " NR ": " $2 > "/dev/stderr"; failed = 1
      }
      if ($3 !~ /^[A-Za-z0-9][A-Za-z0-9._-]*$/) {
        print "invalid repository key at line " NR ": " $3 > "/dev/stderr"; failed = 1
      }
      if ($4 !~ /^https:\/\/github.com\/[^[:space:]]+\.git$/) {
        print "non-GitHub URL at line " NR ": " $4 > "/dev/stderr"; failed = 1
      }
      if ($5 !~ /^[0-9a-f]{40}$/) {
        print "revision is not a full commit at line " NR ": " $5 > "/dev/stderr"; failed = 1
      }
      if ($6 !~ /^(c|cxx)$/ || $7 !~ /^(tree|exact)$/ || $8 == "" ||
          $9 !~ /^[1-9][0-9]*$/ || $10 == "") {
        print "invalid case fields at line " NR > "/dev/stderr"; failed = 1
      }
      identity = $3 SUBSEP $4 SUBSEP $5
      repo_identity[$3] = repo_identity[$3] ? repo_identity[$3] : identity
      if (repo_identity[$3] != identity) {
        print "repository key has inconsistent URL/revision: " $3 > "/dev/stderr"; failed = 1
      }
    }
    END {
      required[1] = "good-c"; required[2] = "good-cxx"; required[3] = "poor-c"
      required[4] = "poor-cxx"; required[5] = "ioccc"
      if (rows != 50) {
        print "expected 50 cases, found " rows > "/dev/stderr"; failed = 1
      }
      for (i = 1; i <= 5; i++) {
        if (cohort[required[i]] != 10) {
          print "expected 10 " required[i] " cases, found " cohort[required[i]] > "/dev/stderr"
          failed = 1
        }
      }
      exit failed
    }
  ' "$manifest"
  awk -F '\t' '
    NR == 1 {
      if ($0 != "repository\tcflag") {
        print "invalid parser-context header" > "/dev/stderr"; failed = 1
      }
      next
    }
    $1 !~ /^[A-Za-z0-9][A-Za-z0-9._-]*$/ || $2 !~ /^-/ {
      print "invalid parser-context entry at line " NR > "/dev/stderr"; failed = 1
    }
    { if (seen[$1 SUBSEP $2]++) {
        print "duplicate parser-context entry at line " NR > "/dev/stderr"; failed = 1
      }
    }
    END { exit failed }
  ' "$context_manifest"
}

validate_manifest
if [ "$validate_only" -eq 1 ]; then
  echo "c-facts external corpus manifest: PASS (50 cases, 10 per cohort)"
  exit 0
fi

if [ -z "$facts_tool" ]; then
  facts_tool="../programs/p101-wrapper-audit/p101-c-facts"
fi
case "$facts_tool" in
  /*) ;;
  *) facts_tool="$(CDPATH='' cd -P "$(dirname -- "$facts_tool")" && pwd -P)/$(basename -- "$facts_tool")" ;;
esac
if [ ! -x "$facts_tool" ]; then
  echo "p101-c-facts is not executable: $facts_tool" >&2
  exit 2
fi
if [ -z "$facts_clang" ]; then
  facts_clang="$(command -v clang 2>/dev/null || true)"
fi
if [ -z "$facts_clang" ] || [ ! -x "$facts_clang" ]; then
  echo "Clang driver is not executable: $facts_clang" >&2
  exit 2
fi
resource_dir="$("$facts_clang" -print-resource-dir 2>/dev/null || true)"
sysroot=""
if [ "$(uname -s)" = "Darwin" ] && command -v xcrun >/dev/null 2>&1; then
  sysroot="$(xcrun --show-sdk-path 2>/dev/null || true)"
fi

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-c-facts-external.XXXXXX")"
fi
out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
cache_dir="$(mkdir -p "$cache_dir/repos" && CDPATH='' cd -P "$cache_dir" && pwd -P)"
results="$out_dir/results.tsv"
summary="$out_dir/summary.md"

printf 'status\tcohort\tcase_id\trepository\trevision\tsources\tsource_macros\ttool_status\tfacts\tfiles\tincludes\tfunctions\tcalls\ttypes\temitted_macros\tnotes\n' > "$results"
cat > "$summary" <<'EOF'
# p101-c-facts external corpus

Every case must emit facts without crashing. Missing generated/build context
is retained as an explicit partial result rather than hidden.

| Status | Cohort | Case | Sources | Tool status | Facts |
| --- | --- | --- | ---: | ---: | ---: |
EOF

prepare_repo() {
  repository="$1"
  url="$2"
  revision="$3"
  repo_dir="$cache_dir/repos/$repository"

  if [ ! -d "$repo_dir/.git" ]; then
    if [ "$offline" -eq 1 ]; then
      echo "offline cache miss: $repository" >&2
      return 1
    fi
    mkdir -p "$repo_dir"
    git -C "$repo_dir" init -q
    git -C "$repo_dir" remote add origin "$url"
  fi
  if ! git -C "$repo_dir" cat-file -e "$revision^{commit}" 2>/dev/null; then
    if [ "$offline" -eq 1 ]; then
      echo "offline cache lacks $repository revision $revision" >&2
      return 1
    fi
    git -C "$repo_dir" fetch -q --depth 1 origin "$revision"
  fi
  git -C "$repo_dir" checkout -q --detach "$revision"
  actual="$(git -C "$repo_dir" rev-parse HEAD)"
  [ "$actual" = "$revision" ] || {
    echo "$repository resolved to $actual, expected $revision" >&2
    return 1
  }
}

select_sources() {
  repo_dir="$1"
  language="$2"
  mode="$3"
  selection="$4"
  maximum="$5"
  destination="$6"
  all_sources="$destination.all"
  : > "$all_sources"

  old_ifs="$IFS"
  IFS=';'
  set -- $selection
  IFS="$old_ifs"
  if [ "$mode" = "exact" ]; then
    for item in "$@"; do
      [ -f "$repo_dir/$item" ] || {
        echo "selected source does not exist: $item" >&2
        return 1
      }
      printf '%s\n' "$item" >> "$all_sources"
    done
  else
    for item in "$@"; do
      [ -d "$repo_dir/$item" ] || {
        echo "selected source root does not exist: $item" >&2
        return 1
      }
      if [ "$language" = "c" ]; then
        find "$repo_dir/$item" -type f -name '*.c' -print
      else
        find "$repo_dir/$item" -type f \( -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' -o -name '*.C' \) -print
      fi
    done | sed "s|^$repo_dir/||" >> "$all_sources"
  fi
  LC_ALL=C sort -u "$all_sources" | awk -v maximum="$maximum" 'NR <= maximum' > "$destination"
  rm -f "$all_sources"
  [ -s "$destination" ]
}

count_kind() {
  kind="$1"
  file="$2"
  awk -F '\t' -v kind="$kind" '$1 == "P101FACT" && $2 == "2" && $3 == kind { count++ } END { print count + 0 }' "$file"
}

sha256_file() {
  file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{ print $1 }'
  else
    shasum -a 256 "$file" | awk '{ print $1 }'
  fi
}

run_case() {
  cohort="$1"
  case_id="$2"
  repository="$3"
  url="$4"
  revision="$5"
  language="$6"
  mode="$7"
  selection="$8"
  maximum="$9"
  license="${10}"
  case_dir="$out_dir/cases/$case_id"
  repo_dir="$cache_dir/repos/$repository"
  sources="$case_dir/sources.txt"
  facts="$case_dir/facts.tsv"
  diagnostics="$case_dir/diagnostics.txt"
  status="PASS"

  echo "==> $cohort/$case_id"
  mkdir -p "$case_dir"
  if ! prepare_repo "$repository" "$url" "$revision"; then
    status="FAIL"
    printf 'repository preparation failed\n' > "$diagnostics"
    tool_status=125
    source_count=0
    : > "$facts"
  elif [ ! -f "$repo_dir/$license" ]; then
    status="FAIL"
    printf 'declared license does not exist: %s\n' "$license" > "$diagnostics"
    tool_status=125
    source_count=0
    : > "$facts"
  elif ! select_sources "$repo_dir" "$language" "$mode" "$selection" "$maximum" "$sources"; then
    status="FAIL"
    printf 'source selection failed\n' > "$diagnostics"
    tool_status=125
    source_count=0
    : > "$facts"
  else
    source_count="$(awk 'END { print NR + 0 }' "$sources")"
    parser_args=(--keep-going)
    if [ "$language" = "c" ]; then
      parser_args+=(--cflag=-std=gnu17)
    else
      parser_args+=(--cflag=-std=gnu++17)
    fi
    parser_args+=(--cflag=-D_GNU_SOURCE=1)
    if [ -n "$resource_dir" ]; then
      parser_args+=("--cflag=-resource-dir=$resource_dir")
    fi
    if [ -n "$sysroot" ]; then
      parser_args+=(--cflag=-isysroot "--cflag=$sysroot")
    fi
    while IFS=$'\t' read -r context_repository context_flag; do
      [ "$context_repository" = "repository" ] && continue
      if [ "$context_repository" = "$repository" ]; then
        parser_args+=("--cflag=$context_flag")
      fi
    done < "$context_manifest"
    parser_args+=("--cflag=-I$repo_dir")
    for include_dir in include src test tests testcasesupport; do
      if [ -d "$repo_dir/$include_dir" ]; then
        parser_args+=("--cflag=-I$repo_dir/$include_dir")
      fi
    done
    while IFS= read -r source; do
      parser_args+=("--cflag=-I$repo_dir/$(dirname -- "$source")")
      parser_args+=("$source")
    done < "$sources"

    set +e
    (CDPATH='' cd "$repo_dir" && "$facts_tool" "${parser_args[@]}") > "$facts" 2> "$diagnostics"
    tool_status=$?
    set -e
  fi

  fact_count="$(awk -F '\t' '$1 == "P101FACT" && $2 == "2" { count++ } END { print count + 0 }' "$facts")"
  file_count="$(count_kind FILE "$facts")"
  include_count="$(count_kind INCLUDE "$facts")"
  function_count="$(count_kind FUNCTION "$facts")"
  call_count="$(count_kind CALL "$facts")"
  type_count="$(count_kind TYPE "$facts")"
  macro_count="$(count_kind MACRO "$facts")"
  note_count="$(count_kind NOTE "$facts")"
  source_macro_count=0
  if [ -s "$sources" ]; then
    while IFS= read -r source; do
      count="$(LC_ALL=C grep -c -E '^[[:space:]]*#[[:space:]]*define([[:space:]]|$)' "$repo_dir/$source" || true)"
      source_macro_count=$((source_macro_count + count))
    done < "$sources"
  fi

  if [ "$fact_count" -eq 0 ] || [ "$file_count" -eq 0 ]; then
    status="FAIL"
  fi
  case "$cohort" in
    good-c|good-cxx)
      if [ "$tool_status" -ne 0 ] && [ "$tool_status" -ne 2 ]; then
        status="FAIL"
      elif [ "$tool_status" -eq 2 ] && [ "$status" = "PASS" ]; then
        status="PASS-PARTIAL"
      fi
      semantic_count=$((function_count + call_count + type_count))
      if [ "$semantic_count" -lt $((source_count * 3)) ]; then
        printf 'semantic-density gate failed: %s semantic facts for %s sources\n' \
          "$semantic_count" "$source_count" >> "$diagnostics"
        status="FAIL"
      fi
      ;;
    *)
      if [ "$tool_status" -ne 0 ] && [ "$tool_status" -ne 2 ]; then
        status="FAIL"
      elif [ "$tool_status" -eq 2 ] && [ "$status" = "PASS" ]; then
        status="PASS-PARTIAL"
      fi
      ;;
  esac

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$status" "$cohort" "$case_id" "$repository" "$revision" "$source_count" \
    "$source_macro_count" "$tool_status" "$fact_count" "$file_count" "$include_count" \
    "$function_count" "$call_count" "$type_count" "$macro_count" "$note_count" >> "$results"
  printf '| %s | %s | %s | %s | %s | %s |\n' \
    "$status" "$cohort" "$case_id" "$source_count" "$tool_status" "$fact_count" >> "$summary"
  echo "    $status: sources=$source_count status=$tool_status facts=$fact_count"
}

matched=0
while IFS=$'\t' read -r cohort case_id repository url revision language mode selection maximum license; do
  [ "$cohort" = "cohort" ] && continue
  [ -z "$cohort_filter" ] || [ "$cohort" = "$cohort_filter" ] || continue
  [ -z "$case_filter" ] || [ "$case_id" = "$case_filter" ] || continue
  matched=$((matched + 1))
  run_case "$cohort" "$case_id" "$repository" "$url" "$revision" "$language" \
    "$mode" "$selection" "$maximum" "$license"
done < "$manifest"

if [ "$matched" -eq 0 ]; then
  echo "No manifest cases matched the requested filter." >&2
  exit 2
fi

failures="$(awk -F '\t' 'NR > 1 && $1 == "FAIL" { count++ } END { print count + 0 }' "$results")"
partial="$(awk -F '\t' 'NR > 1 && $1 == "PASS-PARTIAL" { count++ } END { print count + 0 }' "$results")"
ioccc_macros="$(awk -F '\t' 'NR > 1 && $2 == "ioccc" { total += $7 } END { print total + 0 }' "$results")"
if { [ -z "$cohort_filter" ] || [ "$cohort_filter" = "ioccc" ]; } &&
   [ -z "$case_filter" ] && [ "$ioccc_macros" -eq 0 ]; then
  echo "IOCCC cohort emitted no macro facts." >&2
  failures=$((failures + 1))
  printf '\nIOCCC macro coverage gate: **FAIL** (zero source macro definitions).\n' >> "$summary"
else
  printf '\nIOCCC source macro definitions exercised: **%s**.\n' "$ioccc_macros" >> "$summary"
fi

printf '\nCases: **%s**; partial: **%s**; failures: **%s**.\n' \
  "$matched" "$partial" "$failures" >> "$summary"
cat > "$out_dir/receipt.txt" <<EOF
schema=p101-c-facts-external-receipt-v1
manifest_sha256=$(sha256_file "$manifest")
parser_context_sha256=$(sha256_file "$context_manifest")
facts_tool=$facts_tool
facts_tool_sha256=$(sha256_file "$facts_tool")
clang=$facts_clang
clang_version=$("$facts_clang" --version | awk 'NR == 1 { print; exit }')
host=$(uname -s) $(uname -r) $(uname -m)
cases=$matched
partial=$partial
failures=$failures
EOF
echo "c-facts external corpus output: $out_dir"
echo "Summary: $summary"
if [ "$failures" -ne 0 ]; then
  exit 1
fi
