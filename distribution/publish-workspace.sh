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

if ! git symbolic-ref --quiet --short HEAD >/dev/null; then
    printf 'Error: the scripts repository is detached.\n' >&2
    exit 2
fi
if ! git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then
    printf 'Error: the scripts repository has no configured upstream.\n' >&2
    exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
    printf 'Error: the scripts repository has uncommitted changes; review and commit them before publication.\n' >&2
    git status --short >&2
    exit 1
fi
if ((dry_run == 0)) && ! git var GIT_AUTHOR_IDENT >/dev/null 2>&1; then
    printf 'Error: git has no author identity; set user.name and user.email.\n' >&2
    exit 2
fi

./distribution/push-repos.sh "${push_args[@]}"

if ((dry_run)); then
    printf '(dry run) would refresh and commit the workspace lock and stack contract\n'
    printf '(dry run) would push the scripts repository and audit the published revisions\n'
    exit 0
fi

printf '== refreshing workspace lock and stack contract\n'
./workspace/repos-lock.py refresh
./workspace/stack-contract.py refresh

if [[ -n "$(git status --porcelain -- repos.lock contracts/p101-stack-contract.json)" ]]; then
    git add repos.lock contracts/p101-stack-contract.json
    git commit -m "Refresh workspace lock and stack contract"
fi

git push

printf '== auditing published workspace\n'
./workspace/repos-lock.py verify --require-clean
./workspace/stack-contract.py verify

locked_entries="$(./workspace/repos-lock.py entries)"
drift=0
while IFS='|' read -r _url relative_path _kind locked_commit <&3; do
    [[ -n "${relative_path:-}" ]] || continue
    head_commit="$(git -C "$relative_path" rev-parse HEAD)"
    upstream="$(git -C "$relative_path" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
    if [[ "$head_commit" != "$locked_commit" ]]; then
        printf 'UNPINNED: %s is at %.12s; lock says %.12s\n' \
            "$relative_path" "$head_commit" "$locked_commit" >&2
        drift=1
    elif [[ -z "$upstream" || "$(git -C "$relative_path" rev-parse "$upstream")" != "$locked_commit" ]]; then
        printf 'UNPUSHED: %s locked revision is not its upstream revision\n' \
            "$relative_path" >&2
        drift=1
    fi
done 3<<< "$locked_entries"

scripts_upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}')"
if [[ -n "$(git status --porcelain)" ]] || [[ "$(git rev-parse HEAD)" != "$(git rev-parse "$scripts_upstream")" ]]; then
    printf 'UNPUBLISHED: scripts is dirty or differs from %s\n' "$scripts_upstream" >&2
    drift=1
fi
if ((drift)); then
    printf 'Workspace publication audit failed.\n' >&2
    exit 1
fi

printf '== workspace published, pinned, and audited\n'
