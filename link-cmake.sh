#!/usr/bin/env bash
set -euo pipefail

dry_run=0

usage() {
  cat <<'P101_USAGE'
Usage: ./link-cmake.sh [-n]

Symlink the shared scripts/cmake helper directory into every C/C++ repo listed
in repos.txt.

Options:
  -n  dry run; report changes without writing
  -h  help
P101_USAGE
}

case " $* " in *" --help "*|*" -h "*) usage; exit 0 ;; esac

while getopts ":nh" opt; do
  case "$opt" in
    n) dry_run=1 ;;
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
  script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  cmake_dir="${script_dir}/cmake"
  repos_file="${script_dir}/repos.txt"

  [[ -d "${cmake_dir}" ]] || { echo "ERROR: ${cmake_dir} not found." >&2; exit 1; }
  [[ -f "${repos_file}" ]] || { echo "ERROR: ${repos_file} not found." >&2; exit 1; }

  while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
    raw="${raw%$'\r'}"
    raw="${raw%%#*}"
    raw="$(printf '%s' "${raw}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -z "${raw}" ]] && continue

    local _url dir _type
    IFS='|' read -r _url dir _type <<EOF
${raw}
EOF
    [[ -n "${dir:-}" ]] || continue
    [[ "${_type:-}" == "c" || "${_type:-}" == "cxx" ]] || continue

    case "${dir}" in
      /*) : ;;
      *) dir="$(cd -- "${script_dir}/${dir}" 2>/dev/null && pwd -P)" || { echo "SKIP: cannot resolve ${dir}"; continue; } ;;
    esac
    [[ -d "${dir}" ]] || { echo "SKIP: not a directory: ${dir}"; continue; }
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
      if [[ "${dry_run}" -eq 1 ]]; then
        echo "[dry-run] update: ${linkpath} -> ${target}"
      else
        ln -sfn -- "${target}" "${linkpath}"; echo "Updated: ${linkpath} -> ${target}"
      fi
    elif [[ -e "${linkpath}" ]]; then
      if [[ "${dry_run}" -eq 1 ]]; then
        echo "[dry-run] replace directory: ${linkpath} -> ${target}"
      else
        rm -rf -- "${linkpath}"
        ln -s -- "${target}" "${linkpath}"; echo "Replaced: ${linkpath} -> ${target}"
      fi
    else
      if [[ "${dry_run}" -eq 1 ]]; then
        echo "[dry-run] create: ${linkpath} -> ${target}"
      else
        ln -s -- "${target}" "${linkpath}"; echo "Created: ${linkpath} -> ${target}"
      fi
    fi
  done < "${repos_file}"
}

create_symlinks
