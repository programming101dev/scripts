#!/usr/bin/env bash
set -euo pipefail

create_symlinks() {
  local root
  root="$(pwd -P)"

  # Absolute inputs
  local c_compilers_file="${root}/supported_c_compilers.txt"
  local cxx_compilers_file="${root}/supported_cxx_compilers.txt"
  local sanitizers_file="${root}/sanitizers.txt"
  local repos_file="${root}/repos.txt"

  # Sanity checks (don’t hard-fail on sanitizers; some repos may not use them)
  [[ -f "${repos_file}" ]] || { echo "ERROR: ${repos_file} not found." >&2; exit 1; }
  [[ -f "${c_compilers_file}" ]] || { echo "ERROR: ${c_compilers_file} not found." >&2; exit 1; }
  [[ -f "${cxx_compilers_file}" ]] || { echo "ERROR: ${cxx_compilers_file} not found." >&2; exit 1; }
  [[ -f "${sanitizers_file}" ]] || echo "WARN: ${sanitizers_file} not found; will skip sanitizer links."

  # Helper: ensure symlink points to target (update if wrong; create if missing)
  ensure_link() {
    # $1 = target (absolute), $2 = link path
    local target="$1" linkpath="$2"

    # Skip if target is missing (don’t make broken links)
    if [[ ! -e "${target}" ]]; then
      echo "WARN: target missing, skip link: ${linkpath} -> ${target}"
      return 0
    fi

    if [[ -L "${linkpath}" ]]; then
      # Existing symlink: update only if different
      local cur
      cur="$(readlink "${linkpath}")"
      if [[ "${cur}" == "${target}" ]]; then
        echo "OK: link already correct: ${linkpath}"
        return 0
      fi
      ln -sfn -- "${target}" "${linkpath}"
      echo "Updated symlink: ${linkpath} -> ${target}"
    elif [[ -e "${linkpath}" ]]; then
      echo "SKIP: ${linkpath} exists and is not a symlink."
      return 0
    else
      ln -s -- "${target}" "${linkpath}"
      echo "Created symlink: ${linkpath} -> ${target}"
    fi
  }

  # Read repos.txt: <git-url>|<dest-path>|<lang>
  while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
    # strip CR, comments, and surrounding whitespace
    raw="${raw%$'\r'}"
    raw="${raw%%#*}"
    raw="$(printf '%s' "${raw}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -z "${raw}" ]] && continue

    local repo_url dir repo_type
    IFS='|' read -r repo_url dir repo_type <<EOF
${raw}
EOF

    # Require a destination dir
    if [[ -z "${dir:-}" ]]; then
      echo "SKIP: no destination in line: ${raw}"
      continue
    fi

    # Resolve to absolute path
    case "${dir}" in
      /*) : ;;
      *) dir="${root}/${dir}" ;;
    esac

    if [[ ! -d "${dir}" ]]; then
      echo "SKIP: destination directory does not exist: ${dir}"
      continue
    fi

    # Choose which compiler list to link
    local comp_target comp_link
    case "${repo_type:-}" in
      c)
        comp_target="${c_compilers_file}"
        comp_link="${dir}/supported_c_compilers.txt"
        ;;
      cxx)
        comp_target="${cxx_compilers_file}"
        comp_link="${dir}/supported_cxx_compilers.txt"
        ;;
      *)
        echo "SKIP: unsupported repo type '${repo_type:-}' for ${dir}"
        continue
        ;;
    esac

    ensure_link "${comp_target}" "${comp_link}"

    # Sanitizers link (optional)
    if [[ -f "${sanitizers_file}" ]]; then
      ensure_link "${sanitizers_file}" "${dir}/sanitizers.txt"
    fi
  done < "${repos_file}"
}

create_symlinks
