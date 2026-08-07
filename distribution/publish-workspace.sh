#!/usr/bin/env bash
#
# Publish the whole workspace in the order the contracts require.
#
# The exact-revision lock pins every managed repository, so publication is a
# sequence, not a single push: managed repositories first, then a lock and
# stack-contract refresh, then the scripts repository that carries them.
# Running the steps by hand invites the classic failure where a check run
# stops at workspace-lock because one repository moved after the refresh.

set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

commit_message=""
dry_run=0
push_args=(--yes)

usage()
{
    cat <<'EOF'
Usage: ./distribution/publish-workspace.sh [options]

Publish managed repositories, refresh the workspace lock and stack contract,
and publish the scripts repository, in that order.

  -m, --message TEXT  First commit every dirty managed repository with TEXT.
                      Without this, dirty repositories stop publication.
  -n, --dry-run       Show what would happen; change nothing remote.
  --skip-preflight    Pass through to push-repos.sh.
  --sweep-locks       Remove stale .git lock files older than one minute
                      before starting. Only safe when no git command runs.
  -h, --help          Show this help.
EOF
}

sweep_locks=0
while (($# > 0)); do
    case "$1" in
        -m | --message)
            commit_message="${2:?--message needs text}"
            shift
            ;;
        -n | --dry-run)
            dry_run=1
            push_args+=(--dry-run)
            ;;
        --skip-preflight)
            push_args+=(--skip-preflight)
            ;;
        --sweep-locks)
            sweep_locks=1
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

workspace="$(cd .. && pwd -P)"

if ((sweep_locks)); then
    find "$workspace" -path '*/.git/*' -name '*.lock' -mmin +1 -print -delete
fi

repositories=()
while IFS= read -r line; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [[ -n "$line" ]] || continue
    repositories+=("$workspace/$line")
done < repos.txt

blocked=0
for repository in "${repositories[@]}"; do
    [[ -d "$repository/.git" ]] || continue
    if [[ -n "$(git -C "$repository" status --porcelain)" ]]; then
        if [[ -n "$commit_message" ]]; then
            printf '== committing %s\n' "${repository#"$workspace"/}"
            if ((dry_run)); then
                git -C "$repository" status --short
            else
                git -C "$repository" add -A
                git -C "$repository" commit -m "$commit_message"
            fi
        else
            printf 'DIRTY: %s (commit it, or rerun with -m)\n' "${repository#"$workspace"/}" >&2
            blocked=1
        fi
    fi
done
if ((blocked)); then
    exit 1
fi

./distribution/push-repos.sh "${push_args[@]}"

printf '== refreshing workspace lock and stack contract\n'
./workspace/repos-lock.py refresh
./workspace/stack-contract.py refresh

if [[ -n "$(git status --porcelain -- repos.lock contracts/p101-stack-contract.json)" ]]; then
    if ((dry_run)); then
        printf '(dry run) would commit and push the refreshed lock and contract\n'
    else
        git add repos.lock contracts/p101-stack-contract.json
        git commit -m "Refresh workspace lock and stack contract"
    fi
fi

if ((dry_run)); then
    git push --dry-run
else
    git push
fi

printf '== workspace published\n'
