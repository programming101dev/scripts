#!/usr/bin/env bash
# build-repo.sh — configure + build (+ optional install) every repo in repos.txt

set -euo pipefail

# ----------------- defaults -----------------
c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""
forward_skip_cache=false   # if true, pass -s to install.sh (skip cache refresh)

usage() {
  cat <<USAGE >&2
Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-S]
  -c  C compiler         (e.g. gcc-15, clang)
  -x  C++ compiler       (e.g. g++-15, clang++)
  -f  clang-format       (default: clang-format; path or name)
  -t  clang-tidy         (default: clang-tidy;  path or name)
  -k  cppcheck           (default: cppcheck;    path or name)
  -s  sanitizers list    (e.g. address,undefined) — if omitted, repo may read sanitizers.txt
  -S  forward 'skip cache update' to install.sh (passes -s to install.sh)

Example:
  $0 -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck -s address,undefined -S
USAGE
  exit 1
}

# ----------------- args -----------------
while getopts ":c:x:f:t:k:s:S" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    S) forward_skip_cache=true ;;
    \?|:) usage ;;
  esac
done

[[ -n "$c_compiler"   ]] || { echo "Error: -c (C compiler) is required" >&2; usage; }
[[ -n "$cxx_compiler" ]] || { echo "Error: -x (C++ compiler) is required" >&2; usage; }

# ----------------- helpers -----------------
say() { printf '%b\n' "$*"; }
hr()  { printf '%*s\n' "$(tput cols 2>/dev/null || echo 80)" '' | tr ' ' -; }

resolve_any() {
  local v="$1" p
  if [[ "$v" = /* ]]; then
    [[ -x "$v" ]] || { echo "Error: '$v' not executable" >&2; exit 2; }
    printf '%s' "$v"
  else
    p="$(command -v "$v" 2>/dev/null)" || { echo "Error: '$v' not found in PATH" >&2; exit 2; }
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

while IFS='|' read -r repo_url dir repo_type; do
  [[ -n "${dir:-}" && -n "${repo_type:-}" ]] || continue

  hr
  say "Working on ${dir} (${repo_type})"

  if [[ ! -d "$dir" ]]; then
    say "  -> Skipping (directory not found): $dir"
    continue
  fi

  pushd "$dir" >/dev/null

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
  if [[ -x ./install.sh ]]; then
    if $forward_skip_cache; then
      say "Installing (skip cache update): ${dir}"
      ./install.sh -S
    else
      say "Installing: ${dir}"
      ./install.sh
    fi
  fi

  popd >/dev/null
done < "$repos_file"

hr
say "All repositories processed."
