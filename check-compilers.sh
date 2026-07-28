#!/usr/bin/env bash
set -euo pipefail

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
  *" --help "*|*" -h "*)
    cat <<'P101_USAGE'
check-compilers.sh — takes no command-line options; run with no arguments.
P101_USAGE
    exit 0 ;;
esac

# Always operate from the directory this script lives in, so the outputs
# land in the scripts repo.
CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Detect the operating system
OS="$(uname)"

# ---------------------------------------------------------------------------
# Durable compiler discovery.
#
# Outputs:
#   supported_c_compilers.txt / supported_cxx_compilers.txt — compiler NAMES,
#     one per line (what users type, what update-all.sh pairs, what child
#     repos see via symlinks)
#   compiler_paths.txt — the pinned NAME=ABSOLUTE-PATH mapping. Everything
#     that executes a compiler resolves the name through this map first, so
#     a later PATH change cannot silently swap which binary a name means.
#     Comment lines record each binary's --version for drift diagnosis.
#
# Durability properties:
#   - discovery is by NAME PATTERN, not enumerated versions: gcc-\d, gcc\d
#     (FreeBSD), gcc-mp-\d (MacPorts), clang-\d, clang\d, clang-mp-\d, plus
#     the generic names — a new compiler release never requires an edit here
#   - the scan covers PATH plus known keg/unlinked locations (Homebrew llvm
#     and llvm@NN on macOS and Linuxbrew, MacPorts libexec, Debian
#     /usr/lib/llvm-N, FreeBSD /usr/local/llvmNN), so an uncommented shell
#     profile cannot hide an installed toolchain
#   - every candidate must compile a trivial program before being recorded
#   - generic and versioned aliases are both kept when they point at the same
#     physical binary, so Ubuntu's gcc and gcc-13 are both valid user-facing
#     compiler names
#   - a name is normally the binary's basename; first discovery wins (PATH
#     order first, keg dirs last), so /usr/bin/clang keeps the name "clang"
#     even when a keg also ships a plain clang
#   - a SECOND binary carrying an already-taken generic name (clang, gcc,
#     clang++, g++) gets a synthesized versioned name from its --version
#     major (a keg-only plain clang that is v22 becomes "clang-22"), so both
#     Apple clang AND Homebrew clang stay reachable
#   - Apple's /usr/bin/gcc and /usr/bin/g++ are excluded outright: they are
#     clang stubs, not GCC
#   - the whole state is regenerated from scratch on every run, and
#     update.sh re-runs this automatically whenever any pinned path stops
#     existing — the map self-heals after upgrades and uninstalls
# ---------------------------------------------------------------------------

MAP_FILE="compiler_paths.txt"
DISCOVERY_LOG="compiler-discovery.log"

c_patterns=(
  gcc "gcc-[0-9]*" "gcc[0-9]*" "gcc-mp-[0-9]*"
  clang "clang-[0-9]*" "clang[0-9]*" "clang-mp-[0-9]*" clang-devel
)
cxx_patterns=(
  g++ "g++-[0-9]*" "g++[0-9]*" "g++-mp-[0-9]*"
  clang++ "clang++-[0-9]*" "clang++[0-9]*" "clang++-mp-[0-9]*" clang++-devel
)

# Keg/unlinked locations searched in addition to PATH. PATH wins on name
# conflicts, so a system compiler is never shadowed by a keg.
extra_dirs=(
  /opt/homebrew/opt/llvm/bin
  /opt/homebrew/opt/llvm@*/bin
  /usr/local/opt/llvm/bin
  /usr/local/opt/llvm@*/bin
  /opt/local/libexec/llvm-*/bin
  /usr/lib/llvm-*/bin
  /usr/local/llvm*/bin
  /home/linuxbrew/.linuxbrew/opt/llvm/bin
  /home/linuxbrew/.linuxbrew/opt/llvm@*/bin
)

# Compile-test a compiler: writes a tiny main and checks it can produce an exe.
# On failure the compiler's actual error output is left in CAN_COMPILE_ERR so
# rejection is TRACKED, never silent — a broken compiler (missing SDK,
# botched install) shows up in compiler-discovery.log with its real error.
# (explicit cleanup instead of a RETURN trap — RETURN traps set inside a
# function persist after it returns and would re-fire on later returns)
CAN_COMPILE_ERR=""
_can_compile() {
  local cc="$1" lang="$2"
  local tmpdir rc out
  CAN_COMPILE_ERR=""
  tmpdir="$(mktemp -d 2>/dev/null || mktemp -d -t ccprobe)"

  local src="$tmpdir/t.$lang" exe="$tmpdir/a.out"
  if [[ "$lang" == "c" ]]; then
    printf 'int main(void){return 0;}\n' >"$src"
  else
    printf 'int main(){return 0;}\n' >"$src"
  fi

  rc=1
  if out="$("$cc" -x "$lang" "$src" -o "$exe" 2>&1)" && [[ -x "$exe" ]]; then
    rc=0
  else
    CAN_COMPILE_ERR="$out"
  fi
  rm -rf "$tmpdir" 2>/dev/null || true
  return "$rc"
}

_version_line() {
  "$1" --version 2>/dev/null | head -n 1
}

_version_major() {
  # first "version <N>" occurrence; empty if undetectable
  _version_line "$1" | sed -n -E 's/.*version ([0-9]+).*/\1/p'
}

_is_versioned_compiler_alias() {
  case "$1" in
    gcc-[0-9]*|gcc[0-9]*|gcc-mp-[0-9]*|g++-[0-9]*|g++[0-9]*|g++-mp-[0-9]*|clang-[0-9]*|clang[0-9]*|clang-mp-[0-9]*|clang++-[0-9]*|clang++[0-9]*|clang++-mp-[0-9]*)
      return 0 ;;
    *)
      return 1 ;;
  esac
}

# Probe candidates; write NAMES to supported_<type>.txt and NAME=PATH lines
# to the (already-truncated) map file.
# Args: <type> <lang> <patterns...>
probe_list() {
  local type="$1" lang="$2"; shift 2
  local out="supported_${type}.txt"
  : >"$out"

  # -- collect (name, path) candidates --
  local cand_names=() cand_paths=() seen_names=" " seen_paths=" "
  local d f base pattern real
  local search_dirs=()

  local IFS_saved="$IFS"
  IFS=':'
  for d in $PATH; do
    [[ -n "$d" && -d "$d" ]] && search_dirs+=("$d")
  done
  IFS="$IFS_saved"
  for d in "${extra_dirs[@]}"; do
    [[ -d "$d" ]] && search_dirs+=("$d")
  done

  add_candidate() {
    # $1 = path; assigns a free name or skips
    local p="$1" b name major
    b="$(basename "$p")"
    # Keep stable versioned aliases even when the generic name points at
    # the same physical binary. On Ubuntu, for example, gcc and gcc-13 are
    # often both intentional user-facing names for /usr/bin/gcc-13.
    real="$p"
    if command -v realpath >/dev/null 2>&1; then
      real="$(realpath "$p" 2>/dev/null || printf '%s' "$p")"
    fi
    case " $seen_paths " in
      *" $real "*)
        _is_versioned_compiler_alias "$b" || return 0
        ;;
    esac

    name="$b"
    case " $seen_names " in
      *" $b "*)
        # generic name already taken by an earlier (PATH-priority) binary:
        # synthesize a versioned name so this one stays reachable too
        case "$b" in
          gcc|g++|clang|clang++)
            major="$(_version_major "$p")"
            [[ -n "$major" ]] || return 0
            name="${b}-${major}"
            case " $seen_names " in *" $name "*) return 0 ;; esac
            ;;
          *) return 0 ;;
        esac
        ;;
    esac
    seen_names="$seen_names $name"
    seen_paths="$seen_paths $real"
    cand_names+=("$name")
    cand_paths+=("$p")
  }

  for d in "${search_dirs[@]}"; do
    for pattern in "$@"; do
      for f in "$d"/$pattern; do
        [[ -x "$f" && ! -d "$f" ]] || continue
        # Apple's /usr/bin/gcc and /usr/bin/g++ are clang stubs — skip them
        if [[ "$OS" == "Darwin" && ( "$f" == "/usr/bin/gcc" || "$f" == "/usr/bin/g++" ) ]]; then
          continue
        fi
        add_candidate "$f"
      done
    done
  done

  # -- compile-test each candidate; record name + pinned path;
  #    TRACK rejections with the compiler's own error output --
  local i=0 n name path
  n=${#cand_names[@]}
  while [[ $i -lt $n ]]; do
    name="${cand_names[$i]}"
    path="${cand_paths[$i]}"
    if ! _can_compile "$path" "$lang"; then
      echo "REJECTED: $name ($path) cannot compile a trivial $lang program — details in $DISCOVERY_LOG" >&2
      {
        printf '== REJECTED %s (%s) [%s]\n' "$name" "$path" "$lang"
        printf 'version: %s\n' "$(_version_line "$path")"
        printf 'error output:\n'
        printf '%s\n' "$CAN_COMPILE_ERR" | head -15 | sed 's/^/  | /'
        echo
      } >>"$DISCOVERY_LOG"
      i=$((i+1))
      continue
    fi
    # Synthesized names (name != binary basename): the shared
    # CMakeLists.txt keys its .flags cache by the compiler binary's
    # basename, so give the binary a local symlink bearing the
    # synthesized name and pin the map to that symlink.
    if [[ "$name" != "$(basename "$path")" ]]; then
      mkdir -p .compiler-links
      ln -sfn -- "$path" ".compiler-links/$name"
      path="$PWD/.compiler-links/$name"
    fi
    printf '%s\n' "$name" >>"$out"
    {
      printf '# %s: %s\n' "$name" "$(_version_line "$path")"
      printf '%s=%s\n' "$name" "$path"
    } >>"$MAP_FILE"
    i=$((i+1))
  done

  if [[ ! -s "$out" ]]; then
    echo "No working ${type} found. Wrote empty ${out}." >&2
    exit 1
  fi
  echo "Supported ${type} compilers written to ${out} (paths pinned in ${MAP_FILE}):"
  sed 's/^/  /' "$out"
}

{
  echo "# compiler_paths.txt — generated by check-compilers.sh; do not edit."
  echo "# NAME=ABSOLUTE_PATH; comment lines record each binary's --version."
} >"$MAP_FILE"

{
  echo "# compiler-discovery.log — generated by check-compilers.sh."
  echo "# Candidates found but REJECTED (failed to compile a trivial program),"
  echo "# each with the compiler's own error output. Empty = nothing rejected."
  echo
} >"$DISCOVERY_LOG"

probe_list "c_compilers"   "c"   "${c_patterns[@]}"
probe_list "cxx_compilers" "c++" "${cxx_patterns[@]}"

# Summarize rejections at the end so they can't scroll away unnoticed
if grep -q '^== REJECTED' "$DISCOVERY_LOG" 2>/dev/null; then
  echo
  echo "NOTE: some discovered compilers were rejected as broken:"
  grep '^== REJECTED' "$DISCOVERY_LOG" | sed 's/^== REJECTED /  - /'
  echo "  Full error output: $DISCOVERY_LOG"
fi
