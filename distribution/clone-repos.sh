#!/usr/bin/env bash
set -euo pipefail

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
    *" --help "*|*" -h "*)
        cat <<'P101_USAGE'
Usage: clone-repos.sh [--interactive]

Clone missing repositories and refresh existing repositories from their
configured upstreams.

  -i, --interactive
      Pause after a repository-refresh failure. Resolve the repository in
      another terminal, then press Enter to retry that repository. Enter q to
      abort. Local changes are never discarded or stashed automatically.
P101_USAGE
        exit 0
        ;;
esac

interactive=false
refresh_aborted=false
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -i|--interactive)
            interactive=true
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            printf 'Error: unknown option: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done
if [[ "$#" -ne 0 ]]; then
    printf 'Error: unexpected arguments.\n' >&2
    exit 2
fi

# Always operate from the directory this script lives in (repos.txt lives
# here, and the relative dest paths in it are relative to this directory).
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

REPOS_FILE="repos.txt"
REFRESH_REPO_SH="./distribution/refresh-repo.sh"
GIT_RETRY_ATTEMPTS=5
GIT_RETRY_DELAY_SECONDS=5

retry_git() {
    local attempts
    local delay_seconds
    local attempt

    attempts="${GIT_RETRY_ATTEMPTS}"
    delay_seconds="${GIT_RETRY_DELAY_SECONDS}"
    attempt=1

    while true; do
        if "$@"; then
            return 0
        fi

        if (( attempt >= attempts )); then
            echo "  ! Git command failed after ${attempts} attempts." >&2
            return 1
        fi

        echo "  ! Git command failed. Retrying in ${delay_seconds} seconds (${attempt}/${attempts})..." >&2
        sleep "${delay_seconds}"
        attempt=$((attempt + 1))
    done
}

trim_whitespace() {
    local value

    value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    printf '%s' "${value}"
}

refresh_repository() {
    local target_dir="$1"
    local refresh_status
    local answer

    while true; do
        refresh_status=0
        "${REFRESH_REPO_SH}" "${target_dir}" || refresh_status=$?
        if [[ "${refresh_status}" -eq 0 || "${refresh_status}" -eq 1 ]]; then
            return 0
        fi
        if ! ${interactive}; then
            return "${refresh_status}"
        fi

        printf '\nFAILED: refresh %s (exit %d).\n' \
            "${target_dir}" "${refresh_status}" >&2
        printf 'Resolve the repository, then press Enter to retry it; enter q to abort: ' >&2
        if ! IFS= read -r answer; then
            printf '\nInteractive input closed; aborting.\n' >&2
            refresh_aborted=true
            return "${refresh_status}"
        fi
        case "${answer}" in
            q|Q|quit|QUIT)
                printf 'Aborting at repository refresh: %s\n' "${target_dir}" >&2
                refresh_aborted=true
                return "${refresh_status}"
                ;;
        esac
        printf 'Retrying repository refresh: %s\n\n' "${target_dir}" >&2
    done
}

if [[ ! -f "${REPOS_FILE}" ]]; then
    echo "Error: ${REPOS_FILE} not found in current directory." >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git not found in PATH." >&2
    exit 1
fi
if [[ ! -x "${REFRESH_REPO_SH}" ]]; then
    echo "Error: ${REFRESH_REPO_SH} is missing or not executable." >&2
    exit 1
fi

failures=0
processed=0

# Read repos.txt on fd 3 so children (git credential prompts, ssh host-key
# confirmations, submodule hooks) keep the real stdin instead of silently
# consuming the remaining lines of the list.
while IFS= read -r raw <&3 || [[ -n "${raw:-}" ]]; do
    local_line="${raw%%#*}"
    line="$(trim_whitespace "${local_line}")"

    if [[ -z "${line}" ]]; then
        continue
    fi

    IFS='|' read -r repo_url target_dir repo_type <<< "${line}"

    repo_url="$(trim_whitespace "${repo_url:-}")"
    target_dir="$(trim_whitespace "${target_dir:-}")"
    repo_type="$(trim_whitespace "${repo_type:-}")"

    if [[ -z "${repo_url}" || -z "${target_dir}" || -z "${repo_type}" ]]; then
        echo "FAIL: malformed line: ${raw}" >&2
        failures=$((failures + 1))
        continue
    fi
    case "${repo_type}" in
        c|cxx|python|c-bootstrap) ;;
        *)
            echo "FAIL: unsupported repo type '${repo_type}': ${target_dir}" >&2
            failures=$((failures + 1))
            continue
            ;;
    esac
    processed=$((processed + 1))

    if [[ -n "${repo_type}" ]]; then
        echo "==> ${target_dir} (${repo_type})"
    else
        echo "==> ${target_dir} (-)"
    fi

    mkdir -p -- "$(dirname -- "${target_dir}")"

    if [[ -d "${target_dir}" ]]; then
        if ! git -C "${target_dir}" rev-parse --git-dir >/dev/null 2>&1; then
            echo "  ! Exists but not a git repo."
            failures=$((failures + 1))
            echo
            continue
        fi

        current_origin="$(git -C "${target_dir}" remote get-url origin 2>/dev/null || echo "")"

        if [[ -n "${current_origin}" && "${current_origin}" != "${repo_url}" ]]; then
            echo "  ! Origin mismatch:"
            echo "     current: ${current_origin}"
            echo "     wanted : ${repo_url}"
            failures=$((failures + 1))
            echo
            continue
        elif [[ -z "${current_origin}" ]]; then
            echo "  ! Missing origin remote; expected ${repo_url}."
            failures=$((failures + 1))
            echo
            continue
        fi

        if [[ "${repo_type}" == "c-bootstrap" ]] &&
           ! git -C "${target_dir}" rev-parse --verify HEAD >/dev/null 2>&1; then
            # A newly-created GitHub repository may intentionally have no
            # first commit yet. It still belongs in repos.txt so every
            # workspace clones it, but there is nothing to refresh until its
            # project contract is populated.
            echo "  -> Empty bootstrap repository; no upstream branch yet."
        else
            echo "  -> Refreshing configured upstream..."
            refresh_status=0
            refresh_repository "${target_dir}" || refresh_status=$?
            if [[ "${refresh_status}" -ne 0 ]]; then
                if ${refresh_aborted}; then
                    exit "${refresh_status}"
                fi
                echo "  ! Repository refresh failed (exit ${refresh_status})."
                echo "  ! Resolve manually in ${target_dir}."
                failures=$((failures + 1))
                echo
                continue
            fi
        fi
    else
        echo "  -> Cloning ${repo_url}"
        if retry_git git clone --recursive "${repo_url}" "${target_dir}"; then
            echo "  -> Clone OK."
        else
            echo "  ! Clone failed."
            failures=$((failures + 1))
            echo
            continue
        fi
    fi

    if [[ -f "${target_dir}/.gitmodules" ]]; then
        echo "  -> Updating submodules..."
        if ! retry_git git -C "${target_dir}" submodule update --init --recursive; then
            echo "  ! Submodule update failed."
            failures=$((failures + 1))
        fi
    fi

    echo
done 3< "${REPOS_FILE}"

if (( processed == 0 )); then
    echo "Error: ${REPOS_FILE} did not contain any repositories." >&2
    exit 1
fi
if (( failures > 0 )); then
    echo "Repository update failed: ${failures} problem(s)." >&2
    exit 1
fi
echo "All ${processed} repositories processed successfully."
