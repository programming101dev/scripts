#!/usr/bin/env bash
#
# Publish committed changes for repositories listed in repos.txt.
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

usage()
{
    cat <<'EOF'
Usage: ./push-repos.sh [--yes] [--dry-run] [--skip-preflight]

Push clean, ahead-only repositories from repos.txt to their configured
upstreams. Before any push, run the strict local GitHub Actions preflight.
The scripts repository is always excluded.

  -n, --dry-run       Ask Git to validate each push without changing a remote.
  -y, --yes           Skip the confirmation question.
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

if [[ ! -f repos.txt ]]; then
    printf 'Error: %s/repos.txt does not exist.\n' "$PWD" >&2
    exit 2
fi

repositories=()
states=()

while IFS='|' read -r _url relative_path _kind; do
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

    git -C "$repository" fetch --quiet --prune origin
    state=$(git -C "$repository" status --short --branch | sed -n '1p')
    case "$state" in
        *diverged* | *behind*)
            printf 'Error: repository is not ahead-only: %s (%s)\n' \
                "$repository" "$state" >&2
            exit 2
            ;;
        *ahead* | *gone*)
            repositories+=("$repository")
            states+=("$state")
            ;;
    esac
done < repos.txt

printf 'scripts repository: EXCLUDED\n'

if ((${#repositories[@]} == 0)); then
    printf 'Nothing to push; every managed repository is current.\n'
    exit 0
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
    printf '\nWARNING: GitHub Actions preflight explicitly disabled.\n\n' >&2
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

for index in "${!repositories[@]}"; do
    repository=${repositories[$index]}
    state=${states[$index]}
    printf '==> %s\n' "$repository"
    if [[ "$state" == *gone* ]]; then
        if [[ $dry_run -eq 1 ]]; then
            git -C "$repository" push --dry-run -u origin main
        else
            git -C "$repository" push -u origin main
        fi
    elif [[ $dry_run -eq 1 ]]; then
        git -C "$repository" push --dry-run
    else
        git -C "$repository" push
    fi
done

printf 'Push operation completed for %d repositories.\n' "${#repositories[@]}"
