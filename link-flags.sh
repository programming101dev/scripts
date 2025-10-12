#!/usr/bin/env bash
set -euo pipefail

create_symlinks() {
  # Resolve script and repo root so links are absolute & stable
  local script_dir repo_root flags_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  repo_root="$(cd -- "${script_dir}/.." && pwd -P)"
  flags_dir="${repo_root}/.flags"

  # repos.txt: kept as "where you run the script" by default. Fallback to alongside the script.
  local repos_file="${PWD}/repos.txt"
  [[ -f "${repos_file}" ]] || repos_file="${script_dir}/repos.txt"
  [[ -f "${repos_file}" ]] || { echo "ERROR: repos.txt not found." >&2; exit 1; }

  # Don’t create broken links
  if [[ ! -d "${flags_dir}" ]]; then
    echo "ERROR: flags directory not found: ${flags_dir}" >&2
    exit 1
  fi

  # Helper: ensure link points to target; update if wrong, create if missing
  ensure_link() {
    # $1 = target (absolute), $2 = link path
    local target="$1" linkpath="$2"

    if [[ -L "${linkpath}" ]]; then
      # already a symlink — fix it if it points elsewhere
      local cur
      cur="$(readlink "${linkpath}")"
      if [[ "${cur}" == "${target}" ]]; then
        echo "OK: link already correct: ${linkpath}"
        return 0
      fi
      ln -sfn -- "${target}" "${linkpath}"
      echo "Updated symlink: ${linkpath} -> ${target}"
    elif [[ -e "${linkpath}" ]]; then
      # exists but not a symlink — don’t overwrite
      echo "SKIP: ${linkpath} exists and is not a symlink."
      return 0
    else
      ln -s -- "${target}" "${linkpath}"
      echo "Created symlink: ${linkpath} -> ${target}"
    fi
  }

  # repos.txt lines: <git-url>|<dest-path>|<lang>
  while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
    # strip CR, comments, and surrounding whitespace
    raw="${raw%$'\r'}"
    raw="${raw%%#*}"
    raw="$(printf '%s' "${raw}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -z "${raw}" ]] && continue

    local _url dir _type
    IFS='|' read -r _url dir _type <<<"${raw}"

    if [[ -z "${dir:-}" ]]; then
      echo "SKIP: missing dest path in line: ${raw}"
      continue
    fi

    # Resolve dest to absolute
    case "${dir}" in
      /*) : ;;
      *) dir="$(cd -- "${dir}" 2>/dev/null && pwd -P)" || { echo "SKIP: cannot resolve ${dir}"; continue; }
    esac

    if [[ ! -d "${dir}" ]]; then
      echo "SKIP: not a directory: ${dir}"
      continue
    fi

    ensure_link "${flags_dir}" "${dir}/.flags"
  done < "${repos_file}"
}

create_symlinks
