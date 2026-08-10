#!/usr/bin/env bash
#
# Publish the whole workspace in the order the contracts require.
#
# Git hosting cannot mutate independent repositories atomically. This command
# supplies the useful substitute: validate one immutable candidate, preflight
# every selected remote, push exact commit/ref pairs, and retain a resumable
# receipt until scripts publishes the candidate lock and stack contract last.

set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

dry_run=0
skip_preflight=0
transaction_dir=""
resume_candidate=""
preflight_script="${P101_PUSH_PREFLIGHT:-./github-actions/preflight.sh}"
github_cli="${P101_GITHUB_CLI:-gh}"
qualification_receipt=""

usage()
{
    cat <<'EOF'
Usage: ./distribution/publish-workspace.sh [options]

Validate one immutable workspace candidate, publish its exact managed-repository
commits, refresh the workspace lock and stack contract, then publish scripts.

  -n, --dry-run       Show what would happen; change nothing remote.
  -o, --output <dir>  Durable transaction evidence directory.
  --resume <receipt>  Resume the exact candidate after a partial publication.
  --qualification <receipt>
                      Use an already-downloaded three-platform qualification
                      receipt for this exact candidate.
  --skip-preflight    Record an explicit unvalidated emergency candidate.
  -h, --help          Show this help.
EOF
}

while (($# > 0)); do
    case "$1" in
        -n | --dry-run)
            dry_run=1
            ;;
        -o | --output)
            transaction_dir="${2:?Error: --output requires a directory}"
            shift
            ;;
        --resume)
            resume_candidate="${2:?Error: --resume requires a candidate receipt}"
            shift
            ;;
        --qualification)
            qualification_receipt="${2:?Error: --qualification requires a receipt}"
            shift
            ;;
        --skip-preflight)
            skip_preflight=1
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

create_qualification_commit()
{
    local parent_commit="$1"
    local candidate_lock_file="$2"
    local candidate_stack_contract="$3"
    local candidate_identity="$4"
    local candidate_lock_digest="$5"
    local index_file
    local lock_blob
    local stack_contract_blob
    local tree
    local author_name
    local author_email
    local author_date
    local committer_name
    local committer_email
    local committer_date

    index_file="$(mktemp "${TMPDIR:-/tmp}/p101-qualification-index.XXXXXX")"
    rm -f -- "$index_file"
    GIT_INDEX_FILE="$index_file" git read-tree "$parent_commit"
    lock_blob="$(git hash-object -w "$candidate_lock_file")"
    stack_contract_blob="$(git hash-object -w "$candidate_stack_contract")"
    GIT_INDEX_FILE="$index_file" git update-index \
        --add --cacheinfo "100644,$lock_blob,repos.lock"
    GIT_INDEX_FILE="$index_file" git update-index \
        --add --cacheinfo \
        "100644,$stack_contract_blob,contracts/p101-stack-contract.json"
    tree="$(GIT_INDEX_FILE="$index_file" git write-tree)"
    rm -f -- "$index_file"

    author_name="$(git show -s --format=%an "$parent_commit")"
    author_email="$(git show -s --format=%ae "$parent_commit")"
    author_date="$(git show -s --format=%aI "$parent_commit")"
    committer_name="$(git show -s --format=%cn "$parent_commit")"
    committer_email="$(git show -s --format=%ce "$parent_commit")"
    committer_date="$(git show -s --format=%cI "$parent_commit")"
    printf 'Qualify workspace candidate\n\nCandidate: %s\nLock-SHA256: %s\n' \
        "$candidate_identity" "$candidate_lock_digest" |
        GIT_AUTHOR_NAME="$author_name" \
        GIT_AUTHOR_EMAIL="$author_email" \
        GIT_AUTHOR_DATE="$author_date" \
        GIT_COMMITTER_NAME="$committer_name" \
        GIT_COMMITTER_EMAIL="$committer_email" \
        GIT_COMMITTER_DATE="$committer_date" \
        git commit-tree "$tree" -p "$parent_commit"
}

prepare_qualification_stack_contract()
{
    local parent_commit="$1"
    local candidate_lock_file="$2"
    local output_contract="$3"
    local worktree
    local status=0

    worktree="$(mktemp -d "$PWD/../.p101-qualification-scripts.XXXXXX")"
    rmdir -- "$worktree"
    git worktree add --quiet --detach "$worktree" "$parent_commit"
    if ! cp -- "$candidate_lock_file" "$worktree/repos.lock"; then
        status=2
    elif ! "$worktree/workspace/stack-contract.py" \
        --scripts-root "$worktree" \
        --contract "$output_contract" refresh; then
        status=2
    fi
    git worktree remove --force "$worktree"
    return "$status"
}

verify_qualification_commit()
{
    local qualification_commit="$1"
    local parent_commit="$2"
    local candidate_lock_file="$3"
    local candidate_stack_contract="$4"
    local observed_parent
    local changed_paths
    local expected_blob
    local observed_blob
    local expected_stack_blob
    local observed_stack_blob

    observed_parent="$(git rev-parse "$qualification_commit^")"
    if [[ "$observed_parent" != "$parent_commit" ]]; then
        printf 'Error: qualification commit does not directly descend from the admitted scripts commit.\n' >&2
        return 2
    fi
    changed_paths="$(
        git diff-tree --no-commit-id --name-only -r \
            "$parent_commit" "$qualification_commit"
    )"
    if [[ "$changed_paths" != $'contracts/p101-stack-contract.json\nrepos.lock' ]]; then
        printf 'Error: qualification commit must change only repos.lock and its stack contract.\n' >&2
        return 2
    fi
    expected_blob="$(git hash-object "$candidate_lock_file")"
    observed_blob="$(git rev-parse "$qualification_commit:repos.lock")"
    if [[ "$observed_blob" != "$expected_blob" ]]; then
        printf 'Error: qualification commit does not contain the candidate lock.\n' >&2
        return 2
    fi
    expected_stack_blob="$(git hash-object "$candidate_stack_contract")"
    observed_stack_blob="$(
        git rev-parse \
            "$qualification_commit:contracts/p101-stack-contract.json"
    )"
    if [[ "$observed_stack_blob" != "$expected_stack_blob" ]]; then
        printf 'Error: qualification commit does not contain the candidate stack contract.\n' >&2
        return 2
    fi
}

stage_scripts_qualification_ref()
{
    local candidate_ref="$1"
    local qualification_commit="$2"
    local admitted_commit="$3"
    local scripts_remote_ref="$4"
    local admitted_upstream="$5"
    local remote_branch
    local current_default
    local current_candidate
    local current_local
    local push_arguments=()

    git fetch --quiet --prune origin </dev/null
    remote_branch=${scripts_remote_ref#refs/heads/}
    current_default="$(
        git rev-parse --verify "refs/remotes/origin/$remote_branch" \
            2>/dev/null || true
    )"
    current_local="$(git rev-parse HEAD)"
    if [[ "$current_default" != "$admitted_upstream" && \
          "$current_default" != "$admitted_commit" && \
          "$current_default" != "$current_local" ]]; then
        printf 'Error: scripts remote moved before candidate qualification (%s, expected %s, %s, or safe local completion %s).\n' \
            "${current_default:-absent}" "${admitted_upstream:-absent}" \
            "$admitted_commit" "$current_local" >&2
        return 2
    fi
    current_candidate="$(git ls-remote origin "$candidate_ref" | awk 'NR == 1 { print $1 }')"
    if [[ -n "$current_candidate" && "$current_candidate" != "$qualification_commit" ]]; then
        printf 'Error: scripts candidate qualification ref points at another commit: %s\n' \
            "$current_candidate" >&2
        return 2
    fi
    if [[ "$current_candidate" == "$qualification_commit" ]]; then
        printf 'scripts candidate qualification ref already staged at %.12s.\n' \
            "$qualification_commit"
        return 0
    fi
    if ((dry_run)); then
        push_arguments+=(--dry-run)
    fi
    git push "${push_arguments[@]}" origin "$qualification_commit:$candidate_ref"
}

run_remote_qualification()
{
    local candidate_identity="$1"
    local candidate_lock_digest="$2"
    local candidate_stack_contract_digest="$3"
    local candidate_ref="$4"
    local qualification_commit="$5"
    local output_receipt="$6"
    local github_repository
    local workflow_name
    local branch
    local run_id=""
    local attempt
    local download_dir
    local downloaded_receipt

    if ! command -v "$github_cli" >/dev/null 2>&1; then
        printf 'Error: GitHub CLI is required for three-platform qualification: %s\n' \
            "$github_cli" >&2
        return 2
    fi
    github_repository="$(
        "$github_cli" repo view --json nameWithOwner --jq .nameWithOwner
    )"
    workflow_name="${P101_GITHUB_WORKFLOW:-p101-stack.yml}"
    branch=${candidate_ref#refs/heads/}
    printf '== dispatching three-platform candidate qualification\n'
    "$github_cli" workflow run "$workflow_name" \
        --repo "$github_repository" \
        --ref "$branch" \
        -f target_os=all \
        -f "candidate_id=$candidate_identity" \
        -f "candidate_lock_sha256=$candidate_lock_digest" \
        -f "candidate_stack_contract_sha256=$candidate_stack_contract_digest" \
        -f "qualification_ref=$candidate_ref"
    attempt=1
    while ((attempt <= 30)); do
        run_id="$(
            "$github_cli" run list \
                --repo "$github_repository" \
                --workflow "$workflow_name" \
                --branch "$branch" \
                --event workflow_dispatch \
                --commit "$qualification_commit" \
                --limit 1 \
                --json databaseId \
                --jq '.[0].databaseId // empty'
        )"
        [[ -z "$run_id" ]] || break
        sleep 2
        attempt=$((attempt + 1))
    done
    if [[ -z "$run_id" ]]; then
        printf 'Error: dispatched qualification run was not observable for commit %s.\n' \
            "$qualification_commit" >&2
        return 2
    fi
    printf 'GitHub Actions qualification run: %s\n' "$run_id"
    "$github_cli" run watch "$run_id" \
        --repo "$github_repository" --exit-status
    download_dir="$(dirname -- "$output_receipt")/github-run-$run_id"
    rm -rf -- "$download_dir"
    mkdir -p "$download_dir"
    "$github_cli" run download "$run_id" \
        --repo "$github_repository" \
        --name workspace-qualification \
        --dir "$download_dir"
    downloaded_receipt="$download_dir/workspace-qualification.json"
    if [[ ! -f "$downloaded_receipt" ]]; then
        printf 'Error: qualification run did not publish workspace-qualification.json.\n' >&2
        return 2
    fi
    cp -- "$downloaded_receipt" "$output_receipt"
    ./workspace/repos-lock.py verify-qualification \
        --candidate "$candidate_receipt" \
        --qualification "$output_receipt" \
        --scripts-commit "$qualification_commit" \
        --github-run-id "$run_id"
}

cleanup_candidate_refs()
{
    local candidate_ref="$1"
    local qualification_commit="$2"
    local cleanup_failures=0
    local repository
    local commit
    local _branch
    local _remote_ref
    local _upstream
    local current_candidate

    while IFS='|' read -r repository commit _branch _remote_ref _upstream <&3; do
        [[ -n "${repository:-}" ]] || continue
        current_candidate="$(
            git -C "$repository" ls-remote origin "$candidate_ref" |
                awk 'NR == 1 { print $1 }'
        )"
        if [[ -z "$current_candidate" ]]; then
            continue
        fi
        if [[ "$current_candidate" != "$commit" ]]; then
            printf 'REFUSED cleanup: %s candidate ref changed to %s\n' \
                "$repository" "$current_candidate" >&2
            cleanup_failures=$((cleanup_failures + 1))
            continue
        fi
        git -C "$repository" push origin ":$candidate_ref" ||
            cleanup_failures=$((cleanup_failures + 1))
    done 3< <(
        ./workspace/repos-lock.py candidate-entries \
            --candidate "$candidate_receipt"
    )
    current_candidate="$(git ls-remote origin "$candidate_ref" | awk 'NR == 1 { print $1 }')"
    if [[ -n "$current_candidate" && "$current_candidate" != "$qualification_commit" ]]; then
        printf 'REFUSED cleanup: scripts candidate ref changed to %s\n' \
            "$current_candidate" >&2
        cleanup_failures=$((cleanup_failures + 1))
    elif [[ "$current_candidate" == "$qualification_commit" ]]; then
        git push origin ":$candidate_ref" ||
            cleanup_failures=$((cleanup_failures + 1))
    fi
    if ((cleanup_failures)); then
        printf 'Candidate publication completed, but %d temporary ref cleanup(s) failed.\n' \
            "$cleanup_failures" >&2
        return 1
    fi
}

if [[ -n "$resume_candidate" && -n "$transaction_dir" ]]; then
    printf 'Error: --resume and --output are mutually exclusive.\n' >&2
    exit 2
fi

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

if [[ -n "$resume_candidate" ]]; then
    candidate_receipt="$(
        CDPATH='' cd -- "$(dirname -- "$resume_candidate")" && pwd -P
    )/$(basename -- "$resume_candidate")"
    transaction_dir="$(dirname -- "$candidate_receipt")"
    candidate_validation="$(
        ./workspace/repos-lock.py candidate-status \
            --candidate "$candidate_receipt"
    )"
    if [[ "$candidate_validation" != "passed" && "$skip_preflight" -eq 0 ]]; then
        printf 'Error: resuming a bypassed candidate requires --skip-preflight.\n' >&2
        exit 2
    fi
elif [[ -z "$transaction_dir" ]]; then
    mkdir -p target/workspace-transactions
    transaction_dir="$(mktemp -d "$PWD/target/workspace-transactions/candidate.XXXXXX")"
else
    mkdir -p "$transaction_dir"
    transaction_dir="$(CDPATH='' cd -- "$transaction_dir" && pwd -P)"
    if [[ -n "$(find "$transaction_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        printf 'Error: transaction output directory is not empty: %s\n' \
            "$transaction_dir" >&2
        exit 2
    fi
fi

candidate_lock="$transaction_dir/repos.candidate.lock"
if [[ -z "$resume_candidate" ]]; then
    candidate_receipt="$transaction_dir/workspace-candidate.json"
fi

if [[ -n "$resume_candidate" ]]; then
    printf '== resuming immutable workspace candidate\n'
elif ((skip_preflight)); then
    printf 'WARNING: creating an immutable candidate without validation evidence.\n' >&2
    ./workspace/repos-lock.py --lock "$candidate_lock" refresh \
        --require-clean --allow-ahead
    candidate_stack_contract="$transaction_dir/p101-stack-contract.candidate.json"
    prepare_qualification_stack_contract \
        "$(git rev-parse HEAD)" "$candidate_lock" \
        "$candidate_stack_contract"
    ./workspace/repos-lock.py --lock "$candidate_lock" candidate \
        --receipt "$candidate_receipt" \
        --candidate-stack-contract "$candidate_stack_contract" \
        --bypass-validation
else
    if [[ ! -x "$preflight_script" ]]; then
        printf 'Error: GitHub Actions preflight is unavailable: %s\n' \
            "$preflight_script" >&2
        exit 2
    fi
    "$preflight_script" -o "$transaction_dir"
fi

verify_candidate_arguments=(
    verify-candidate
    --candidate "$candidate_receipt"
)
if [[ -n "$resume_candidate" ]]; then
    verify_candidate_arguments+=(--allow-scripts-descendant)
fi
./workspace/repos-lock.py "${verify_candidate_arguments[@]}"

IFS='|' read -r candidate_identity candidate_lock_digest \
    candidate_stack_contract_digest candidate_ref < <(
    ./workspace/repos-lock.py candidate-qualification \
        --candidate "$candidate_receipt"
)
IFS='|' read -r admitted_scripts_commit _scripts_branch scripts_remote_ref \
    admitted_scripts_upstream < <(
        ./workspace/repos-lock.py candidate-scripts \
            --candidate "$candidate_receipt"
    )

if ((skip_preflight)); then
    push_arguments=(
        --yes
        --candidate "$candidate_receipt"
        --skip-preflight
        --skip-qualification
    )
    if ((dry_run)); then
        push_arguments+=(--dry-run)
    fi
    ./distribution/push-repos.sh "${push_arguments[@]}"
else
    qualification_commit_file="$transaction_dir/scripts-qualification-commit"
    qualification_stack_contract="$transaction_dir/p101-stack-contract.candidate.json"
    if [[ ! -f "$qualification_stack_contract" ]]; then
        prepare_qualification_stack_contract \
            "$admitted_scripts_commit" "$candidate_lock" \
            "$qualification_stack_contract"
    fi
    if [[ -f "$qualification_commit_file" ]]; then
        qualification_commit="$(tr -d '[:space:]' < "$qualification_commit_file")"
    else
        qualification_commit="$(
            create_qualification_commit \
                "$admitted_scripts_commit" "$candidate_lock" \
                "$qualification_stack_contract" \
                "$candidate_identity" "$candidate_lock_digest"
        )"
        printf '%s\n' "$qualification_commit" > "$qualification_commit_file"
    fi
    verify_qualification_commit \
        "$qualification_commit" "$admitted_scripts_commit" "$candidate_lock" \
        "$qualification_stack_contract"

    staging_arguments=(
        --yes
        --candidate "$candidate_receipt"
        --candidate-ref "$candidate_ref"
    )
    if ((dry_run)); then
        staging_arguments+=(--dry-run)
    fi
    ./distribution/push-repos.sh "${staging_arguments[@]}"
    stage_scripts_qualification_ref \
        "$candidate_ref" "$qualification_commit" \
        "$admitted_scripts_commit" "$scripts_remote_ref" \
        "$admitted_scripts_upstream"

    if [[ -n "$qualification_receipt" ]]; then
        qualification_receipt="$(
            CDPATH='' cd -- "$(dirname -- "$qualification_receipt")" && pwd -P
        )/$(basename -- "$qualification_receipt")"
    else
        qualification_receipt="$transaction_dir/workspace-qualification.json"
    fi

    if ((dry_run)); then
        if [[ -f "$qualification_receipt" ]]; then
            ./workspace/repos-lock.py verify-qualification \
                --candidate "$candidate_receipt" \
                --qualification "$qualification_receipt" \
                --scripts-commit "$qualification_commit"
            printf '(dry run) existing three-platform qualification is valid\n'
        fi
        printf '(dry run) validated immutable candidate: %s\n' "$candidate_receipt"
        printf '(dry run) would dispatch Linux, macOS, and FreeBSD qualification for %s\n' \
            "$candidate_ref"
        printf '(dry run) would promote exact candidate commits only after a clean aggregate receipt\n'
        printf '(dry run) would refresh and commit the workspace lock and stack contract\n'
        printf '(dry run) would push scripts last, audit publication, and remove temporary refs\n'
        exit 0
    fi

    if [[ -f "$qualification_receipt" ]]; then
        printf '== reusing candidate-bound three-platform qualification\n'
        ./workspace/repos-lock.py verify-qualification \
            --candidate "$candidate_receipt" \
            --qualification "$qualification_receipt" \
            --scripts-commit "$qualification_commit"
    else
        run_remote_qualification \
            "$candidate_identity" "$candidate_lock_digest" \
            "$candidate_stack_contract_digest" "$candidate_ref" \
            "$qualification_commit" \
            "$qualification_receipt"
    fi
    promotion_arguments=(
        --yes
        --candidate "$candidate_receipt"
        --qualification "$qualification_receipt"
    )
    ./distribution/push-repos.sh "${promotion_arguments[@]}"
fi

if ((dry_run)); then
    printf '(dry run) validated immutable candidate (emergency bypass): %s\n' "$candidate_receipt"
    printf '(dry run) would refresh and commit the workspace lock and stack contract\n'
    printf '(dry run) would push scripts and write the completion receipt\n'
    exit 0
fi

printf '== refreshing workspace lock and stack contract\n'
./workspace/repos-lock.py refresh
./workspace/stack-contract.py refresh

if [[ -n "$(git status --porcelain -- repos.lock contracts/p101-stack-contract.json)" ]]; then
    git add repos.lock contracts/p101-stack-contract.json
    git commit -m "Refresh workspace lock and stack contract"
fi

final_scripts_commit="$(git rev-parse HEAD)"
if ! git merge-base --is-ancestor \
    "$admitted_scripts_commit" "$final_scripts_commit"; then
    printf 'Error: final scripts commit does not descend from the validated candidate.\n' >&2
    exit 2
fi
git fetch --quiet --prune origin </dev/null
scripts_remote_branch=${scripts_remote_ref#refs/heads/}
current_scripts_remote="$(
    git rev-parse --verify "refs/remotes/origin/$scripts_remote_branch" \
        2>/dev/null || true
)"
if [[ "$current_scripts_remote" == "$final_scripts_commit" ]]; then
    printf 'scripts already published at %.12s; continuing audit.\n' \
        "$final_scripts_commit"
elif [[ "$current_scripts_remote" != "$admitted_scripts_upstream" && \
        "$current_scripts_remote" != "$admitted_scripts_commit" ]]; then
    printf 'Error: scripts remote moved after candidate validation (%s, expected base %s or admitted %s).\n' \
        "${current_scripts_remote:-absent}" \
        "${admitted_scripts_upstream:-absent}" \
        "$admitted_scripts_commit" >&2
    exit 2
elif [[ -z "$admitted_scripts_upstream" ]]; then
    git push -u origin "$final_scripts_commit:$scripts_remote_ref"
else
    git push origin "$final_scripts_commit:$scripts_remote_ref"
fi

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

completion_arguments=(
    complete-candidate
    --candidate "$candidate_receipt"
    --receipt "$transaction_dir/completion.json"
)
if ((skip_preflight == 0)); then
    completion_arguments+=(--qualification "$qualification_receipt")
fi
./workspace/repos-lock.py "${completion_arguments[@]}"

if ((skip_preflight == 0)); then
    printf '== removing temporary candidate refs\n'
    cleanup_candidate_refs "$candidate_ref" "$qualification_commit"
fi

printf '== workspace published, pinned, and audited\n'
printf 'Transaction: %s\n' "$candidate_receipt"
printf 'Completion:  %s\n' "$transaction_dir/completion.json"
