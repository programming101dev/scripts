#!/usr/bin/env bash
set -euo pipefail

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
    *" --help "*|*" -h "*)
        cat <<'P101_USAGE'
Usage: clone-repos.sh [--interactive] [--latest]

Clone missing repositories and align existing repositories to repos.lock.
The lock is the default so one invocation uses one immutable workspace
revision set.

  -i, --interactive
      Pause after a repository-refresh failure. Resolve the repository in
      another terminal, then press Enter to retry that repository. Enter q to
      abort. Local changes are never discarded or stashed automatically.
  --latest
      Ignore repos.lock and fast-forward configured upstream branches from
      repos.txt. This is an explicit development mode; refresh repos.lock
      afterward before running strict workspace acceptance.
P101_USAGE
        exit 0
        ;;
esac

interactive=false
latest=false
interaction_aborted=false
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -i|--interactive)
            interactive=true
            shift
            ;;
        --latest)
            latest=true
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
LOCK_FILE="repos.lock"
LOCK_HELPER="./workspace/repos-lock.py"
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

retry_clone() {
    local repo_url="$1"
    local target_dir="$2"
    local attempt=1

    while true; do
        if git clone --recursive "$repo_url" "$target_dir"; then
            return 0
        fi
        rm -rf -- "$target_dir"
        if ((attempt >= GIT_RETRY_ATTEMPTS)); then
            echo "  ! Git clone failed after ${GIT_RETRY_ATTEMPTS} attempts." >&2
            return 1
        fi
        echo "  ! Git clone failed. Retrying in ${GIT_RETRY_DELAY_SECONDS} seconds (${attempt}/${GIT_RETRY_ATTEMPTS})..." >&2
        sleep "$GIT_RETRY_DELAY_SECONDS"
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
            interaction_aborted=true
            return "${refresh_status}"
        fi
        case "${answer}" in
            q|Q|quit|QUIT)
                printf 'Aborting at repository refresh: %s\n' "${target_dir}" >&2
                interaction_aborted=true
                return "${refresh_status}"
                ;;
        esac
        printf 'Retrying repository refresh: %s\n\n' "${target_dir}" >&2
    done
}

align_repository() {
    local target_dir="$1"
    local expected_commit="$2"
    local align_status
    local answer

    while true; do
        align_status=0
        align_locked_repository "${target_dir}" "${expected_commit}" || align_status=$?
        if [[ "${align_status}" -eq 0 ]]; then
            return 0
        fi
        if ! ${interactive}; then
            return "${align_status}"
        fi

        printf '\nFAILED: align %s to locked revision %s (exit %d).\n' \
            "${target_dir}" "${expected_commit:0:12}" "${align_status}" >&2
        printf 'Resolve the repository, then press Enter to retry it; enter q to abort: ' >&2
        if ! IFS= read -r answer; then
            printf '\nInteractive input closed; aborting.\n' >&2
            interaction_aborted=true
            return "${align_status}"
        fi
        case "${answer}" in
            q|Q|quit|QUIT)
                printf 'Aborting at locked repository alignment: %s\n' "${target_dir}" >&2
                interaction_aborted=true
                return "${align_status}"
                ;;
        esac
        printf 'Retrying locked repository alignment: %s\n\n' "${target_dir}" >&2
    done
}

align_locked_repository() {
    local target_dir="$1"
    local expected_commit="$2"
    local current_commit
    local branch

    current_commit="$(git -C "${target_dir}" rev-parse HEAD)" || return 2
    if [[ "${current_commit}" == "${expected_commit}" ]]; then
        echo "  -> Locked revision already checked out: ${expected_commit:0:12}"
        return 0
    fi
    if [[ -n "$(git -C "${target_dir}" status --porcelain=v1 --untracked-files=normal)" ]]; then
        echo "  ! Cannot align a modified worktree to locked ${expected_commit:0:12}." >&2
        if git -C "${target_dir}" merge-base --is-ancestor "${expected_commit}" "${current_commit}"; then
            echo "  ! The worktree is based on the lock but contains newer development work." >&2
            echo "  ! Abort and rerun the calling command with --latest to preserve and build it." >&2
        fi
        return 3
    fi
    echo "  -> Fetching locked revision ${expected_commit:0:12}..."
    retry_git git -C "${target_dir}" fetch --tags --prune origin || return 3
    if ! git -C "${target_dir}" cat-file -e "${expected_commit}^{commit}" 2>/dev/null; then
        echo "  ! Locked commit is not available from origin: ${expected_commit}" >&2
        return 3
    fi
    if ! git -C "${target_dir}" merge-base --is-ancestor "${current_commit}" "${expected_commit}"; then
        echo "  ! Current HEAD ${current_commit:0:12} cannot fast-forward to locked ${expected_commit:0:12}." >&2
        echo "  ! Refusing to rewind or discard local commits." >&2
        return 3
    fi
    branch="$(git -C "${target_dir}" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [[ -n "${branch}" ]]; then
        git -C "${target_dir}" merge --ff-only --quiet "${expected_commit}" || return 3
    else
        git -C "${target_dir}" checkout --quiet --detach "${expected_commit}" || return 3
    fi
    echo "  -> Aligned to locked revision ${expected_commit:0:12}."
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

repository_input="${REPOS_FILE}"
lock_snapshot=""
manifest_snapshot=""
if ! ${latest}; then
    if [[ ! -f "${LOCK_FILE}" || ! -x "${LOCK_HELPER}" ]]; then
        echo "Error: locked refresh requires ${LOCK_FILE} and ${LOCK_HELPER}." >&2
        exit 1
    fi
    lock_snapshot="$(mktemp "${TMPDIR:-/tmp}/p101-repos-lock.XXXXXX")"
    manifest_snapshot="${lock_snapshot}.manifest"
    repository_input="${lock_snapshot}.entries"
    trap 'rm -f -- "${lock_snapshot}" "${manifest_snapshot}" "${repository_input}"' EXIT
    cp -- "${LOCK_FILE}" "${lock_snapshot}"
    cp -- "${REPOS_FILE}" "${manifest_snapshot}"
    if ! "${LOCK_HELPER}" --manifest "${manifest_snapshot}" --lock "${lock_snapshot}" entries > "${repository_input}"; then
        echo "Error: repository lock validation failed." >&2
        exit 1
    fi
    echo "Using locked workspace revisions:"
    echo "  lock_sha256=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "${lock_snapshot}")"
fi

failures=0
processed=0
failed_repositories=()
failure_reasons=()

record_failure() {
    local repository="$1"
    local reason="$2"

    failures=$((failures + 1))
    failed_repositories+=("${repository}")
    failure_reasons+=("${reason}")
}

# Read repos.txt on fd 3 so children (git credential prompts, ssh host-key
# confirmations, submodule hooks) keep the real stdin instead of silently
# consuming the remaining lines of the list.
while IFS= read -r raw <&3 || [[ -n "${raw:-}" ]]; do
    local_line="${raw%%#*}"
    line="$(trim_whitespace "${local_line}")"

    if [[ -z "${line}" ]]; then
        continue
    fi

    IFS='|' read -r repo_url target_dir repo_type locked_commit <<< "${line}"

    repo_url="$(trim_whitespace "${repo_url:-}")"
    target_dir="$(trim_whitespace "${target_dir:-}")"
    repo_type="$(trim_whitespace "${repo_type:-}")"
    locked_commit="$(trim_whitespace "${locked_commit:-}")"

    if [[ -z "${repo_url}" || -z "${target_dir}" || -z "${repo_type}" ]]; then
        echo "FAIL: malformed line: ${raw}" >&2
        record_failure "<manifest>" "malformed entry: ${raw}"
        continue
    fi
    case "${repo_type}" in
        c|cxx|c-reference|python|c-bootstrap) ;;
        *)
            echo "FAIL: unsupported repo type '${repo_type}': ${target_dir}" >&2
            record_failure "${target_dir:-<manifest>}" \
                "unsupported repository type '${repo_type}'"
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
            record_failure "${target_dir}" "path exists but is not a Git repository"
            echo
            continue
        fi

        current_origin="$(git -C "${target_dir}" remote get-url origin 2>/dev/null || echo "")"

        if [[ -n "${current_origin}" && "${current_origin}" != "${repo_url}" ]]; then
            echo "  ! Origin mismatch:"
            echo "     current: ${current_origin}"
            echo "     wanted : ${repo_url}"
            record_failure "${target_dir}" \
                "origin mismatch: current=${current_origin} wanted=${repo_url}"
            echo
            continue
        elif [[ -z "${current_origin}" ]]; then
            echo "  ! Missing origin remote; expected ${repo_url}."
            record_failure "${target_dir}" "missing origin remote; expected ${repo_url}"
            echo
            continue
        fi

        if ! ${latest}; then
            if [[ -z "${locked_commit}" && "${repo_type}" != "c-bootstrap" ]]; then
                echo "  ! Missing locked commit: ${target_dir}" >&2
                record_failure "${target_dir}" "non-bootstrap repository has no locked commit"
                echo
                continue
            fi
            if [[ -z "${locked_commit}" ]]; then
                if git -C "${target_dir}" rev-parse --verify HEAD >/dev/null 2>&1; then
                    echo "  ! Bootstrap repository now has a commit; refresh repos.lock." >&2
                    record_failure "${target_dir}" \
                        "bootstrap repository has a commit but repos.lock records it as empty"
                    echo
                    continue
                fi
                echo "  -> Empty bootstrap repository matches the lock."
                echo
                continue
            fi
            align_status=0
            align_repository "${target_dir}" "${locked_commit}" || align_status=$?
            if [[ "${align_status}" -ne 0 ]]; then
                if ${interaction_aborted}; then
                    exit "${align_status}"
                fi
                echo "  ! Locked repository alignment failed (exit ${align_status})."
                record_failure "${target_dir}" \
                    "locked revision alignment failed (exit ${align_status})"
                echo
                continue
            fi
        elif [[ "${repo_type}" == "c-bootstrap" ]] &&
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
                if ${interaction_aborted}; then
                    exit "${refresh_status}"
                fi
                echo "  ! Repository refresh failed (exit ${refresh_status})."
                echo "  ! Resolve manually in ${target_dir}."
                record_failure "${target_dir}" \
                    "upstream refresh failed (exit ${refresh_status})"
                echo
                continue
            fi
        fi
    else
        echo "  -> Cloning ${repo_url}"
        if retry_clone "${repo_url}" "${target_dir}"; then
            echo "  -> Clone OK."
            if ! ${latest} && [[ -z "${locked_commit}" ]]; then
                if git -C "${target_dir}" rev-parse --verify HEAD >/dev/null 2>&1; then
                    echo "  ! Bootstrap repository now has a commit; refresh repos.lock." >&2
                    record_failure "${target_dir}" \
                        "newly cloned bootstrap repository has a commit but repos.lock records it as empty"
                    echo
                    continue
                fi
                echo "  -> Empty bootstrap repository matches the lock."
            elif ! ${latest} && [[ "$(git -C "${target_dir}" rev-parse HEAD)" != "${locked_commit}" ]]; then
                if ! git -C "${target_dir}" checkout --quiet --detach "${locked_commit}"; then
                    echo "  ! Could not check out locked commit ${locked_commit}."
                    record_failure "${target_dir}" \
                        "could not check out locked commit ${locked_commit}"
                    echo
                    continue
                fi
                echo "  -> Checked out locked revision ${locked_commit:0:12}."
            fi
        else
            echo "  ! Clone failed."
            record_failure "${target_dir}" "clone failed from ${repo_url}"
            echo
            continue
        fi
    fi

    if [[ -f "${target_dir}/.gitmodules" ]]; then
        echo "  -> Updating submodules..."
        if ! retry_git git -C "${target_dir}" submodule update --init --recursive; then
            echo "  ! Submodule update failed."
            record_failure "${target_dir}" "submodule update failed"
        fi
    fi

    echo
done 3< "${repository_input}"

if (( processed == 0 )); then
    echo "Error: ${REPOS_FILE} did not contain any repositories." >&2
    exit 1
fi
if (( failures > 0 )); then
    failure_index=0
    printf '\nFailed repositories:\n' >&2
    while (( failure_index < ${#failed_repositories[@]} )); do
        printf '  - %s: %s\n' \
            "${failed_repositories[${failure_index}]}" \
            "${failure_reasons[${failure_index}]}" >&2
        failure_index=$((failure_index + 1))
    done
    echo "Repository update failed: ${failures} problem(s)." >&2
    exit 1
fi
if ! ${latest}; then
    if ! cmp -s "${LOCK_FILE}" "${lock_snapshot}" ||
       ! cmp -s "${REPOS_FILE}" "${manifest_snapshot}"; then
        echo "Repository manifest or lock changed while this refresh was running; refusing mixed revisions." >&2
        exit 1
    fi
    "${LOCK_HELPER}" --manifest "${manifest_snapshot}" --lock "${lock_snapshot}" verify
fi
echo "All ${processed} repositories processed successfully."
