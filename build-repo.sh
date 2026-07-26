#!/usr/bin/env bash
# build-repo.sh — configure + build (+ optional install) every repo in repos.txt

set -euo pipefail

# Always operate from the directory this script lives in (repos.txt lives
# here, and the relative dest paths in it are relative to this directory).
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# ----------------- defaults -----------------
c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""
forward_skip_cache=false   # if true, pass -S to install.sh (skip cache refresh)
skip_install=false

usage() {
  cat <<USAGE >&2
Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-S] [-I]
  -c  C compiler         (e.g. gcc-15, clang)
  -x  C++ compiler       (e.g. g++-15, clang++)
  -f  clang-format       (default: clang-format; path or name)
  -t  clang-tidy         (default: clang-tidy;  path or name)
  -k  cppcheck           (default: cppcheck;    path or name)
  -s  sanitizers list    (e.g. address,undefined) — if omitted, repo may read sanitizers.txt
  -S  forward 'skip cache update' to install.sh (passes -S to install.sh)
  -I  skip install.sh after building repos

Example:
  $0 -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck -s address,undefined -S
USAGE
  exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

# ----------------- args -----------------
while getopts ":c:x:f:t:k:s:SI" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    S) forward_skip_cache=true ;;
    I) skip_install=true ;;
    \?|:) usage ;;
  esac
done

[[ -n "$c_compiler"   ]] || { echo "Error: -c (C compiler) is required" >&2; usage; }
[[ -n "$cxx_compiler" ]] || { echo "Error: -x (C++ compiler) is required" >&2; usage; }

# ----------------- helpers -----------------
say() { printf '%b\n' "$*"; }
hr()  { printf '%*s\n' "$(tput cols 2>/dev/null || echo 80)" '' | tr ' ' -; }

# compiler names resolve through the pinned map first (compiler_paths.txt,
# written by check-compilers.sh), then PATH; absolute paths pass through
MAP_FILE="compiler_paths.txt"
map_lookup() {
  local name="$1" line
  [[ -f "$MAP_FILE" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in "$name="*) printf '%s' "${line#*=}"; return 0 ;; esac
  done < "$MAP_FILE"
  return 1
}

resolve_any() {
  local v="$1" p
  if [[ "$v" = /* ]]; then
    [[ -x "$v" ]] || { echo "Error: '$v' not executable" >&2; exit 2; }
    printf '%s' "$v"
  elif p="$(map_lookup "$v")" && [[ -x "$p" ]]; then
    printf '%s' "$p"
  else
    p="$(command -v "$v" 2>/dev/null)" || { echo "Error: '$v' not found in $MAP_FILE or PATH" >&2; exit 2; }
    printf '%s' "$p"
  fi
}

CC_PATH="$(resolve_any "$c_compiler")"
CXX_PATH="$(resolve_any "$cxx_compiler")"
CLANG_FORMAT_PATH="$(resolve_any "$clang_format_name")"
CLANG_TIDY_PATH="$(resolve_any "$clang_tidy_name")"
CPPCHECK_PATH="$(resolve_any "$cppcheck_name")"

# ----------------- iterate repos -----------------
repos_file="repos.txt"
[[ -f "$repos_file" ]] || { echo "Error: $repos_file not found" >&2; exit 3; }

trim() {
  local s="${1-}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

# Read repos.txt on fd 3 so the children (change-compiler.sh, build.sh,
# install.sh — which may legitimately read stdin, e.g. a sudo password
# prompt in install.sh) keep the real stdin.
while IFS= read -r raw <&3 || [[ -n "${raw:-}" ]]; do
  # Strip CR (CRLF files), comments, and surrounding whitespace — same
  # semantics as clone-repos.sh / link-*.sh / copy-cmake.sh.
  raw="${raw%$'\r'}"
  raw="${raw%%#*}"
  line="$(trim "${raw}")"
  [[ -z "$line" ]] && continue

  IFS='|' read -r repo_url dir repo_type <<< "$line"
  repo_url="$(trim "${repo_url:-}")"
  dir="$(trim "${dir:-}")"
  repo_type="$(trim "${repo_type:-}")"

  if [[ -z "$dir" || -z "$repo_type" ]]; then
    say "  -> Skipping malformed line: $raw"
    continue
  fi

  hr
  say "Working on ${dir} (${repo_type})"

  if [[ ! -d "$dir" ]]; then
    say "  -> Skipping (directory not found): $dir"
    continue
  fi

  pushd "$dir" >/dev/null

  # A repo without change-compiler.sh should be skipped with a message,
  # not kill the whole multi-repo run via set -e.
  if [[ ! -x ./change-compiler.sh ]]; then
    say "  -> No executable change-compiler.sh in ${dir}; skipping repo."
    popd >/dev/null
    continue
  fi

  # Decide which compiler to feed into change-compiler.sh
  case "$repo_type" in
    c)
      say "Configuring with: CC=${CC_PATH}, clang-format=${CLANG_FORMAT_PATH}, clang-tidy=${CLANG_TIDY_PATH}, cppcheck=${CPPCHECK_PATH}, sanitizers=${sanitizers:-<none>}"
      if [[ -n "$sanitizers" ]]; then
        ./change-compiler.sh -c "$CC_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH" -s "$sanitizers"
      else
        ./change-compiler.sh -c "$CC_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH"
      fi
      ;;
    cxx)
      say "Configuring with: CXX=${CXX_PATH}, clang-format=${CLANG_FORMAT_PATH}, clang-tidy=${CLANG_TIDY_PATH}, cppcheck=${CPPCHECK_PATH}, sanitizers=${sanitizers:-<none>}"
      # Your cxx repos typically have their own change-compiler script taking -c for C++ compiler;
      # if they expect -x for C++ specifically, adjust here. Most of your templates use -c.
      if [[ -n "$sanitizers" ]]; then
        ./change-compiler.sh -c "$CXX_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH" -s "$sanitizers"
      else
        ./change-compiler.sh -c "$CXX_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH"
      fi
      ;;
    python)
      say "Configuring Python/Clang tool with: clang=${CC_PATH}"
      ./change-compiler.sh -c "$CC_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH"
      ;;
    *)
      say "  -> Unknown repo type '${repo_type}', skipping."
      popd >/dev/null
      continue
      ;;
  esac

  # Always build right away
  if [[ -x ./build.sh ]]; then
    say "Building: ${dir}"
    ./build.sh
  else
    say "  -> No build.sh found, skipping build."
  fi

  # If there’s an installer, run it (forward -s to skip cache if -S was given)
  if $skip_install; then
    say "Skipping install: ${dir}"
  elif [[ -x ./install.sh ]]; then
    if $forward_skip_cache; then
      say "Installing (skip cache update): ${dir}"
      ./install.sh -S
    else
      say "Installing: ${dir}"
      ./install.sh
    fi
  fi

  popd >/dev/null
done 3< "$repos_file"

hr
say "All repositories processed."
