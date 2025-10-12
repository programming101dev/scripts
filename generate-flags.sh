#!/usr/bin/env bash
# generate-flags.sh — probe flags from scripts/flags/*.txt dynamically
# - No hard-coded list names; every *.txt in scripts/flags/ is processed
# - Phase inferred from filename:
#     *_link_flags.txt -> link
#     *code_generation*/*instrumentation_compiler*/*instrumentation_flags*/*sanitizer*/*safe_stack_flags.txt/*shadow_call_stack_flags.txt -> compile
#     everything else -> syntax
# - Strict: -Werror, and reject "unknown/unused/ignored" even with rc==0
# - Outputs ONLY to .flags/<compiler-exe>/
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
  local s="${1-}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

# read a flags file into an array (comments allowed, multiple tokens per line OK)
# usage: read_flags_file <path> <out_array_name>
read_flags_file() {
  local path="$1" out="$2" line tok
  eval "$out=()"
  [[ -f "$path" ]] || return 0
  # shellcheck disable=SC2162
  while IFS= read -r line || [[ -n "${line-}" ]]; do
    line="${line%%#*}"
    line="$(trim "${line-}")"
    [[ -z "$line" ]] && continue
    line="${line//$'\r'/}"
    line="${line//\"/}"; line="${line//\'/}"
    line="${line//“/}";  line="${line//”/}"
    line="${line//‘/}";  line="${line//’/}"
    for tok in $line; do
      [[ -n "$tok" ]] && eval "$out+=(\"\$tok\")"
    done
  done < "$path"
}

# infer probe mode from filename (portable lowercase)
probe_mode_for_file() {
  local fname="$1"
  local fname_lc
  fname_lc="$(printf '%s' "$fname" | tr '[:upper:]' '[:lower:]')"
  case "$fname_lc" in
    *_link_flags.txt) echo link; return ;;
  esac
  if [[ "$fname_lc" == *code_generation* \
     || "$fname_lc" == *instrumentation_compiler* \
     || "$fname_lc" == *instrumentation_flags* \
     || "$fname_lc" == *sanitizer* \
     || "$fname_lc" == *safe_stack_flags.txt \
     || "$fname_lc" == *shadow_call_stack_flags.txt ]]; then
    echo compile; return
  fi
  echo syntax
}

# clang-ish?
is_clang_like() { [[ "$1" == *clang* || "$1" == *clang++* ]]; }

# policy fast-reject to avoid false positives
policy_force_reject() {
  local cc="$1" lang="$2" flag="$3"
  if [[ "$lang" == c   && $flag == -std=c++* ]]; then return 0; fi
  if [[ "$lang" == c   && $flag == -Wc++*  ]]; then return 0; fi
  if [[ "$lang" == c++ && $flag == -std=c* ]]; then return 0; fi
  if ! is_clang_like "$cc"; then
    case "$flag" in
      -fvtable-verify=*|-fvtv-*) return 0 ;;
      -fsanitize=shadow-call-stack|-fsanitize=safe-stack) return 0 ;;
    esac
  fi
  return 1
}

# diagnostics that mean "unsupported/ignored" (even if rc==0)
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

# run a probe for a single flag
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

  run_and_check() {
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
    local obj="$TMP/obj.$RANDOM.o"
    run_and_check bash -lc \
      "cd '$srcdir' && '$cc' -x '$lang' -c -o '$obj' '$flag' './$srcbase' ${extra[*]}" || rc=1
    if [[ $rc -eq 0 ]]; then
      local exe="$TMP/a.$RANDOM.out"
      run_and_check bash -lc \
        "cd '$srcdir' && '$cc' -x '$lang' './$srcbase' -o '$exe' '$flag' ${extra[*]}" || rc=1
      rm -f "$exe"
    fi
    rm -f "$obj"

  elif [[ "$mode" == "link" ]]; then
    run_and_check bash -lc \
      "cd '$srcdir' && '$cc' -x '$lang' './$srcbase' -o '$TMP/a.$RANDOM.out' '$flag' ${extra[*]}" || rc=1

  else # syntax
    run_and_check bash -lc \
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

# probe one file (dynamically read + infer mode) — writes only under .flags/<compiler-exe>/
probe_flags_file() {
  local cc="$1" lang="$2" src="$3" file="$4" tmpdir="$5"

  local fname base mode
  fname="$(basename "$file")"
  base="${fname%.txt}"
  mode="$(probe_mode_for_file "$fname")"

  local flags=(); read_flags_file "$file" flags
  [[ ${#flags[@]} -gt 0 ]] || return 0

  local cc_base out_cc log
  cc_base="$(basename "$cc")"
  out_cc="${OUT_DIR}/${cc_base}"
  log="${out_cc}/${cc_base}-${lang}.log"

  mkdir -p "$out_cc"
  : >"$log"

  printf 'File: %s  (mode: %s)\n' "$fname" "$mode"

  local supported=() flag tmpout
  for flag in "${flags[@]}"; do
    tmpout="${tmpdir}/probe_${base}_$RANDOM.log"; : >"$tmpout"
    if classify_support "$cc" "$lang" "$src" "$flag" "$mode" "$log" "$tmpout"; then
      supported+=("$flag")
    fi
    rm -f "$tmpout"
  done

  # write results (space-separated list)
  printf "%s" "$(IFS=" "; echo "${supported[*]}")" > "${out_cc}/${base}.txt"
}

# ---------- tmp & tiny sources ----------
mkd() { mktemp -d 2>/dev/null || mktemp -d -t flagprobe; }
TMP="$(mkd)"; trap 'rm -rf "$TMP" 2>/dev/null || true' EXIT
tmp_c_src="$TMP/probe.c";      printf 'int main(void){return 0;}\n' >"$tmp_c_src"
tmp_cxx_src="$TMP/probe.cpp";  printf 'int main(){return 0;}\n'     >"$tmp_cxx_src"

# ---------- compilers ----------
read_list_file() {
  local f="$1" out="$2" line
  eval "$out=()"
  [[ -f "$f" ]] || return 0
  # shellcheck disable=SC2162
  while IFS= read -r line || [[ -n "${line-}" ]]; do
    line="$(trim "${line-}")"
    [[ -n "$line" ]] && eval "$out+=(\"\$line\")"
  done < "$f"
}

supported_c_compilers=()
supported_cxx_compilers=()
read_list_file "$C_LIST_FILE"   supported_c_compilers
read_list_file "$CXX_LIST_FILE" supported_cxx_compilers

if [[ ${#supported_c_compilers[@]} -eq 0 && ${#supported_cxx_compilers[@]} -eq 0 ]]; then
  echo "No compilers listed in:"
  echo "  $C_LIST_FILE"
  echo "  $CXX_LIST_FILE"
  exit 1
fi

# ---------- discover flags files dynamically ----------
shopt -s nullglob
flags_files=( "${FLAGS_DIR}"/*.txt )
shopt -u nullglob
[[ ${#flags_files[@]} -gt 0 ]] || { echo "No flags files found in ${FLAGS_DIR}/" >&2; exit 1; }

# ---------- run probes ----------
for cc in "${supported_c_compilers[@]}"; do
  echo "Checking: $cc [C]"
  out="${OUT_DIR}/$(basename "$cc")"; mkdir -p "$out"; rm -f "$out"/* || true
  for f in "${flags_files[@]}"; do
    probe_flags_file "$cc" "c" "$tmp_c_src" "$f" "$TMP"
  done
done

for cc in "${supported_cxx_compilers[@]}"; do
  echo "Checking: $cc [C++]"
  out="${OUT_DIR}/$(basename "$cc")"; mkdir -p "$out"; rm -f "$out"/* || true
  for f in "${flags_files[@]}"; do
    probe_flags_file "$cc" "c++" "$tmp_cxx_src" "$f" "$TMP"
  done
done

echo "Done. Results written under: ${OUT_DIR}/<compiler-exe>/"
