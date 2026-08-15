#!/usr/bin/env bash
# Refresh one existing repository from its configured upstream.
#
# Exit status is part of the contract:
#   0  upstream was already incorporated (or an allowed source snapshot)
#   1  the current branch was fast-forwarded
#   2  repository/upstream configuration is invalid
#   3  fetch or fast-forward failed

set -uo pipefail

allow_snapshot=false
directory="."

usage() {
    cat <<'P101_USAGE'
Usage: refresh-repo.sh [--allow-snapshot] [directory]

Explicitly refresh the current branch's configured upstream and merge it with
--ff-only. The upstream branch ref is fetched with a forced refspec so this
does not depend on a stale or incomplete remote.origin.fetch configuration.

Exit status:
  0  already current, locally ahead, or an allowed source snapshot
  1  fast-forwarded successfully
  2  invalid repository or upstream configuration
  3  fetch or fast-forward failure
P101_USAGE
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --allow-snapshot)
            allow_snapshot=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            printf 'Error: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
        *)
            directory="$1"
            shift
            break
            ;;
    esac
done

if [[ "$#" -ne 0 ]]; then
    printf 'Error: expected at most one repository directory.\n' >&2
    usage >&2
    exit 2
fi

retry_git() {
    local attempts="${P101_GIT_RETRY_ATTEMPTS:-5}"
    local delay_seconds="${P101_GIT_RETRY_DELAY_SECONDS:-5}"
    local attempt=1

    while true; do
        if "$@"; then
            return 0
        fi
        if (( attempt >= attempts )); then
            printf 'Git fetch failed after %d attempt(s).\n' "$attempts" >&2
            return 1
        fi
        printf 'Git fetch failed; retrying in %s second(s) (%d/%d)...\n' \
            "$delay_seconds" "$attempt" "$attempts" >&2
        sleep "$delay_seconds"
        attempt=$((attempt + 1))
    done
}

requested_root="$(CDPATH='' cd -- "$directory" 2>/dev/null && pwd -P || true)"
repository_root="$(git -C "$directory" rev-parse --show-toplevel 2>/dev/null || true)"
resolved_repository_root=""
if [[ -n "$repository_root" ]]; then
    resolved_repository_root="$(CDPATH='' cd -- "$repository_root" 2>/dev/null && pwd -P || true)"
fi

name="${requested_root##*/}"
if [[ -z "$requested_root" || -z "$resolved_repository_root" || "$requested_root" != "$resolved_repository_root" ]]; then
    if $allow_snapshot; then
        printf '%s is a source snapshot without usable Git metadata; skipping refresh.\n' "${name:-repository}"
        exit 0
    fi
    printf 'Error: %s is not the root of a usable Git repository.\n' "$directory" >&2
    exit 2
fi

branch="$(git -C "$requested_root" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if [[ -z "$branch" ]]; then
    printf 'Error: %s is on a detached HEAD.\n' "$name" >&2
    exit 2
fi

remote="$(git -C "$requested_root" config --get "branch.${branch}.remote" || true)"
merge_ref="$(git -C "$requested_root" config --get "branch.${branch}.merge" || true)"
if [[ -z "$remote" || -z "$merge_ref" ]]; then
    printf 'Error: %s branch %s has no configured upstream.\n' "$name" "$branch" >&2
    exit 2
fi

case "$merge_ref" in
    refs/heads/*) ;;
    *)
        printf 'Error: unsupported upstream ref for %s: %s\n' "$name" "$merge_ref" >&2
        exit 2
        ;;
esac

if [[ "$remote" == "." ]]; then
    upstream_ref="$merge_ref"
else
    upstream_ref="refs/remotes/${remote}/${merge_ref#refs/heads/}"
    if ! git -C "$requested_root" show-ref --verify --quiet "$upstream_ref"; then
        remote_probe_status=0
        git -C "$requested_root" ls-remote --exit-code "$remote" "$merge_ref" \
            >/dev/null 2>&1 || remote_probe_status=$?
        if [[ "$remote_probe_status" -eq 2 ]]; then
            printf '%s has no published %s yet; preserving local commit for first publication.\n' \
                "$name" "$merge_ref"
            exit 0
        fi
    fi
    if ! retry_git git -C "$requested_root" fetch --tags --prune "$remote" "+${merge_ref}:${upstream_ref}"; then
        printf 'Error: failed to refresh %s from %s.\n' "$name" "$remote" >&2
        exit 3
    fi
fi

before="$(git -C "$requested_root" rev-parse HEAD)" || exit 2
if ! git -C "$requested_root" merge --ff-only --quiet "$upstream_ref"; then
    printf 'Error: cannot fast-forward %s to %s; resolve local divergence or conflicts.\n' "$name" "$upstream_ref" >&2
    exit 3
fi
after="$(git -C "$requested_root" rev-parse HEAD)" || exit 2

if [[ "$before" != "$after" ]]; then
    printf '%s fast-forwarded from %.12s to %.12s.\n' "$name" "$before" "$after"
    exit 1
fi

ahead="$(git -C "$requested_root" rev-list --count "${upstream_ref}..HEAD")" || exit 2
if (( ahead > 0 )); then
    printf '%s is ahead of %s by %d commit(s); no merge required.\n' "$name" "$upstream_ref" "$ahead"
else
    printf '%s is already up to date with %s.\n' "$name" "$upstream_ref"
fi
exit 0
