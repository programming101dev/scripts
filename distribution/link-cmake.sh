#!/usr/bin/env bash
set -euo pipefail

dry_run=0
check_only=0

usage() {
  cat <<'P101_USAGE'
Usage: ./link-cmake.sh [-n] [-c]

Symlink the shared scripts/cmake helper directory into every C/C++ repo listed
in repos.txt.

Options:
  -n  dry run; report changes without writing
  -c  check only; fail when a required link is missing or stale
  -h  help
P101_USAGE
}

case " $* " in *" --help "*|*" -h "*) usage; exit 0 ;; esac

while getopts ":nch" opt; do
  case "$opt" in
    n) dry_run=1 ;;
    c) check_only=1; dry_run=1 ;;
    h) usage; exit 0 ;;
    \?|:) usage >&2; exit 2 ;;
  esac
done

# link-cmake.sh — symlink the shared cmake/ helper directory into every repo
# listed in repos.txt, mirroring how link-flags.sh symlinks .flags and
# link-compilers.sh symlinks compiler_paths.txt.
#
# The shared CMakeLists sources its helper scripts from ${repo}/cmake, so a
# repo needs that directory to build. In the workspace all repos sit next to
# scripts/, so a symlink to scripts/cmake/ makes them share ONE source of
# truth: edit scripts/cmake/<helper> once and every repo sees it live, no
# re-copy. (A standalone checkout with no scripts/ sibling instead runs
# scripts/recreate-cmake-helpers.sh to materialize a real cmake/.)

create_symlinks() {
  local script_dir cmake_dir repos_file
  script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
  cmake_dir="${script_dir}/cmake"
  repos_file="${script_dir}/repos.txt"

  [[ -d "${cmake_dir}" ]] || { echo "ERROR: ${cmake_dir} not found." >&2; exit 1; }
  [[ -f "${repos_file}" ]] || { echo "ERROR: ${repos_file} not found." >&2; exit 1; }
  local failures=0

  while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
    raw="${raw%$'\r'}"
    raw="${raw%%#*}"
    raw="$(printf '%s' "${raw}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -z "${raw}" ]] && continue

    local _url dir _type
    IFS='|' read -r _url dir _type <<EOF
${raw}
EOF
    if [[ -z "${dir:-}" ]]; then
      echo "FAIL: malformed repos.txt line: ${raw}" >&2
      failures=$((failures + 1))
      continue
    fi
    [[ "${_type:-}" == "c" || "${_type:-}" == "cxx" ]] || continue

    case "${dir}" in
      /*) : ;;
      *) dir="$(cd -- "${script_dir}/${dir}" 2>/dev/null && pwd -P)" || {
        echo "FAIL: cannot resolve ${dir}" >&2
        failures=$((failures + 1))
        continue
      } ;;
    esac
    if [[ ! -d "${dir}" ]]; then
      echo "FAIL: not a directory: ${dir}" >&2
      failures=$((failures + 1))
      continue
    fi
    [[ "${dir}" == "${script_dir}" ]] && continue   # scripts/ owns the real dir

    # Use a RELATIVE target (../../scripts/cmake) so the symlink is valid no
    # matter what absolute path the workspace sits at — important because it
    # may be created under one mount (e.g. a bridged VM) and used under
    # another (the host). python3 is already required by the build; fall back
    # to an absolute link only if it is somehow unavailable.
    local target="${cmake_dir}"
    if command -v python3 >/dev/null 2>&1; then
      target="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "${cmake_dir}" "${dir}")"
    fi

    local linkpath="${dir}/cmake"
    if [[ -L "${linkpath}" ]]; then
      [[ "$(readlink "${linkpath}")" == "${target}" ]] && { echo "OK: ${linkpath}"; continue; }
      if [[ "${check_only}" -eq 1 ]]; then
        echo "FAIL: stale CMake helper link: ${linkpath} -> $(readlink "${linkpath}")" >&2
        failures=$((failures + 1))
      elif [[ "${dry_run}" -eq 1 ]]; then
        echo "[dry-run] update: ${linkpath} -> ${target}"
      else
        ln -sfn -- "${target}" "${linkpath}"; echo "Updated: ${linkpath} -> ${target}"
      fi
    elif [[ -e "${linkpath}" ]]; then
      echo "FAIL: refusing to replace non-symlink path: ${linkpath}" >&2
      echo "      Move or remove it explicitly, then run this script again." >&2
      failures=$((failures + 1))
    else
      if [[ "${check_only}" -eq 1 ]]; then
        echo "FAIL: missing CMake helper link: ${linkpath}" >&2
        failures=$((failures + 1))
      elif [[ "${dry_run}" -eq 1 ]]; then
        echo "[dry-run] create: ${linkpath} -> ${target}"
      else
        ln -s -- "${target}" "${linkpath}"; echo "Created: ${linkpath} -> ${target}"
      fi
    fi
  done < "${repos_file}"

  if [[ "${failures}" -gt 0 ]]; then
    echo "CMake link update failed: ${failures} problem(s)." >&2
    return 1
  fi
}

create_symlinks
