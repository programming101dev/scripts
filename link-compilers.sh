#!/usr/bin/env bash
set -euo pipefail

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
  *" --help "*|*" -h "*)
    cat <<'P101_USAGE'
link-compilers.sh — takes no command-line options; run with no arguments.
P101_USAGE
    exit 0 ;;
esac

create_symlinks() {
  # Anchor to the directory this script lives in, not the caller's cwd.
  local root
  root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

  # Absolute inputs
  local c_compilers_file="${root}/supported_c_compilers.txt"
  local cxx_compilers_file="${root}/supported_cxx_compilers.txt"
  local sanitizers_file="${root}/sanitizers.txt"
  local compiler_map_file="${root}/compiler_paths.txt"
  local repos_file="${root}/repos.txt"

  # These are shared build inputs. Do not create a partially linked repo.
  [[ -f "${repos_file}" ]] || { echo "ERROR: ${repos_file} not found." >&2; exit 1; }
  [[ -f "${c_compilers_file}" ]] || { echo "ERROR: ${c_compilers_file} not found." >&2; exit 1; }
  [[ -f "${cxx_compilers_file}" ]] || { echo "ERROR: ${cxx_compilers_file} not found." >&2; exit 1; }
  [[ -f "${sanitizers_file}" ]] || { echo "ERROR: ${sanitizers_file} not found." >&2; exit 1; }
  [[ -f "${compiler_map_file}" ]] || { echo "ERROR: ${compiler_map_file} not found." >&2; exit 1; }
  local failures=0

  # Helper: ensure symlink points to target (update if wrong; create if missing)
  ensure_link() {
    # $1 = target (absolute), $2 = link path
    local target="$1" linkpath="$2"

    # Skip if target is missing (don’t make broken links)
    if [[ ! -e "${target}" ]]; then
      echo "FAIL: target missing: ${linkpath} -> ${target}" >&2
      return 1
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
      echo "FAIL: ${linkpath} exists and is not a symlink." >&2
      return 1
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

    local _repo_url dir repo_type
    IFS='|' read -r _repo_url dir repo_type <<EOF
${raw}
EOF

    # Require a destination dir
    if [[ -z "${dir:-}" ]]; then
      echo "FAIL: no destination in line: ${raw}" >&2
      failures=$((failures + 1))
      continue
    fi

    # Resolve to absolute path (relative dests are relative to the scripts dir)
    case "${dir}" in
      /*) : ;;
      *) dir="${root}/${dir}" ;;
    esac

    if [[ ! -d "${dir}" ]]; then
      echo "FAIL: destination directory does not exist: ${dir}" >&2
      failures=$((failures + 1))
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
        # Python tools do not consume the C/C++ compiler-list contract.
        if [[ "${repo_type:-}" == "python" ]]; then
          continue
        fi
        echo "FAIL: unsupported repo type '${repo_type:-}' for ${dir}" >&2
        failures=$((failures + 1))
        continue
        ;;
    esac

    if ! ensure_link "${comp_target}" "${comp_link}"; then
      failures=$((failures + 1))
    fi

    if ! ensure_link "${sanitizers_file}" "${dir}/sanitizers.txt"; then
      failures=$((failures + 1))
    fi

    # Name->path map link, so repos resolve compiler names to the
    # same pinned binaries the scripts do
    if ! ensure_link "${compiler_map_file}" "${dir}/compiler_paths.txt"; then
      failures=$((failures + 1))
    fi
  done < "${repos_file}"

  if [[ "${failures}" -gt 0 ]]; then
    echo "Compiler link update failed: ${failures} problem(s)." >&2
    return 1
  fi
}

create_symlinks
