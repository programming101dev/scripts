#!/usr/bin/env bash
set -euo pipefail

REPOS_FILE="repos.txt"
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

if [[ ! -f "${REPOS_FILE}" ]]; then
    echo "Error: ${REPOS_FILE} not found in current directory." >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git not found in PATH." >&2
    exit 1
fi

while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
    local_line="${raw%%#*}"
    line="$(trim_whitespace "${local_line}")"

    if [[ -z "${line}" ]]; then
        continue
    fi

    IFS='|' read -r repo_url target_dir repo_type <<< "${line}"

    repo_url="$(trim_whitespace "${repo_url:-}")"
    target_dir="$(trim_whitespace "${target_dir:-}")"
    repo_type="$(trim_whitespace "${repo_type:-}")"

    if [[ -z "${repo_url}" || -z "${target_dir}" ]]; then
        echo "Skip malformed line: ${raw}" >&2
        continue
    fi

    if [[ -n "${repo_type}" ]]; then
        echo "==> ${target_dir} (${repo_type})"
    else
        echo "==> ${target_dir} (-)"
    fi

    mkdir -p -- "$(dirname -- "${target_dir}")"

    if [[ -d "${target_dir}" ]]; then
        if [[ ! -d "${target_dir}/.git" ]]; then
            echo "  ! Exists but not a git repo — skipping."
            echo
            continue
        fi

        current_origin="$(git -C "${target_dir}" remote get-url origin 2>/dev/null || echo "")"

        if [[ -n "${current_origin}" && "${current_origin}" != "${repo_url}" ]]; then
            echo "  ! Origin mismatch:"
            echo "     current: ${current_origin}"
            echo "     wanted : ${repo_url}"
        fi

        echo "  -> Fetching..."
        if ! retry_git git -C "${target_dir}" fetch --tags --prune; then
            echo "  ! Fetch failed — skipping repository."
            echo
            continue
        fi

        if git -C "${target_dir}" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
            echo "  -> Rebase onto upstream..."
            if ! retry_git git -C "${target_dir}" pull --rebase --autostash; then
                echo "  ! Pull failed — skipping repository."
                echo
                continue
            fi
        else
            echo "  ! No upstream tracking branch; skipping pull."
        fi
    else
        echo "  -> Cloning ${repo_url}"
        if retry_git git clone --recursive "${repo_url}" "${target_dir}"; then
            echo "  -> Clone OK."
        else
            echo "  ! Clone failed — skipping."
            echo
            continue
        fi
    fi

    if [[ -f "${target_dir}/.gitmodules" ]]; then
        echo "  -> Updating submodules..."
        if ! retry_git git -C "${target_dir}" submodule update --init --recursive; then
            echo "  ! Submodule update failed — continuing."
        fi
    fi

    echo
done < "${REPOS_FILE}"

echo "All repositories processed."