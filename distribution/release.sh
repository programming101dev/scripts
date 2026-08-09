#!/usr/bin/env bash
#
# Release the workspace.
#
# A release publishes revisions that were deliberately committed beforehand
# and re-derives every hash that pins them. It never chooses files for a
# commit: review boundaries belong to the person preparing each repository.
#
# This program implements no publication policy of its own. It coordinates the
# programs that already own each step:
#
#   distribution/publish-workspace.sh  require clean managed repositories, run
#                                      the preflight, push them, refresh
#                                      repos.lock and the stack contract, and
#                                      publish the scripts repository last
#   distribution/push-repos.sh         (called by the above) the strict
#                                      GitHub Actions preflight and the pushes
#   workspace/repos-lock.py            the exact-revision pin
#   workspace/stack-contract.py        the byte-hashed policy artifacts
#
# What it adds is a clean-workspace precondition and a post-publication audit.
# The audit is the point. A release that pushed everything but left the lock pointing at a
# revision that is not on the remote is not a release; it is a trap for the
# next machine to clone.

set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

dry_run=0
publish_args=()

usage()
{
    cat <<'EOF'
Usage: ./distribution/release.sh [options]

Publish the already-committed workspace, refresh the hashes that pin it, and
prove the result is reproducible from the lock. Dirty repositories are always
rejected; this command never runs git add or creates source commits.

  -n, --dry-run       Show what would happen; change nothing remote and
                      create no commits.
      --skip-preflight
                      Skip the strict local GitHub Actions preflight. The
                      remote jobs become the first strict check.
  -h, --help          Show this help.

Order matters and is not configurable: managed repositories are published
before the lock and the stack contract are refreshed, because the refresh
records where they landed, and the scripts repository is published last,
because it is what carries that record.
EOF
}

while (($# > 0)); do
    case "$1" in
        -n | --dry-run)
            dry_run=1
            publish_args+=(--dry-run)
            ;;
        --skip-preflight)
            publish_args+=(--skip-preflight)
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            printf 'Error: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

step=0

announce()
{
    step=$((step + 1))
    printf '\n== %d. %s\n' "$step" "$1"
}

# ---------------------------------------------------------------------------
# Preconditions. Each of these fails late and confusingly if left to chance:
# an unset identity aborts the first commit after the preflight has already
# run, and a detached scripts checkout produces a push with nowhere to go.
# ---------------------------------------------------------------------------

announce 'checking preconditions'

if ! git var GIT_AUTHOR_IDENT > /dev/null 2>&1; then
    printf 'Error: git has no author identity; set user.name and user.email.\n' >&2
    exit 2
fi

if ! scripts_branch="$(git symbolic-ref --quiet --short HEAD)"; then
    printf 'Error: the scripts repository is detached; check out a branch.\n' >&2
    exit 2
fi

if ! git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' > /dev/null 2>&1; then
    printf 'Error: the scripts repository has no upstream for %s.\n' "$scripts_branch" >&2
    exit 2
fi

printf 'identity: %s\n' "$(git var GIT_AUTHOR_IDENT)"
printf 'scripts branch: %s\n' "$scripts_branch"

# A release candidate is a set of revisions, not a set of working-tree bytes.
# Refuse every uncommitted change rather than silently deciding its commit.

if [[ -n "$(git status --porcelain)" ]]; then
    printf 'Error: the scripts repository has uncommitted changes.\n' >&2
    git status --short >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Publication. Everything below this line is publish-workspace.sh's contract.
# ---------------------------------------------------------------------------

announce 'publishing the workspace'
./distribution/publish-workspace.sh "${publish_args[@]}"

# ---------------------------------------------------------------------------
# The audit. Everything above reports its own success; none of it proves the
# property the next machine actually depends on, which is that the lock names
# revisions that exist on the remotes and match what is on this disk.
# ---------------------------------------------------------------------------

if ((dry_run)); then
    announce 'skipping the release audit (dry run)'
    printf '(dry run) nothing was published, so there is nothing to audit\n'
    exit 0
fi

announce 'auditing the release'

./workspace/repos-lock.py verify --require-clean
./workspace/stack-contract.py verify

# Read the entries up front. In a process substitution a failing generator
# would leave the loop with nothing to read and the audit would pass by
# reading zero repositories, which is the opposite of what it is for.
locked_entries="$(./workspace/repos-lock.py entries)"

drift=0
while IFS='|' read -r _url relative_path _kind locked_commit <&3; do
    [[ -n "${relative_path:-}" ]] || continue

    head_commit="$(git -C "$relative_path" rev-parse HEAD)"
    if [[ "$head_commit" != "$locked_commit" ]]; then
        printf 'UNPINNED: %s is at %s; the lock says %s\n' \
            "$relative_path" "${head_commit:0:12}" "${locked_commit:0:12}" >&2
        drift=1
        continue
    fi

    upstream="$(git -C "$relative_path" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
    if [[ -z "$upstream" ]]; then
        printf 'UNTRACKED: %s has no upstream; the lock cannot be cloned from it\n' \
            "$relative_path" >&2
        drift=1
        continue
    fi

    remote_commit="$(git -C "$relative_path" rev-parse "$upstream")"
    if [[ "$remote_commit" != "$locked_commit" ]]; then
        printf 'UNPUSHED: %s pins %s, but %s is at %s\n' \
            "$relative_path" "${locked_commit:0:12}" "$upstream" "${remote_commit:0:12}" >&2
        drift=1
    fi
done 3<<< "$locked_entries"

if [[ -n "$(git status --porcelain)" ]]; then
    printf 'DIRTY: the scripts repository still has local changes after the release\n' >&2
    git status --short >&2
    drift=1
fi

scripts_head="$(git rev-parse HEAD)"
scripts_upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}')"
if [[ "$scripts_head" != "$(git rev-parse "$scripts_upstream")" ]]; then
    printf 'UNPUSHED: scripts is at %s, but %s is at %s\n' \
        "${scripts_head:0:12}" "$scripts_upstream" \
        "$(git rev-parse "$scripts_upstream" | cut -c1-12)" >&2
    drift=1
fi

if ((drift)); then
    printf '\nRelease audit FAILED. The workspace was published, but another\n' >&2
    printf 'machine cloning at this lock would not reproduce it.\n' >&2
    exit 1
fi

printf '\n== released\n'
printf 'scripts %s at %s\n' "$scripts_branch" "${scripts_head:0:12}"
printf 'Every managed repository is clean, pushed, and pinned by repos.lock.\n'
