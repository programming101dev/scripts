#!/usr/bin/env bash

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
  *" --help "*|*" -h "*)
    cat <<'P101_USAGE'
claude.sh — collect everything Claude needs about this machine's toolchain.

Run this in a normal terminal on each machine you care about (macOS, Linux,
FreeBSD). It is READ-ONLY with respect to the build system: it never edits
flags/*.txt, supported_*.txt, sanitizers.txt, or version.txt. Everything it
learns is written under claude-report/ in this directory — which is inside
the folder connected to the Claude session, so afterwards just tell Claude
"the report is ready" and it can read the results directly.

What it collects:
  system.txt        OS / arch / core count
  tools.txt         cmake, clang-format, clang-tidy, cppcheck, python3
                    (paths + versions), plus bare check-env.sh output
  compilers.txt     every gcc/g++/clang/clang++-family binary on PATH:
                    path, version, target, and a trivial compile check
  curation.txt      per-file include/exclude counts for flags/*.txt,
                    supported_*.txt, sanitizers.txt, version.txt
  git.txt           branch, last commit, short status
  harvest/          harvest-flags.py canonical + worklist reports for every
                    working compiler found (the big one)
  probed-flags/     copy of ../.flags/<cc>/*.txt — what generate-flags.sh
                    has actually approved on THIS machine (if present)

Portable: stock macOS bash 3.2, Linux, FreeBSD. No network. Best-effort:
missing tools are recorded, never fatal.
P101_USAGE
    exit 0 ;;
esac
# claude.sh — collect everything Claude needs about this machine's toolchain.
#
# Run this in a normal terminal on each machine you care about (macOS, Linux,
# FreeBSD). It is READ-ONLY with respect to the build system: it never edits
# flags/*.txt, supported_*.txt, sanitizers.txt, or version.txt. Everything it
# learns is written under claude-report/ in this directory — which is inside
# the folder connected to the Claude session, so afterwards just tell Claude
# "the report is ready" and it can read the results directly.
#
# What it collects:
#   system.txt        OS / arch / core count
#   tools.txt         cmake, clang-format, clang-tidy, cppcheck, python3
#                     (paths + versions), plus bare check-env.sh output
#   compilers.txt     every gcc/g++/clang/clang++-family binary on PATH:
#                     path, version, target, and a trivial compile check
#   curation.txt      per-file include/exclude counts for flags/*.txt,
#                     supported_*.txt, sanitizers.txt, version.txt
#   git.txt           branch, last commit, short status
#   harvest/          harvest-flags.py canonical + worklist reports for every
#                     working compiler found (the big one)
#   probed-flags/     copy of ../.flags/<cc>/*.txt — what generate-flags.sh
#                     has actually approved on THIS machine (if present)
#
# Portable: stock macOS bash 3.2, Linux, FreeBSD. No network. Best-effort:
# missing tools are recorded, never fatal.

set -u

CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1
SCRIPT_DIR="$PWD"
OUT="$SCRIPT_DIR/claude-report"

rm -rf "$OUT"
mkdir -p "$OUT"

note() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------- system ----------
{
  echo "== system =="
  uname -a
  case "$(uname)" in
    Darwin)  sw_vers 2>/dev/null || true
             sysctl -n machdep.cpu.brand_string 2>/dev/null || true
             echo "cores: $(sysctl -n hw.ncpu 2>/dev/null || echo '?')" ;;
    FreeBSD) freebsd-version 2>/dev/null || true
             echo "cores: $(sysctl -n hw.ncpu 2>/dev/null || echo '?')" ;;
    *)       [ -r /etc/os-release ] && grep -E '^(NAME|VERSION)=' /etc/os-release
             echo "cores: $(nproc 2>/dev/null || echo '?')" ;;
  esac
  echo "date: $(date)"
  echo "PATH: $PATH"
} > "$OUT/system.txt" 2>&1
note "wrote system.txt"

# ---------- support tools ----------
{
  echo "== support tools =="
  for t in cmake clang-format clang-tidy cppcheck python3 scan-build diagtool git; do
    if have "$t"; then
      printf '%-14s %s\n' "$t" "$(command -v "$t")"
      printf '%-14s %s\n' "" "$("$t" --version 2>&1 | head -1)"
    else
      printf '%-14s NOT FOUND\n' "$t"
    fi
  done
  echo
  echo "== check-env.sh (generic tools) =="
  if [ -x ./check-env.sh ]; then
    ./check-env.sh 2>&1
    echo "exit: $?"
  else
    echo "check-env.sh not present/executable"
  fi
} > "$OUT/tools.txt" 2>&1
note "wrote tools.txt"

# ---------- compiler inventory ----------
# Scan PATH for compiler-family binaries; dedupe by basename.
found_names=""
scan_path() {
  local IFS=':'
  local d f base
  for d in $PATH; do
    [ -d "$d" ] || continue
    for f in "$d"/gcc "$d"/gcc-* "$d"/gcc[0-9]* \
             "$d"/g++ "$d"/g++-* "$d"/g++[0-9]* \
             "$d"/clang "$d"/clang-[0-9]* "$d"/clang[0-9]* "$d"/clang-devel \
             "$d"/clang++ "$d"/clang++-* "$d"/clang++[0-9]* \
             "$d"/cc "$d"/c++; do
      [ -x "$f" ] && [ ! -d "$f" ] || continue
      base="$(basename "$f")"
      # skip binutils wrappers that match the gcc-* glob
      case "$base" in
        *-ar|*-ar-[0-9]*|*-nm|*-nm-[0-9]*|*-ranlib|*-ranlib-[0-9]*) continue ;;
      esac
      case " $found_names " in
        *" $base "*) ;;
        *) found_names="$found_names $base" ;;
      esac
    done
  done
}
scan_path

# Results are returned via globals (NOT command substitution — that would
# run the function in a subshell and lose TRIVIAL_ERR).
TRIVIAL_ST=""
TRIVIAL_ERR=""
trivial_check() {
  # trivial_check <compiler>: sets TRIVIAL_ST to OK/BROKEN; on BROKEN the
  # compiler's actual error output is left in TRIVIAL_ERR so the report
  # shows WHY.
  local cc="$1" tmpdir lang src out
  TRIVIAL_ST="BROKEN"
  TRIVIAL_ERR=""
  tmpdir="$(mktemp -d 2>/dev/null || mktemp -d -t ccprobe)" || { TRIVIAL_ST="?"; return; }
  case "$(basename "$cc")" in
    *++*|c++) lang="c++"; src="$tmpdir/t.cpp"; printf 'int main(){return 0;}\n' >"$src" ;;
    *)        lang="c";   src="$tmpdir/t.c";   printf 'int main(void){return 0;}\n' >"$src" ;;
  esac
  if out="$("$cc" -x "$lang" "$src" -o "$tmpdir/a.out" 2>&1)" && [ -x "$tmpdir/a.out" ]; then
    TRIVIAL_ST="OK"
  else
    TRIVIAL_ERR="$out"
  fi
  rm -rf "$tmpdir" 2>/dev/null
}

working_compilers=""
{
  echo "== compiler inventory (from PATH scan) =="
  if [ -z "$found_names" ]; then
    echo "no compiler-family binaries found on PATH"
  fi
  for name in $found_names; do
    p="$(command -v "$name" 2>/dev/null)" || continue
    echo "---- $name"
    echo "path:    $p"
    echo "version: $("$name" --version 2>&1 | head -1)"
    echo "target:  $("$name" -dumpmachine 2>/dev/null || echo '?')"
    trivial_check "$name"
    echo "compiles trivial program: $TRIVIAL_ST"
    if [ "$TRIVIAL_ST" = "OK" ]; then
      working_compilers="$working_compilers $name"
    elif [ -n "$TRIVIAL_ERR" ]; then
      echo "error output:"
      printf '%s\n' "$TRIVIAL_ERR" | head -8 | sed 's/^/    | /'
    fi
    echo
  done
  echo "== current supported lists =="
  for f in supported_c_compilers.txt supported_cxx_compilers.txt; do
    echo "-- $f:"
    cat "$f" 2>/dev/null || echo "(absent)"
  done
} > "$OUT/compilers.txt" 2>&1
note "wrote compilers.txt (working:${working_compilers:- none})"

# ---------- curation state ----------
{
  echo "== flags/*.txt curation state (active = include, comment = exclude) =="
  if [ -d flags ]; then
    for f in flags/*.txt; do
      [ -f "$f" ] || continue
      active=$(sed 's/#.*//' "$f" | tr -d '"' | tr -s ' \t' '\n' | grep -c '^-' || true)
      commented=$(grep -c '^[[:space:]]*#.*-' "$f" || true)
      printf '%-45s active-tokens: %-5s commented-lines: %s\n' "$(basename "$f")" "$active" "$commented"
    done
  else
    echo "flags/ directory absent"
  fi
  echo
  echo "version.txt:    $(cat version.txt 2>/dev/null || echo '(absent)')"
  echo "sanitizers.txt: $(cat sanitizers.txt 2>/dev/null || echo '(absent)')"
  echo
  echo "== compiler_paths.txt (pinned name->path map) =="
  cat compiler_paths.txt 2>/dev/null || echo "(absent — check-compilers.sh not run since the map feature landed)"
  echo
  echo "== compiler-discovery.log (rejected compilers + why) =="
  cat compiler-discovery.log 2>/dev/null || echo "(absent — check-compilers.sh not run since rejection tracking landed)"
  echo
  echo "== lint-flags.py (negation/downgrade conflicts among actives) =="
  if [ -x ./lint-flags.py ] && have python3; then
    python3 ./lint-flags.py 2>&1
  else
    echo "(lint-flags.py or python3 not available)"
  fi
} > "$OUT/curation.txt" 2>&1
note "wrote curation.txt"

# ---------- git ----------
{
  echo "== git =="
  git rev-parse --abbrev-ref HEAD 2>&1
  git log --oneline -3 2>&1
  echo "-- status:"
  git status --porcelain 2>&1
} > "$OUT/git.txt" 2>&1
note "wrote git.txt"

# ---------- harvest: canonical universe + undecided worklists ----------
if [ -x ./harvest-flags.py ] && have python3 && [ -n "$working_compilers" ]; then
  note "harvesting flag universes (this queries the installed compilers)..."
  # shellcheck disable=SC2086
  python3 ./harvest-flags.py $working_compilers > "$OUT/harvest-summary.txt" 2>&1 || true
  if [ -d flag_report ]; then
    mkdir -p "$OUT/harvest"
    cp flag_report/*.txt "$OUT/harvest/" 2>/dev/null || true
  fi
  note "wrote harvest/ ($(ls "$OUT/harvest" 2>/dev/null | wc -l | tr -d ' ') files)"
else
  echo "harvest skipped: need harvest-flags.py + python3 + a working compiler" \
    > "$OUT/harvest-summary.txt"
  note "harvest skipped"
fi

# ---------- probed-flag cache from this machine ----------
if [ -d ../.flags ]; then
  mkdir -p "$OUT/probed-flags"
  {
    echo "== ../.flags cache summary (what generate-flags.sh approved here) =="
    for d in ../.flags/*/; do
      [ -d "$d" ] || continue
      cc_base="$(basename "$d")"
      mkdir -p "$OUT/probed-flags/$cc_base"
      for f in "$d"*.txt; do
        [ -f "$f" ] || continue
        cp "$f" "$OUT/probed-flags/$cc_base/" 2>/dev/null || true
        printf '%-12s %-45s %s tokens\n' "$cc_base" "$(basename "$f")" "$(wc -w < "$f" | tr -d ' ')"
      done
    done
    echo
    echo "cache version: $(cat ../.flags/version.txt 2>/dev/null || echo '(absent)')"
  } > "$OUT/probed-flags/summary.txt" 2>&1
  note "wrote probed-flags/"
else
  note "no ../.flags cache on this machine (setup.sh/update.sh not run yet) — skipped"
fi

# ---------- done ----------
echo
echo "=================================================================="
echo "Report complete: $OUT"
echo "This folder is inside your Claude-connected directory — just tell"
echo "Claude the report is ready and it can read everything itself."
echo "=================================================================="
