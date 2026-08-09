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

dry_run=0
push_args=(--yes)

usage()
{
    cat <<'EOF'
Usage: ./distribution/publish-workspace.sh [options]

Publish managed repositories, refresh the workspace lock and stack contract,
and publish the scripts repository, in that order.

  -n, --dry-run       Show what would happen; change nothing remote.
  --skip-preflight    Pass through to push-repos.sh.
  -h, --help          Show this help.
EOF
}

while (($# > 0)); do
    case "$1" in
        -n | --dry-run)
            dry_run=1
            push_args+=(--dry-run)
            ;;
        --skip-preflight)
            push_args+=(--skip-preflight)
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

# repos.txt holds url|path|type rows; the path is relative to this directory.
# Parsing it as a whole line silently matched nothing, so every dirty
# repository slipped past the check below and push-repos.sh refused instead.
repositories=()
while IFS='|' read -r url relative_path _kind <&3; do
    [[ "${url:-}" != \#* ]] || continue
    relative_path="$(printf '%s' "${relative_path:-}" | tr -d '[:space:]')"
    [[ -n "$relative_path" ]] || continue
    repositories+=("$relative_path")
done 3< repos.txt

blocked=0
for repository in "${repositories[@]}"; do
    [[ -d "$repository/.git" ]] || continue
    if [[ -n "$(git -C "$repository" status --porcelain)" ]]; then
        printf 'DIRTY: %s (review and commit it before publication)\n' "$repository" >&2
        git -C "$repository" status --short >&2
        blocked=1
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
