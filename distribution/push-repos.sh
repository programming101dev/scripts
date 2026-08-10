#!/usr/bin/env bash
#
# Publish committed changes for repositories listed in repos.txt. The atomic
# workspace path supplies --candidate so this low-level mechanism pushes exact
# preflighted commit/ref pairs instead of rediscovering moving HEAD revisions.
#
# The scripts repository is intentionally excluded: repos.txt describes the
# repositories managed by scripts, not scripts itself. This program has no
# option that adds scripts to the publication set.

set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

assume_yes=0
dry_run=0
run_preflight=1
preflight_script="${P101_PUSH_PREFLIGHT:-./github-actions/preflight.sh}"
candidate_receipt=""
candidate_validation=""
candidate_ref=""
qualification_receipt=""
skip_qualification=0

usage()
{
    cat <<'EOF'
Usage: ./push-repos.sh [--yes] [--dry-run] [--candidate <receipt>] [qualification options]

Stage or promote clean exact repositories from repos.txt. Normal default-branch
promotion requires an immutable candidate and three-platform qualification.
The moving-HEAD mode is emergency-only. The scripts repository is always
excluded.

  -n, --dry-run       Ask Git to validate each push without changing a remote.
  -y, --yes           Skip the confirmation question.
  --candidate <path>  Push only the exact commits in a validated immutable
                      workspace-candidate receipt. A passed candidate supplies
                      the required preflight evidence.
  --candidate-ref <ref>
                      Stage exact candidate commits on the temporary
                      refs/heads/p101-candidate/... qualification ref. Default
                      branches are not changed.
  --qualification <path>
                      Required three-platform qualification receipt before a
                      candidate may move default branches.
  --skip-qualification
                      Explicit emergency bypass of remote qualification.
  --skip-preflight    Explicitly bypass the local GitHub Actions preflight.
  -h, --help          Show this help.
EOF
}

while (($# > 0)); do
    case "$1" in
        -n | --dry-run)
            dry_run=1
            ;;
        -y | --yes)
            assume_yes=1
            ;;
        --candidate)
            candidate_receipt="${2:?Error: --candidate requires a receipt}"
            shift
            ;;
        --candidate-ref)
            candidate_ref="${2:?Error: --candidate-ref requires a ref}"
            shift
            ;;
        --qualification)
            qualification_receipt="${2:?Error: --qualification requires a receipt}"
            shift
            ;;
        --skip-qualification)
            skip_qualification=1
            ;;
        --skip-preflight)
            run_preflight=0
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

if [[ -n "$candidate_ref" && -z "$candidate_receipt" ]]; then
    printf 'Error: --candidate-ref requires --candidate.\n' >&2
    exit 2
fi
if [[ -n "$candidate_ref" && -n "$qualification_receipt" ]]; then
    printf 'Error: --candidate-ref and --qualification are mutually exclusive.\n' >&2
    exit 2
fi
if [[ -z "$candidate_receipt" && $dry_run -eq 0 && $skip_qualification -eq 0 ]]; then
    printf 'Error: moving managed default branches requires an immutable qualified candidate.\n' >&2
    printf 'Use publish-workspace.sh; --skip-qualification is emergency-only.\n' >&2
    exit 2
fi

if [[ ! -f repos.txt ]]; then
    printf 'Error: %s/repos.txt does not exist.\n' "$PWD" >&2
    exit 2
fi

repositories=()
states=()
branches=()
commits=()
remote_refs=()
upstream_commits=()

if [[ -n "$candidate_receipt" ]]; then
    candidate_receipt="$(CDPATH='' cd -- "$(dirname -- "$candidate_receipt")" && pwd -P)/$(basename -- "$candidate_receipt")"
    candidate_validation="$(
        ./workspace/repos-lock.py candidate-status \
            --candidate "$candidate_receipt"
    )"
    if [[ "$candidate_validation" != "passed" && "$run_preflight" -eq 1 ]]; then
        printf 'Error: candidate validation was bypassed; use --skip-preflight only for an explicit emergency publication.\n' >&2
        exit 2
    fi
    ./workspace/repos-lock.py verify-candidate \
        --candidate "$candidate_receipt" \
        --allow-scripts-descendant
    IFS='|' read -r _candidate_id _candidate_lock \
        _candidate_stack_contract expected_candidate_ref < <(
        ./workspace/repos-lock.py candidate-qualification \
            --candidate "$candidate_receipt"
    )
    if [[ -n "$candidate_ref" && "$candidate_ref" != "$expected_candidate_ref" ]]; then
        printf 'Error: candidate ref %s does not match immutable candidate ref %s.\n' \
            "$candidate_ref" "$expected_candidate_ref" >&2
        exit 2
    fi
    if [[ -z "$candidate_ref" ]]; then
        if [[ -n "$qualification_receipt" ]]; then
            qualification_receipt="$(
                CDPATH='' cd -- "$(dirname -- "$qualification_receipt")" && pwd -P
            )/$(basename -- "$qualification_receipt")"
            ./workspace/repos-lock.py verify-qualification \
                --candidate "$candidate_receipt" \
                --qualification "$qualification_receipt"
        elif ((skip_qualification == 0)); then
            printf 'Error: default-branch candidate publication requires --qualification.\n' >&2
            printf 'Use --candidate-ref to stage qualification or --skip-qualification only for an emergency.\n' >&2
            exit 2
        fi
    fi
    while IFS='|' read -r relative_path commit branch remote_ref upstream_commit <&3; do
        [[ -n "${relative_path:-}" ]] || continue
        repositories+=("$relative_path")
        states+=("candidate")
        branches+=("$branch")
        commits+=("$commit")
        remote_refs+=("$remote_ref")
        upstream_commits+=("$upstream_commit")
    done 3< <(
        ./workspace/repos-lock.py candidate-entries \
            --candidate "$candidate_receipt"
    )
    run_preflight=0
else
  while IFS='|' read -r _url relative_path _kind <&3; do
      [[ -n "${relative_path:-}" ]] || continue
      [[ "${_url:-}" != \#* ]] || continue

      repository=$relative_path
      if [[ ! -d "$repository/.git" ]]; then
          printf 'Error: repository is missing: %s\n' "$repository" >&2
          exit 2
      fi
      if [[ -n "$(git -C "$repository" status --porcelain)" ]]; then
          printf 'Error: repository has uncommitted changes: %s\n' "$repository" >&2
          exit 2
      fi

      branch="$(git -C "$repository" symbolic-ref --quiet --short HEAD)" || {
          printf 'Error: repository is detached: %s\n' "$repository" >&2
          exit 2
      }
      git -C "$repository" fetch --quiet --prune origin </dev/null
      upstream="$(
          git -C "$repository" rev-parse \
              --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null ||
              true
      )"
      if [[ -n "$upstream" ]]; then
          read -r behind ahead < <(
              git -C "$repository" rev-list \
                  --left-right --count "$upstream...HEAD"
          )
          if ((behind > 0)); then
              printf 'Error: repository is not ahead-only: %s (%s behind, %s ahead)\n' \
                  "$repository" "$behind" "$ahead" >&2
              exit 2
          fi
          if ((ahead > 0)); then
              repositories+=("$repository")
              states+=("tracked")
              branches+=("$branch")
              commits+=("")
              remote_refs+=("")
              upstream_commits+=("")
          fi
          continue
      fi

      configured_remote="$(
          git -C "$repository" config --get "branch.$branch.remote" ||
              true
      )"
      configured_merge="$(
          git -C "$repository" config --get "branch.$branch.merge" ||
              true
      )"
      if [[ "$configured_remote" == "origin" && -n "$configured_merge" ]]; then
          repositories+=("$repository")
          states+=("gone")
          branches+=("$branch")
          commits+=("")
          remote_refs+=("")
          upstream_commits+=("")
      else
          printf 'Error: repository has no configured upstream: %s (%s)\n' \
              "$repository" "$branch" >&2
          exit 2
      fi
  done 3< repos.txt
fi

printf 'scripts repository: EXCLUDED\n'

if ((${#repositories[@]} == 0)); then
    printf 'Nothing to push; every managed repository is current.\n'
    exit 0
fi

if [[ -n "$candidate_receipt" ]]; then
    for index in "${!repositories[@]}"; do
        repository=${repositories[$index]}
        commit=${commits[$index]}
        remote_ref=${remote_refs[$index]}
        upstream_commit=${upstream_commits[$index]}
        git -C "$repository" fetch --quiet --prune origin </dev/null
        remote_branch=${remote_ref#refs/heads/}
        current_remote="$(
            git -C "$repository" rev-parse --verify \
                "refs/remotes/origin/$remote_branch" 2>/dev/null || true
        )"
        if [[ "$current_remote" == "$commit" ]]; then
            default_state="published"
        elif [[ "$current_remote" == "$upstream_commit" ]]; then
            default_state="pending"
        else
            printf 'Error: remote moved after candidate validation: %s (%s, expected base %s or candidate %s)\n' \
                "$repository" "${current_remote:-absent}" \
                "${upstream_commit:-absent}" "$commit" >&2
            exit 2
        fi
        if [[ -n "$candidate_ref" ]]; then
            candidate_remote_branch=${candidate_ref#refs/heads/}
            current_candidate_ref="$(
                git -C "$repository" rev-parse --verify \
                    "refs/remotes/origin/$candidate_remote_branch" 2>/dev/null || true
            )"
            if [[ "$current_candidate_ref" == "$commit" ]]; then
                states[$index]="candidate-ref-published"
            elif [[ -z "$current_candidate_ref" ]]; then
                states[$index]="candidate-ref-pending"
            else
                printf 'Error: candidate qualification ref moved: %s (%s, expected %s or absent)\n' \
                    "$repository" "$current_candidate_ref" "$commit" >&2
                exit 2
            fi
        elif [[ "$default_state" == "published" ]]; then
            states[$index]="candidate-published"
        else
            states[$index]="candidate-pending"
        fi
    done
fi

printf 'Repositories selected for %s:\n' \
    "$([[ $dry_run -eq 1 ]] && printf 'dry-run validation' || printf 'push')"
for repository in "${repositories[@]}"; do
    printf '  %s\n' "$repository"
done

if [[ $run_preflight -eq 1 ]]; then
    if [[ ! -x "$preflight_script" ]]; then
        printf 'Error: GitHub Actions preflight is unavailable: %s\n' \
            "$preflight_script" >&2
        exit 2
    fi
    printf '\nRunning required GitHub Actions preflight before any push...\n'
    "$preflight_script"
    printf 'GitHub Actions preflight: PASS\n\n'
else
    if [[ -n "$candidate_receipt" && "$candidate_validation" == "passed" ]]; then
        printf '\nImmutable candidate preflight evidence: PASS\n'
        if [[ -n "$qualification_receipt" ]]; then
            printf 'Three-platform candidate qualification: PASS\n\n'
        elif [[ -n "$candidate_ref" ]]; then
            printf 'Mode: temporary candidate qualification refs only\n\n'
        elif ((skip_qualification)); then
            printf 'WARNING: three-platform qualification explicitly disabled.\n\n' >&2
        else
            printf '\n'
        fi
    else
        printf '\nWARNING: GitHub Actions preflight explicitly disabled.\n\n' >&2
    fi
fi

if [[ $assume_yes -eq 0 ]]; then
    printf 'Continue? [y/N] '
    IFS= read -r answer
    case "$answer" in
        y | Y | yes | YES | Yes)
            ;;
        *)
            printf 'Cancelled.\n'
            exit 1
            ;;
    esac
fi

push_failures=0
for index in "${!repositories[@]}"; do
    repository=${repositories[$index]}
    state=${states[$index]}
    branch=${branches[$index]}
    printf '==> %s\n' "$repository"
    if [[ "$state" == "candidate-ref-published" ]]; then
        printf 'Candidate qualification ref already at %.12s; skipping.\n' \
            "${commits[$index]}"
    elif [[ "$state" == "candidate-ref-pending" ]]; then
        commit=${commits[$index]}
        if [[ $dry_run -eq 1 ]]; then
            if ! git -C "$repository" push --dry-run origin \
                "$commit:$candidate_ref"; then
                push_failures=$((push_failures + 1))
            fi
        elif ! git -C "$repository" push origin "$commit:$candidate_ref"; then
            push_failures=$((push_failures + 1))
        fi
    elif [[ "$state" == "candidate-published" ]]; then
        printf 'Already published at validated commit %.12s; skipping.\n' \
            "${commits[$index]}"
    elif [[ "$state" == "candidate-pending" ]]; then
        commit=${commits[$index]}
        remote_ref=${remote_refs[$index]}
        if [[ $dry_run -eq 1 ]]; then
            if ! git -C "$repository" push --dry-run origin \
                "$commit:$remote_ref"; then
                push_failures=$((push_failures + 1))
            fi
        elif [[ -z "${upstream_commits[$index]}" ]]; then
            if ! git -C "$repository" push -u origin "$commit:$remote_ref"; then
                push_failures=$((push_failures + 1))
            fi
        elif ! git -C "$repository" push origin "$commit:$remote_ref"; then
            push_failures=$((push_failures + 1))
        fi
    elif [[ "$state" == *gone* ]]; then
        if [[ $dry_run -eq 1 ]]; then
            if ! git -C "$repository" push --dry-run -u origin "$branch"; then
                push_failures=$((push_failures + 1))
            fi
        else
            if ! git -C "$repository" push -u origin "$branch"; then
                push_failures=$((push_failures + 1))
            fi
        fi
    elif [[ $dry_run -eq 1 ]]; then
        if ! git -C "$repository" push --dry-run; then
            push_failures=$((push_failures + 1))
        fi
    else
        if ! git -C "$repository" push; then
            push_failures=$((push_failures + 1))
        fi
    fi
done

if ((push_failures > 0)); then
    printf 'Push operation failed for %d of %d repositories.\n' \
        "$push_failures" "${#repositories[@]}" >&2
    exit 1
fi

printf 'Push operation completed for %d repositories.\n' "${#repositories[@]}"
