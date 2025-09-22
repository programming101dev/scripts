#!/usr/bin/env bash
# generate-flags.sh  — strict flag probing with -Werror
# - Reads flags from scripts/flags/*.txt (one or many per line; quotes OK)
# - Probes with -Werror (syntax-only for warning-like groups; -c for codegen/instr/sanitizers)
# - Treats "unused/unknown/unsupported/ignored" as NOT supported even if exit code is 0
# - Writes to .flags/<compiler-exe>/ and .flags/<CompilerID>/ lists for CMake
# - Cleans temp artifacts; logs details per compiler
set -euo pipefail

# ---------- paths ----------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
FLAGS_DIR="${SCRIPT_DIR}/flags"
OUT_DIR="${REPO_ROOT}/.flags"
C_LIST_FILE="${SCRIPT_DIR}/supported_c_compilers.txt"
CXX_LIST_FILE="${SCRIPT_DIR}/supported_cxx_compilers.txt"

mkdir -p "${OUT_DIR}"

# ---------- helpers ----------
trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

# read_flags_file <path> <array_name>
# - strips comments
# - removes ASCII/smart quotes
# - splits a line into tokens (multiple flags per line OK)
read_flags_file() {
  local path="$1" arr="$2" line tok
  eval "$arr=()"
  [[ -f "$path" ]] || return 0
  # shellcheck disable=SC2162
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(trim "$line")"
    [[ -z "$line" ]] && continue
    line="${line//$'\r'/}"
    line="${line//\"/}"; line="${line//\'/}"
    line="${line//“/}";  line="${line//”/}"
    line="${line//‘/}";  line="${line//’/}"
    for tok in $line; do
      [[ -n "$tok" ]] && eval "$arr+=(\"\$tok\")"
    done
  done < "$path"
}

# Decide probe mode from list name
probe_mode_for() {
  local name="$1"
  if [[ "$name" == *sanitizer* || "$name" == *code_generation* || "$name" == *instrumentation* ]]; then
    printf '%s' compile
  else
    printf '%s' syntax
  fi
}

# clang-ish?
is_clang_like() { [[ "$1" == *clang* || "$1" == *clang++* ]]; }

# crude compiler ID for .flags/Clang or .flags/GNU (matches your CMake usage)
compiler_id() {
  local cc="$1" v
  if "$cc" --version 2>/dev/null | grep -qi clang; then
    printf '%s' Clang
  else
    printf '%s' GNU
  fi
}

# Policy: fast reject obvious mismatches to avoid false positives
policy_force_reject() {
  local cc="$1" lang="$2" flag="$3"

  # language-specific std/warnings
  if [[ "$lang" == c && $flag == -std=c++* ]]; then return 0; fi
  if [[ "$lang" == c && $flag == -Wc++*  ]]; then return 0; fi
  if [[ "$lang" == c++ && $flag == -std=c* ]]; then return 0; fi

  # clang-only stuff on GCC, etc.
  if ! is_clang_like "$cc"; then
    case "$flag" in
      -fvtable-verify=*|-fvtv-*) return 0 ;;
      -fsanitize=shadow-call-stack|-fsanitize=safe-stack) return 0 ;;
    esac
  fi

  return 1
}

# Text that means "unsupported/ignored" (even if rc==0)
reject_patterns() {
  cat <<'PAT'
unknown option
unknown warning option
unknown argument
unrecognized option
unrecognized command line option
invalid argument
not supported
does not support
is valid for .* but not for .*
valid for .* but not for .*
argument unused during compilation
was ignored
ignoring unknown option
unsupported option
ignoring file .* not found
warning: .*option.* has no effect
warning: .*option.* is disabled
PAT
}

# Strict compile/syntax test with -Werror
classify_support() {
  local cc="$1" lang="$2" src="$3" flag="$4" mode="$5" log="$6" tmpout="$7"
  local srcdir srcbase rc=0
  srcdir="$(dirname "$src")"; srcbase="$(basename "$src")"

  if policy_force_reject "$cc" "$lang" "$flag"; then
    printf "  ❌ %s\n" "$flag"
    { printf '%s\n' "$flag rejected by policy gate"; echo "------------------------------"; } >>"$log"
    return 1
  fi

  local extra=(-Werror)
  if is_clang_like "$cc"; then
    extra+=(-Werror=unknown-warning-option -Werror=unused-command-line-argument)
  fi

  # helper to run and capture, then apply reject patterns
  run_and_check() {
    local cmd_desc="$1"; shift
    local rc_local=0
    : >"$tmpout"
    ( "$@" >"$tmpout" 2>&1 ) || rc_local=$?
    cat "$tmpout" >>"$log"
    if grep -Eiq "$(reject_patterns | paste -sd'|' -)" "$tmpout"; then
      rc_local=1
    fi
    return "$rc_local"
  }

  if [[ "$mode" == "compile" ]]; then
    # Phase 1: compile to object with the flag
    local obj="$TMP/obj.$RANDOM.o"
    run_and_check "compile" bash -lc \
      "cd '$srcdir' && '$cc' -x '$lang' -c -o '$obj' '$flag' './$srcbase' ${extra[*]}" || rc=1

    # Phase 2: link a tiny binary with the same flag
    # This is where Apple Clang reports 'argument unused during compilation' for -pg.
    if [[ $rc -eq 0 ]]; then
      local exe="$TMP/a.$RANDOM.out"
      run_and_check "link" bash -lc \
        "cd '$srcdir' && '$cc' -x '$lang' './$srcbase' -o '$exe' '$flag' ${extra[*]}" || rc=1
      rm -f "$exe"
    fi
    rm -f "$obj"

  elif [[ "$mode" == "link" ]]; then
    run_and_check "link" bash -lc \
      "cd '$srcdir' && '$cc' -x '$lang' './$srcbase' -o '$TMP/a.$RANDOM.out' '$flag' ${extra[*]}" || rc=1

  else # syntax
    run_and_check "syntax" bash -lc \
      "cd '$srcdir' && '$cc' -x '$lang' -fsyntax-only '$flag' './$srcbase' ${extra[*]}" || rc=1
  fi

  if [[ $rc -eq 0 ]]; then
    printf "  ✅ %s\n" "$flag"
    return 0
  else
    printf "  ❌ %s\n" "$flag"
    { printf '%s\n' "$flag is not supported"; echo "------------------------------"; } >>"$log"
    return 1
  fi
}

# Probe a list (Bash 3.2 compatible)
probe_flag_list() {
  local cc="$1" lang="$2" src="$3" listname="$4" tmpdir="$5"
  local len=0; eval "len=\${#$listname[@]}"; [[ "$len" -gt 0 ]] || return 0

  local cc_base; cc_base="$(basename "$cc")"
  local cc_id;   cc_id="$(compiler_id "$cc")"

  local out_cc="${OUT_DIR}/${cc_base}"
  local out_id="${OUT_DIR}/${cc_id}"
  local log="${out_cc}/${cc_base}-${lang}.log"

  mkdir -p "$out_cc" "$out_id"
  : >"$log"

  printf '  List: %s\n' "$listname"

  local supported=() i flag tmpout
  for ((i=0; i<len; i++)); do
    eval "flag=\${$listname[$i]}"; [[ -n "$flag" ]] || continue
    tmpout="${tmpdir}/probe_${listname}_${i}.log"; : >"$tmpout"
    if classify_support "$cc" "$lang" "$src" "$flag" "$(probe_mode_for "$listname")" "$log" "$tmpout"; then
      supported+=("$flag")
    fi
    rm -f "$tmpout"
  done

  # write results (space-separated; your CMake parser supports that)
  printf "%s" "$(IFS=" "; echo "${supported[*]}")" > "${out_cc}/${listname}.txt"
  printf "%s" "$(IFS=" "; echo "${supported[*]}")" > "${out_id}/${listname}.txt"
}

# ---------- tmp & sources ----------
mkd() { mktemp -d 2>/dev/null || mktemp -d -t flagprobe; }
TMP="$(mkd)"; trap 'rm -rf "$TMP" 2>/dev/null || true' EXIT

tmp_c_src="$TMP/probe.c";    printf 'int main(void){return 0;}\n' >"$tmp_c_src"
tmp_cxx_src="$TMP/probe.cpp";printf 'int main(){return 0;}\n'     >"$tmp_cxx_src"

# ---------- compilers ----------
read_list_file() {
  local f="$1" outvar="$2" line
  eval "$outvar=()"
  [[ -f "$f" ]] || return 0
  # shellcheck disable=SC2162
  while IFS= read -r line || [[ -n "${line:-}" ]]; do
    line="$(trim "${line:-}")"
    [[ -n "$line" ]] && eval "$outvar+=(\"\$line\")"
  done < "$f"
}

supported_c_compilers=()
supported_cxx_compilers=()
read_list_file "$C_LIST_FILE" supported_c_compilers
read_list_file "$CXX_LIST_FILE" supported_cxx_compilers

# ---------- load flags ----------
declare -a FLAG_LIST_NAMES=()
shopt -s nullglob
for f in "${FLAGS_DIR}"/*.txt; do
  base="$(basename "$f" .txt)"
  read_flags_file "$f" "$base"
  FLAG_LIST_NAMES+=("$base")
done
shopt -u nullglob

[[ ${#FLAG_LIST_NAMES[@]} -gt 0 ]] || { echo "No flag lists in ${FLAGS_DIR}/*.txt" >&2; exit 1; }

# ---------- run probes ----------
for cc in "${supported_c_compilers[@]}"; do
  echo "Checking: $cc [C]"
  out="${OUT_DIR}/$(basename "$cc")"; mkdir -p "$out"; rm -f "$out"/*
  idout="${OUT_DIR}/$(compiler_id "$cc")"; mkdir -p "$idout"; rm -f "$idout"/* || true
  for name in "${FLAG_LIST_NAMES[@]}"; do
    probe_flag_list "$cc" "c" "$tmp_c_src" "$name" "$TMP"
  done
done

for cc in "${supported_cxx_compilers[@]}"; do
  echo "Checking: $cc [C++]"
  out="${OUT_DIR}/$(basename "$cc")"; mkdir -p "$out"; rm -f "$out"/*
  idout="${OUT_DIR}/$(compiler_id "$cc")"; mkdir -p "$idout"; rm -f "$idout"/* || true
  for name in "${FLAG_LIST_NAMES[@]}"; do
    probe_flag_list "$cc" "c++" "$tmp_cxx_src" "$name" "$TMP"
  done
done

echo "Done. Results written under: ${OUT_DIR}/<compiler>/ and ${OUT_DIR}/<CompilerID>/"
