#!/usr/bin/env bash
set -euo pipefail

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
  *" --help "*|*" -h "*)
    cat <<'P101_USAGE'
link-flags.sh — takes no command-line options; run with no arguments.
P101_USAGE
    exit 0 ;;
esac

create_symlinks() {
  # Resolve script and repo root so links are absolute & stable
  local script_dir repo_root flags_dir link_name
  script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  repo_root="$(cd -- "${script_dir}/.." && pwd -P)"
  # Profile-aware (P101_FLAGS_PROFILE=standard -> .flags-standard), so each
  # repo links the cache the current build actually reads.
  if [[ "${P101_FLAGS_PROFILE:-}" == "standard" ]]; then
    link_name=".flags-standard"
  else
    link_name=".flags"
  fi
  flags_dir="${repo_root}/${link_name}"

  # repos.txt lives alongside this script (consistent with the other scripts).
  local repos_file="${script_dir}/repos.txt"
  [[ -f "${repos_file}" ]] || { echo "ERROR: repos.txt not found: ${repos_file}" >&2; exit 1; }

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

    # Resolve dest to absolute (relative dests are relative to the scripts dir)
    case "${dir}" in
      /*) : ;;
      *) dir="$(cd -- "${script_dir}/${dir}" 2>/dev/null && pwd -P)" || { echo "SKIP: cannot resolve ${dir}"; continue; }
    esac

    if [[ ! -d "${dir}" ]]; then
      echo "SKIP: not a directory: ${dir}"
      continue
    fi

    ensure_link "${flags_dir}" "${dir}/${link_name}"
  done < "${repos_file}"
}

create_symlinks
