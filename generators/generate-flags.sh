#!/usr/bin/env bash
# generate-flags.sh — probe flags from scripts/flags/*.txt dynamically
# - No hard-coded list names; every *.txt in scripts/flags/ is processed
# - Phase inferred from filename:
#     *_link_flags.txt -> link
#     *code_generation*/*hardening_compiler*/*coverage*/*profile*/*sanitizer* -> compile
#     everything else -> syntax
# - Strict: -Werror, and reject "unknown/unused/ignored" even with rc==0
# - Outputs ONLY to .flags/<compiler-exe>/
set -euo pipefail

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
  *" --help "*|*" -h "*)
    cat <<'P101_USAGE'
generate-flags.sh — probe flags from scripts/flags/*.txt dynamically
- No hard-coded list names; every *.txt in scripts/flags/ is processed
- Phase inferred from filename:
    *_link_flags.txt -> link
    *code_generation*/*hardening_compiler*/*coverage*/*profile*/*sanitizer* -> compile
    everything else -> syntax
- Strict: -Werror, and reject "unknown/unused/ignored" even with rc==0
- Outputs ONLY to .flags/<compiler-exe>/

Options:
  -C file   C compiler list,   default supported_c_compilers.txt
  -X file   C++ compiler list, default supported_cxx_compilers.txt
P101_USAGE
    exit 0 ;;
esac

c_list_file="supported_c_compilers.txt"
cxx_list_file="supported_cxx_compilers.txt"

while getopts ":C:X:h" opt; do
  case "$opt" in
    C) c_list_file="$OPTARG" ;;
    X) cxx_list_file="$OPTARG" ;;
    h) exit 0 ;;
    \?|:) printf 'Usage: %s [-C c-list] [-X cxx-list]\n' "$0" >&2; exit 2 ;;
  esac
done

# ---------- paths ----------
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
# Profile selection (P101_FLAGS_PROFILE, set by update.sh --standard): the
# 'standard' tier probes flags-standard/ into .flags-standard/ so the maximal
# flags/ + .flags/ are never disturbed. Default profile = maximal.
if [[ "${P101_FLAGS_PROFILE:-}" == "standard" ]]; then
  FLAGS_DIR="${SCRIPT_DIR}/flags-standard"
  OUT_DIR="${REPO_ROOT}/.flags-standard"
else
  FLAGS_DIR="${SCRIPT_DIR}/flags"
  OUT_DIR="${REPO_ROOT}/.flags"
fi
resolve_list_file() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$SCRIPT_DIR" "$1" ;;
  esac
}

C_LIST_FILE="$(resolve_list_file "$c_list_file")"
CXX_LIST_FILE="$(resolve_list_file "$cxx_list_file")"
MAP_FILE="${SCRIPT_DIR}/compiler_paths.txt"
COMPILER_FINGERPRINT_SH="${SCRIPT_DIR}/workspace/compiler-fingerprint.sh"

mkdir -p "${OUT_DIR}"
[[ -x "${COMPILER_FINGERPRINT_SH}" ]] || {
  echo "Error: compiler fingerprint helper is missing or not executable: ${COMPILER_FINGERPRINT_SH}" >&2
  exit 1
}

# Resolve a compiler NAME to its pinned path (compiler_paths.txt written by
# check-compilers.sh); falls back to PATH. Results are keyed by NAME so the
# .flags/<name>/ cache matches what users and CMake see.
map_lookup() {
  local name="$1" line
  [[ -f "$MAP_FILE" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in "$name="*) printf '%s' "${line#*=}"; return 0 ;; esac
  done < "$MAP_FILE"
  return 1
}

resolve_name() {
  local v="$1" p
  case "$v" in /*) printf '%s' "$v"; return 0 ;; esac
  if p="$(map_lookup "$v")" && [[ -x "$p" ]]; then printf '%s' "$p"; return 0; fi
  command -v "$v" 2>/dev/null
}

# ---------- helpers ----------
trim() {
  local s="${1-}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

# ---------- per-family override deny-lists ----------
# render-flags.py can mark a selected flag as off for one compiler family
# (flag-selection.json: "gcc": false / "clang": false). That renders into
# flags/overrides-<family>.txt; any probe unit containing a denied token is
# skipped for compilers of that family.
compiler_family() {
  # gcc or clang, from the binary itself
  if "$1" --version 2>/dev/null | head -n 1 | grep -qi clang; then
    printf 'clang'
  else
    printf 'gcc'
  fi
}

# A probe unit can be denied by two independent scopes: the compiler FAMILY
# (gcc/clang, from overrides-gcc.txt / overrides-clang.txt) and the LANGUAGE
# being probed (c/cxx, from overrides-c.txt / overrides-cxx.txt). Language
# scope is what lets a flag be C++-only even within one family — e.g. clang
# compiles C while clang++ compiles C++, both clang-family, so "clang" can't
# separate them but "c" can. Both lists are consulted; a token in either is
# skipped.
FAMILY_DENY=""   # space-padded token list for the compiler family
LANG_DENY=""     # space-padded token list for the language being probed

_read_deny() {
  # $1 = scope name (gcc|clang|c|cxx); echoes a leading-space token list
  local f="${FLAGS_DIR}/overrides-${1}.txt" line acc=""
  [[ -f "$f" ]] || { printf ''; return 0; }
  while IFS= read -r line || [[ -n "${line-}" ]]; do
    line="$(trim "${line%%#*}")"
    [[ -n "$line" ]] && acc="$acc $line"
  done < "$f"
  printf '%s' "$acc"
}

load_family_deny() {
  # $1 = gcc|clang
  FAMILY_DENY="$(_read_deny "$1") "
}

load_lang_deny() {
  # $1 = probe language token ("c" or "c++"); c++ maps to the cxx override file
  local scope="$1"
  [[ "$scope" == "c++" ]] && scope="cxx"
  LANG_DENY="$(_read_deny "$scope") "
}

unit_denied() {
  # $1 = whole probe unit; true if ANY of its tokens is denied by family OR language
  local tok
  for tok in $1; do
    case "$FAMILY_DENY" in *" $tok "*) return 0 ;; esac
    case "$LANG_DENY"   in *" $tok "*) return 0 ;; esac
  done
  return 1
}

# read a flags file into an array (comments allowed)
# Each LINE is ONE probe unit: a line may hold several tokens that only make
# sense together (e.g. "-fsanitize=cfi -flto -fvisibility=hidden") and they
# are probed — and later emitted — as a group. Single-flag lines behave
# exactly as before.
# Result is returned in READ_FLAGS_RESULT. A fixed result variable avoids
# evaluating file contents as shell code while remaining compatible with the
# Bash 3.2 shipped by macOS.
READ_FLAGS_RESULT=()
read_flags_file() {
  local path="$1" line
  READ_FLAGS_RESULT=()
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
    # collapse internal whitespace so the group joins/splits cleanly
    line="$(printf '%s' "$line" | tr -s ' \t' ' ')"
    line="$(trim "$line")"
    [[ -n "$line" ]] && READ_FLAGS_RESULT+=("$line")
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
  # (safe_stack / shadow_call_stack are covered by *sanitizer* since their
  # files were renamed to <name>_sanitizer_flags.txt — the name CMake's
  # SANITIZER_LIST loader actually looks for)
  if [[ "$fname_lc" == *code_generation* \
     || "$fname_lc" == *hardening_compiler* \
     || "$fname_lc" == *coverage* \
     || "$fname_lc" == *profile* \
     || "$fname_lc" == *sanitizer* ]]; then
    echo compile; return
  fi
  echo syntax
}

# clang-ish?
is_clang_like() { [[ "$1" == *clang* || "$1" == *clang++* ]]; }

# policy fast-reject to avoid false positives (applied per token)
policy_force_reject() {
  local cc="$1" lang="$2" flag="$3"
  if [[ "$lang" == c   && $flag == -std=c++* ]]; then return 0; fi
  if [[ "$lang" == c   && $flag == -Wc++*  ]]; then return 0; fi
  if [[ "$lang" == c++ && $flag == -std=c* ]]; then return 0; fi
  if is_clang_like "$cc"; then
    # vtable verification is a GCC feature (needs libvtv); clang does not
    # implement it — the old gate had this backwards.
    case "$flag" in
      -fvtable-verify=*|-fvtv-*) return 0 ;;
    esac
  else
    # These sanitizers and driver-warning suppressions are clang-only.
    # GCC normally stays silent about an unknown -Wno-* option until some
    # other diagnostic is emitted, so a clean compile probe otherwise records
    # a false positive and cc1 later prints an "unrecognized option" note.
    case "$flag" in
      -fsanitize=shadow-call-stack|-fsanitize=safe-stack|\
      -Wno-poison-system-directories|\
      -Wno-invalid-command-line-argument|\
      -Wno-unused-command-line-argument) return 0 ;;
    esac
  fi
  return 1
}

# diagnostics that mean "unsupported/ignored" (even if rc==0)
# (kept as a function for readability; combined into REJECT_RE once below)
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

# Build the combined regex once (BSD and GNU paste both accept -s -d)
REJECT_RE="$(reject_patterns | paste -s -d '|' -)"

# run a probe for one flag group (a group is one line from the flags file:
# usually a single flag, sometimes several that only work together)
classify_support() {
  local cc="$1" lang="$2" src="$3" flag="$4" mode="$5" log="$6" tmpout="$7"
  local rc=0

  # split the group into argv tokens (bash 3.2-safe)
  local ftoks=()
  IFS=' ' read -r -a ftoks <<< "$flag"

  local tok
  for tok in "${ftoks[@]}"; do
    if policy_force_reject "$cc" "$lang" "$tok"; then
      printf "  ❌ %s\n" "$flag"
      { printf '%s\n' "$flag rejected by policy gate ($tok)"; echo "------------------------------"; } >>"$log"
      return 1
    fi
  done

  local extra=(-Werror)
  if is_clang_like "$cc"; then
    extra+=(-Werror=unknown-warning-option -Werror=unused-command-line-argument)
  fi

  # Invoke the compiler directly (argv), never via an interpolated shell
  # string: no quoting pitfalls with odd flags, and no `bash -l` login-shell
  # startup cost / PATH surprises on every probe. Run with cwd=$TMP so any
  # cwd-relative side outputs (-save-temps, coverage notes, ...) land in the
  # temp dir, not wherever the caller happens to be.
  run_and_check() {
    local rc_local=0
    : >"$tmpout"
    ( cd "$TMP" && "$@" ) >"$tmpout" 2>&1 || rc_local=$?
    cat "$tmpout" >>"$log"
    if grep -Eiq "$REJECT_RE" "$tmpout"; then
      rc_local=1
    fi
    return "$rc_local"
  }

  if [[ "$mode" == "compile" ]]; then
    local obj="$TMP/obj.$RANDOM.o"
    run_and_check "$cc" -x "$lang" -c -o "$obj" "${ftoks[@]}" "$src" "${extra[@]}" || rc=1
    if [[ $rc -eq 0 ]]; then
      local exe="$TMP/a.$RANDOM.out"
      run_and_check "$cc" -x "$lang" "$src" -o "$exe" "${ftoks[@]}" "${extra[@]}" || rc=1
      rm -f "$exe"
    fi
    rm -f "$obj"

  elif [[ "$mode" == "link" ]]; then
    local exe="$TMP/a.$RANDOM.out"
    run_and_check "$cc" -x "$lang" "$src" -o "$exe" "${ftoks[@]}" "${extra[@]}" || rc=1
    rm -f "$exe"

  else # syntax
    run_and_check "$cc" -x "$lang" -fsyntax-only "${ftoks[@]}" "$src" "${extra[@]}" || rc=1
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

# Runtime smoke test for INSTRUMENTATION buckets only (coverage / profile).
# A compile/link probe only proves the toolchain ACCEPTS a flag; it cannot see
# that the flag's whole purpose is a RUNTIME artifact (.gcda for coverage,
# gmon.out for profiling). Build + RUN a trivial program with the surviving
# flags and confirm the artifact appears; if it compiles but produces nothing
# (e.g. -pg on a target with no gmon.out runtime) the flag is useless -> drop
# the bucket. Skipped when the probe host cannot run the target's binaries
# (cross-compile), where compile-acceptance is all we have.
#   args: cc lang "<flags>" <artifact-glob>   return: 0 = keep, 1 = drop
runtime_smoke_ok() {
  local cc="$1" lang="$2" flags="$3" artifact="$4" d src
  d="$(mktemp -d 2>/dev/null || mktemp -d -t smoke)" || return 0
  src="$d/s.c"
  if [[ "$lang" == "c++" ]]; then src="$d/s.cpp"; fi
  printf 'int main(void){return 0;}\n' >"$src"
  # host must run a freshly built PLAIN binary; else assume cross-compile -> keep
  if ! ( cd "$d" && "$cc" -x "$lang" "$src" -o base >/dev/null 2>&1 && ./base >/dev/null 2>&1 ); then
    rm -rf "$d"; return 0
  fi
  # build WITH the instrumentation set; a combined-build failure is inconclusive
  # (not proof of breakage) -> keep the compile-verified flags
  if ! ( cd "$d" && "$cc" -x "$lang" $flags "$src" -o inst >/dev/null 2>&1 ); then
    rm -rf "$d"; return 0
  fi
  ( cd "$d" && ./inst >/dev/null 2>&1 || true )
  if ( cd "$d" && ls $artifact >/dev/null 2>&1 ); then
    rm -rf "$d"; return 0
  fi
  rm -rf "$d"; return 1
}

# probe one file (dynamically read + infer mode) — writes only under .flags/<name>/
# Args: <name (cache key)> <path (binary to run)> <lang> <src> <file> <tmpdir>
probe_flags_file() {
  local cc_name="$1" cc="$2" lang="$3" src="$4" file="$5" tmpdir="$6"

  local fname base mode
  fname="$(basename "$file")"
  base="${fname%.txt}"
  mode="$(probe_mode_for_file "$fname")"

  local flags=()
  read_flags_file "$file"
  flags=("${READ_FLAGS_RESULT[@]}")
  [[ ${#flags[@]} -gt 0 ]] || return 0

  local cc_base out_cc log
  cc_base="$(basename "$cc_name")"
  out_cc="${OUT_DIR}/${cc_base}"
  log="${out_cc}/${cc_base}-${lang}.log"

  mkdir -p "$out_cc"
  # Append to the per-compiler log (the compiler loop rm -f's the dir before
  # probing); truncating here would keep only the LAST flags file's output.
  touch "$log"

  printf 'File: %s  (mode: %s)\n' "$fname" "$mode"

  local supported=() flag tmpout
  for flag in "${flags[@]}"; do
    if unit_denied "$flag"; then
      printf "  ⛔ %s (family override)\n" "$flag"
      continue
    fi
    tmpout="${tmpdir}/probe_${base}_$RANDOM.log"; : >"$tmpout"
    if classify_support "$cc" "$lang" "$src" "$flag" "$mode" "$log" "$tmpout"; then
      supported+=("$flag")
    fi
    rm -f "$tmpout"
  done

  # Runtime smoke test for instrumentation buckets: confirm the surviving flags
  # actually produce their runtime artifact, not just that they compiled.
  case "$base" in
    coverage_flags|profile_flags)
      if [[ ${#supported[@]} -gt 0 ]]; then
        local _art
        if [[ "$base" == "coverage_flags" ]]; then _art='*.gcda'; else _art='gmon.out'; fi
        if runtime_smoke_ok "$cc" "$lang" "${supported[*]}" "$_art"; then
          printf '  smoke: %s produced runtime artifact (kept)\n' "$base"
        else
          printf '  smoke: %s compiled but produced no runtime artifact -> dropped\n' "$base"
          { printf '%s\n' "$base: accepted at compile but no runtime artifact on this target -> dropped"; echo "------------------------------"; } >>"$log"
          supported=()
        fi
      fi
      ;;
  esac

  # write results (space-separated list)
  # ("${supported[*]:-}": expanding an empty array under set -u errors on
  # the stock macOS bash 3.2)
  printf "%s" "${supported[*]:-}" > "${out_cc}/${base}.txt"
}

# ---------- tmp & tiny sources ----------
mkd() { mktemp -d 2>/dev/null || mktemp -d -t flagprobe; }
TMP="$(mkd)"; trap 'rm -rf "$TMP" 2>/dev/null || true' EXIT
# The C probe emits global data and a relocation-bearing external reference, so
# the shared-object whole-set check catches executable-only codegen flags such
# as -fPIE on ELF platforms. A flag that breaks only on a specific C construct
# (e.g. -fharden-control-flow-redundancy on functions calling setjmp) is handled
# per-file in the build (P101_FILE_FLAG_OPTOUTS) so it stays on everywhere else.
tmp_c_src="$TMP/probe.c"
cat > "$tmp_c_src" <<'P101_C_PROBE_EOF'
int p101_probe_global;
extern int p101_probe_external(void);

int p101_probe_external(void)
{
    return p101_probe_global;
}

int main(void)
{
    return p101_probe_global + p101_probe_external();
}
P101_C_PROBE_EOF
# The C++ probe is REPRESENTATIVE: it defines and uses a polymorphic class, so
# it actually emits a vtable. That makes the probe reflect the real toolchain
# for codegen flags that only fail once a vtable exists — notably
# -fvtable-verify, whose emitted ".vtable_map_vars" section the macOS assembler
# rejects. On macOS the probe therefore drops such flags; on Linux (where they
# work) it keeps them. Verified clean under maximal C++ warnings, so it never
# false-rejects a good flag.
cat > "$TMP/probe.cpp" <<'P101_CXX_PROBE_EOF'
struct P101ProbeBase
{
    virtual ~P101ProbeBase() = default;
    virtual int value() const;
};

struct P101ProbeDerived final : P101ProbeBase
{
    int value() const override;
};

int P101ProbeBase::value() const
{
    return 0;
}

int P101ProbeDerived::value() const
{
    return 1;
}

int main()
{
    const P101ProbeBase &ref = P101ProbeDerived();

    return ref.value();
}
P101_CXX_PROBE_EOF
tmp_cxx_src="$TMP/probe.cpp"

# ---------- compilers ----------
READ_LIST_RESULT=()
read_list_file() {
  local f="$1" line
  READ_LIST_RESULT=()
  [[ -f "$f" ]] || return 0
  # shellcheck disable=SC2162
  while IFS= read -r line || [[ -n "${line-}" ]]; do
    line="$(trim "${line-}")"
    [[ -n "$line" ]] && READ_LIST_RESULT+=("$line")
  done < "$f"
}

supported_c_compilers=()
supported_cxx_compilers=()
read_list_file "$C_LIST_FILE"
if [[ ${#READ_LIST_RESULT[@]} -gt 0 ]]; then
  supported_c_compilers=("${READ_LIST_RESULT[@]}")
fi
read_list_file "$CXX_LIST_FILE"
if [[ ${#READ_LIST_RESULT[@]} -gt 0 ]]; then
  supported_cxx_compilers=("${READ_LIST_RESULT[@]}")
fi

if [[ ${#supported_c_compilers[@]} -eq 0 && ${#supported_cxx_compilers[@]} -eq 0 ]]; then
  echo "No compilers listed in:"
  echo "  $C_LIST_FILE"
  echo "  $CXX_LIST_FILE"
  exit 1
fi

# Refuse overlapping basenames between the two lists: results live in
# .flags/<basename>/ with identical file names for both languages, so the
# C++ pass would silently overwrite the C results (and vice versa).
if [[ ${#supported_c_compilers[@]} -gt 0 && ${#supported_cxx_compilers[@]} -gt 0 ]]; then
  for _c in "${supported_c_compilers[@]}"; do
    for _x in "${supported_cxx_compilers[@]}"; do
      if [[ "$(basename "$_c")" == "$(basename "$_x")" ]]; then
        echo "Error: '$(basename "$_c")' appears in both $C_LIST_FILE and $CXX_LIST_FILE." >&2
        echo "Both languages would write to .flags/$(basename "$_c")/ and clobber each other." >&2
        echo "Use distinct driver names (e.g. clang vs clang++)." >&2
        exit 1
      fi
    done
  done
fi

# ---------- discover flags files dynamically ----------
shopt -s nullglob
flags_files=()
for _ff in "${FLAGS_DIR}"/*.txt; do
  case "$(basename "$_ff")" in overrides-*.txt) continue ;; esac
  flags_files+=("$_ff")
done
shopt -u nullglob
[[ ${#flags_files[@]} -gt 0 ]] || { echo "No flags files found in ${FLAGS_DIR}/" >&2; exit 1; }

# ---------- whole-set mutual-exclusion check ----------
# Flags are probed ONE AT A TIME, so two flags that each work alone but
# refuse to coexist (mutual exclusion the driver enforces) slip through
# individual probes. After probing, compile ONE program with the entire
# accepted non-sanitizer set (plus the sanitizers.txt selection, the same
# composition the real build uses). If the sum fails where every part
# passed, a linear bisect names the offending flag; the conflict is
# recorded in .flags/<name>/conflicts.txt and the flag is REMOVED from the
# cache so the build stays viable, never silently broken.
whole_set_check() {
  # $1=name $2=path $3=lang $4=src
  local name="$1" cc="$2" lang="$3" src="$4"
  local out_cc
  out_cc="${OUT_DIR}/$(basename "$name")"
  local all="" f base tok exe="$TMP/ws.out" so="$TMP/ws.so" errlog="$TMP/ws.err"
  local sel="" g gfile

  # non-sanitizer accepted flags, in cache order
  for f in "$out_cc"/*.txt; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f")"
    case "$base" in *_sanitizer_flags.txt|*.log) continue ;; esac
    all="$all $(cat "$f")"
  done
  # plus the currently selected sanitizer groups, like the real build
  if [[ -f "${SCRIPT_DIR}/sanitizers.txt" ]]; then
    sel="$(head -n 1 "${SCRIPT_DIR}/sanitizers.txt" 2>/dev/null || true)"
    local IFS_saved="$IFS"; IFS=','
    for g in $sel; do
      IFS="$IFS_saved"
      g="$(trim "$g")"
      gfile="$out_cc/${g}_sanitizer_flags.txt"
      [[ -n "$g" && -f "$gfile" ]] && all="$all $(cat "$gfile")"
      IFS=','
    done
    IFS="$IFS_saved"
  fi
  [[ -n "${all// /}" ]] || return 0

  # shellcheck disable=SC2086
  if ( cd "$TMP" && "$cc" $all "$src" -o "$exe" ) >"$errlog" 2>&1; then
    # p101 libraries are shared libraries, so also prove the accepted compile
    # flag set can produce a shared object. This catches executable-only codegen
    # flags such as -fPIE that can pass an executable probe but break .so links.
    # shellcheck disable=SC2086
    if ( cd "$TMP" && "$cc" -shared -fPIC $all "$src" -o "$so" ) >>"$errlog" 2>&1; then
      return 0
    fi
  fi

  echo "  ⚠️  whole-set check FAILED for $name — bisecting for mutually exclusive flags..."
  whole_set_candidate_check() {
    local candidate_flags="$1"
    # shellcheck disable=SC2086
    ( cd "$TMP" && "$cc" $candidate_flags "$src" -o "$exe" ) >/dev/null 2>&1 || return 1
    # shellcheck disable=SC2086
    ( cd "$TMP" && "$cc" -shared -fPIC $candidate_flags "$src" -o "$so" ) >/dev/null 2>&1
  }

  # phase 1 — single-drop: drop one token at a time until the set compiles.
  # Finds conflicts where ONE flag is the odd one out. Candidate sets must
  # produce both an executable and a shared object, matching the final gate.
  local conflicts="" keep tokens t candidate
  tokens="$all"
  while :; do
    candidate=""
    for t in $tokens; do
      keep=""
      for tok in $tokens; do
        [[ "$tok" == "$t" ]] || keep="$keep $tok"
      done
      if whole_set_candidate_check "$keep"; then
        candidate="$t"
        break
      fi
    done
    [[ -n "$candidate" ]] || break
    conflicts="$conflicts $candidate"
    keep=""
    for tok in $tokens; do
      [[ "$tok" == "$candidate" ]] || keep="$keep $tok"
    done
    tokens="$keep"
    whole_set_candidate_check "$tokens" && break
  done

  # phase 2 — greedy forward-build: when single-drop can't fix it (three
  # mutually exclusive debug formats: removing any ONE still leaves a
  # conflicting pair), rebuild from the front, dropping each token that
  # breaks the accumulated set. First-listed of an exclusive group wins,
  # matching last-one-wins expectations as closely as a keep-set can.
  if ! whole_set_candidate_check "$tokens"; then
    echo "  ⚠️  single-drop insufficient — greedy forward-build..."
    keep=""
    for t in $tokens; do
      if whole_set_candidate_check "$keep $t"; then
        keep="$keep $t"
      else
        conflicts="$conflicts $t"
      fi
    done
    tokens="$keep"
  fi

  {
    echo "# Mutually exclusive / whole-set conflicts for $name ($lang)."
    echo "# Each flag below passed its INDIVIDUAL probe but breaks the"
    echo "# combined command line; it was removed from this cache."
    echo "# Original combined error:"
    sed 's/^/#   | /' "$errlog" | head -8
    for t in $conflicts; do printf '%s\n' "$t"; done
  } >"$out_cc/conflicts.txt"

  if [[ -n "${conflicts// /}" ]]; then
    echo "  ⚠️  removed from $name cache (see .flags/$(basename "$name")/conflicts.txt):"
    for t in $conflicts; do
      echo "      $t"
      for f in "$out_cc"/*.txt; do
        base="$(basename "$f")"
        case "$base" in conflicts.txt|*.log) continue ;; esac
        if grep -q -- "$t" "$f" 2>/dev/null; then
          # rewrite the cache file without the conflicting token
          local rebuilt="" tok2
          for tok2 in $(cat "$f"); do
            [[ "$tok2" == "$t" ]] || rebuilt="$rebuilt $tok2"
          done
          printf '%s' "${rebuilt# }" >"$f"
        fi
      done
    done
  else
    echo "  ⚠️  bisect could not isolate a single flag — see $out_cc/conflicts.txt"
  fi
}

# ---------- run probes ----------
# (count guards: expanding an empty array under set -u errors on the stock
# macOS bash 3.2)
if [[ ${#supported_c_compilers[@]} -gt 0 ]]; then
  for cc in "${supported_c_compilers[@]}"; do
    cc_path="$(resolve_name "$cc")" || { echo "WARN: cannot resolve '$cc'; skipping." >&2; continue; }
    echo "Checking: $cc [C] ($cc_path)"
    load_family_deny "$(compiler_family "$cc_path")"
    load_lang_deny "c"
    out="${OUT_DIR}/$(basename "$cc")"; mkdir -p "$out"
    rm -f "$out"/* "$out"/.compiler-fingerprint || true
    for f in "${flags_files[@]}"; do
      probe_flags_file "$cc" "$cc_path" "c" "$tmp_c_src" "$f" "$TMP"
    done
    whole_set_check "$cc" "$cc_path" "c" "$tmp_c_src"
    "${COMPILER_FINGERPRINT_SH}" write "$cc_path" "$out/.compiler-fingerprint"
  done
fi

if [[ ${#supported_cxx_compilers[@]} -gt 0 ]]; then
  for cc in "${supported_cxx_compilers[@]}"; do
    cc_path="$(resolve_name "$cc")" || { echo "WARN: cannot resolve '$cc'; skipping." >&2; continue; }
    echo "Checking: $cc [C++] ($cc_path)"
    load_family_deny "$(compiler_family "$cc_path")"
    load_lang_deny "c++"
    out="${OUT_DIR}/$(basename "$cc")"; mkdir -p "$out"
    rm -f "$out"/* "$out"/.compiler-fingerprint || true
    for f in "${flags_files[@]}"; do
      probe_flags_file "$cc" "$cc_path" "c++" "$tmp_cxx_src" "$f" "$TMP"
    done
    whole_set_check "$cc" "$cc_path" "c++" "$tmp_cxx_src"
    "${COMPILER_FINGERPRINT_SH}" write "$cc_path" "$out/.compiler-fingerprint"
  done
fi

echo "Done. Results written under: ${OUT_DIR}/<compiler-exe>/"
