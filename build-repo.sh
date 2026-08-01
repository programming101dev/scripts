#!/usr/bin/env bash
# build-repo.sh — configure + build (+ optional install) every repo in repos.txt

set -euo pipefail

# Always operate from the directory this script lives in (repos.txt lives
# here, and the relative dest paths in it are relative to this directory).
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# ----------------- defaults -----------------
c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""
forward_skip_cache=false   # if true, pass -S to install.sh (skip cache refresh)
skip_install=false
interactive=false

usage() {
  cat <<USAGE >&2
Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-S] [-I] [--interactive]
  -c  C compiler         (e.g. gcc-15, clang)
  -x  C++ compiler       (e.g. g++-15, clang++)
  -f  clang-format       (default: clang-format; path or name)
  -t  clang-tidy         (default: clang-tidy;  path or name)
  -k  cppcheck           (default: cppcheck;    path or name)
  -s  sanitizers list    (e.g. address,undefined) — if omitted, repo may read sanitizers.txt
  -S  forward 'skip cache update' to install.sh (passes -S to install.sh)
  -I  skip install.sh after building repos
  -i, --interactive
      Pause after a configure, build, or install failure. Fix the repository
      in another terminal, then press Enter to retry that same phase. Enter
      'q' to abort.

Example:
  $0 -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck -s address,undefined -S
USAGE
  exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

# Extract long options before getopts. Do not use eval: tool and compiler
# paths may contain shell metacharacters.
filtered_args=()
for argument in "$@"; do
  case "$argument" in
    --interactive) interactive=true ;;
    *) filtered_args+=("$argument") ;;
  esac
done
if [[ "${#filtered_args[@]}" -gt 0 ]]; then
  set -- "${filtered_args[@]}"
else
  set --
fi
unset filtered_args argument

# ----------------- args -----------------
while getopts ":c:x:f:t:k:s:SIi" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    S) forward_skip_cache=true ;;
    I) skip_install=true ;;
    i) interactive=true ;;
    \?|:) usage ;;
  esac
done

[[ -n "$c_compiler"   ]] || { echo "Error: -c (C compiler) is required" >&2; usage; }
[[ -n "$cxx_compiler" ]] || { echo "Error: -x (C++ compiler) is required" >&2; usage; }

# ----------------- helpers -----------------
say() { printf '%b\n' "$*"; }
hr()  { printf '%*s\n' "$(tput cols 2>/dev/null || echo 80)" '' | tr ' ' -; }

run_repo_phase() {
  local description="$1"
  local status
  local answer

  shift
  while true; do
    set +e
    "$@"
    status=$?
    set -e
    if [[ "$status" -eq 0 ]]; then
      return 0
    fi
    if ! $interactive; then
      return "$status"
    fi

    printf '\nFAILED: %s (exit %d).\n' "$description" "$status" >&2
    printf 'Fix the issue, then press Enter to retry this phase; enter q to abort: ' >&2
    if ! IFS= read -r answer; then
      printf '\nInteractive input closed; aborting.\n' >&2
      return "$status"
    fi
    case "$answer" in
      q|Q|quit|QUIT)
        printf 'Aborting at: %s\n' "$description" >&2
        return "$status"
        ;;
      *)
        printf 'Retrying: %s\n\n' "$description" >&2
        ;;
    esac
  done
}

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

change_compiler_supports_sanitizers() {
  # Inspect the option parser instead of executing --help: older repository
  # scripts do not all give --help the same exit behavior.
  grep -Fq -- '-s)' ./change-compiler.sh
}

CC_PATH="$(resolve_any "$c_compiler")"
CXX_PATH="$(resolve_any "$cxx_compiler")"
CLANG_FORMAT_PATH="$(resolve_any "$clang_format_name")"
CLANG_TIDY_PATH="$(resolve_any "$clang_tidy_name")"
CPPCHECK_PATH="$(resolve_any "$cppcheck_name")"

build_script_args=()
if [[ -n "${P101_QUIET:-}" ]]; then
  build_script_args+=(-q)
fi

# ----------------- iterate repos -----------------
repos_file="repos.txt"
[[ -f "$repos_file" ]] || { echo "Error: $repos_file not found" >&2; exit 3; }

trim() {
  local s="${1-}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

failures=0
processed=0

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

  if [[ -z "$repo_url" || -z "$dir" || -z "$repo_type" ]]; then
    say "  -> FAIL: malformed line: $raw"
    failures=$((failures + 1))
    continue
  fi
  processed=$((processed + 1))

  hr
  say "Working on ${dir} (${repo_type})"

  if [[ ! -d "$dir" ]]; then
    say "  -> FAIL: directory not found: $dir"
    failures=$((failures + 1))
    continue
  fi

  if [[ "$repo_type" == "c-bootstrap" ]]; then
    say "  -> Bootstrap repository is present but not active; skipping."
    continue
  fi

  pushd "$dir" >/dev/null

  # Decide which compiler to feed into change-compiler.sh
  case "$repo_type" in
    c)
      if [[ ! -x ./change-compiler.sh ]]; then
        say "  -> FAIL: no executable change-compiler.sh in ${dir}."
        failures=$((failures + 1))
        popd >/dev/null
        continue
      fi
      say "Configuring with: CC=${CC_PATH}, clang-format=${CLANG_FORMAT_PATH}, clang-tidy=${CLANG_TIDY_PATH}, cppcheck=${CPPCHECK_PATH}, sanitizers=${sanitizers:-<none>}"
      change_args=(-c "$CC_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH")
      if [[ -n "$sanitizers" ]] && change_compiler_supports_sanitizers; then
        change_args+=(-s "$sanitizers")
      else
        if [[ -n "$sanitizers" ]]; then
          say "  -> change-compiler.sh does not accept -s; using the repository's configured sanitizer flags."
        fi
      fi
      if run_repo_phase "configure ${dir} with ${CC_PATH}" ./change-compiler.sh "${change_args[@]}"; then
        :
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
      ;;
    cxx)
      if [[ ! -x ./change-compiler.sh ]]; then
        say "  -> FAIL: no executable change-compiler.sh in ${dir}."
        failures=$((failures + 1))
        popd >/dev/null
        continue
      fi
      say "Configuring with: CXX=${CXX_PATH}, clang-format=${CLANG_FORMAT_PATH}, clang-tidy=${CLANG_TIDY_PATH}, cppcheck=${CPPCHECK_PATH}, sanitizers=${sanitizers:-<none>}"
      # Your cxx repos typically have their own change-compiler script taking -c for C++ compiler;
      # if they expect -x for C++ specifically, adjust here. Most of your templates use -c.
      change_args=(-c "$CXX_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH")
      if [[ -n "$sanitizers" ]] && change_compiler_supports_sanitizers; then
        change_args+=(-s "$sanitizers")
      else
        if [[ -n "$sanitizers" ]]; then
          say "  -> change-compiler.sh does not accept -s; using the repository's configured sanitizer flags."
        fi
      fi
      if run_repo_phase "configure ${dir} with ${CXX_PATH}" ./change-compiler.sh "${change_args[@]}"; then
        :
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
      ;;
    python)
      say "Python tool: no CMake/compiler configuration required."
      ;;
    *)
      say "  -> FAIL: unknown repo type '${repo_type}'."
      failures=$((failures + 1))
      popd >/dev/null
      continue
      ;;
  esac

  # Always build right away
  if [[ -x ./build.sh ]]; then
    say "Building: ${dir}"
    # Bash 3.2 treats "${empty_array[@]}" as an unbound variable under
    # `set -u`. Keep the zero-argument path explicit for macOS.
    if [[ "${#build_script_args[@]}" -gt 0 ]]; then
      build_command=(./build.sh "${build_script_args[@]}")
    else
      build_command=(./build.sh)
    fi
    if run_repo_phase "build ${dir} with $([[ "$repo_type" == "cxx" ]] && printf '%s' "$CXX_PATH" || printf '%s' "$CC_PATH")" "${build_command[@]}"; then
      :
    else
      status=$?
      popd >/dev/null
      exit "$status"
    fi
  else
    say "  -> FAIL: no executable build.sh found."
    failures=$((failures + 1))
  fi

  # If there’s an installer, run it (forward -s to skip cache if -S was given)
  if $skip_install; then
    say "Skipping install: ${dir}"
  elif [[ -x ./install.sh ]]; then
    if $forward_skip_cache; then
      say "Installing (skip cache update): ${dir}"
      install_command=(./install.sh -S)
    else
      say "Installing: ${dir}"
      install_command=(./install.sh)
    fi
    if run_repo_phase "install ${dir}" "${install_command[@]}"; then
      :
    else
      status=$?
      popd >/dev/null
      exit "$status"
    fi
  fi

  popd >/dev/null
done 3< "$repos_file"

hr
if [[ "$processed" -eq 0 ]]; then
  say "FAIL: repos.txt did not contain any repositories."
  exit 1
fi
if [[ "$failures" -gt 0 ]]; then
  say "Repository build failed: ${failures} configuration problem(s)."
  exit 1
fi
say "All ${processed} repositories processed successfully."
