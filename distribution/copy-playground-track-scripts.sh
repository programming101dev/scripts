#!/usr/bin/env bash
# Materialize the canonical runner and repository policy files into every
# standalone playground track.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

usage() {
    printf 'Usage: %s [-c] [-n]\n' "$0"
    printf '  -c  check only; fail when a track runner or policy file is missing or stale\n'
    printf '  -n  dry run; report changes without writing\n'
}
case " $* " in
    *" --help "*|*" -h "*) usage; exit 0 ;;
esac

check_only=0
dry_run=0
while getopts ':cnh' opt; do
    case "$opt" in
        c) check_only=1; dry_run=1 ;;
        n) dry_run=1 ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
shift $((OPTIND - 1))
[[ $# -eq 0 ]] || { usage >&2; exit 2; }

canonical="../playgrounds/track-runner.sh"
policy_root="../playgrounds"
tracks_dir="../playgrounds/tracks"
[[ -f "$canonical" && -x "$canonical" ]] || {
    printf 'FAIL: canonical track runner is missing or not executable: %s\n' "$canonical" >&2
    exit 1
}
[[ -d "$tracks_dir" ]] || {
    printf 'FAIL: playground tracks directory is missing: %s\n' "$tracks_dir" >&2
    exit 1
}

failures=0
changes=0
found=0
for track in "$tracks_dir"/[0-9][0-9]-*; do
    [[ -d "$track" ]] || continue
    found=$((found + 1))
    runner="$track/run.sh"
    if ! { [[ -f "$runner" ]] && cmp -s "$canonical" "$runner" && [[ -x "$runner" ]]; }; then
        if [[ "$check_only" -eq 1 ]]; then
            printf 'FAIL: stale or missing playground runner: %s\n' "$runner" >&2
            failures=$((failures + 1))
        elif [[ "$dry_run" -eq 1 ]]; then
            printf '[dry-run] update: %s\n' "$runner"
            changes=$((changes + 1))
        else
            cp "$canonical" "$runner"
            chmod +x "$runner"
            printf 'Updated: %s\n' "$runner"
            changes=$((changes + 1))
        fi
    fi
    for policy_name in .gitignore LICENSE; do
        source_policy="$policy_root/$policy_name"
        track_policy="$track/$policy_name"
        [[ -f "$source_policy" ]] || {
            printf 'FAIL: canonical playground policy file is missing: %s\n' "$source_policy" >&2
            exit 1
        }
        if [[ -f "$track_policy" ]] && cmp -s "$source_policy" "$track_policy"; then
            continue
        fi
        if [[ "$check_only" -eq 1 ]]; then
            printf 'FAIL: stale or missing playground policy file: %s\n' "$track_policy" >&2
            failures=$((failures + 1))
        elif [[ "$dry_run" -eq 1 ]]; then
            printf '[dry-run] update: %s\n' "$track_policy"
            changes=$((changes + 1))
        else
            cp "$source_policy" "$track_policy"
            printf 'Updated: %s\n' "$track_policy"
            changes=$((changes + 1))
        fi
    done
done

[[ "$found" -gt 0 ]] || {
    echo "FAIL: no playground tracks found." >&2
    exit 1
}
if [[ "$failures" -gt 0 ]]; then
    printf 'Playground runner distribution failed: %d problem(s).\n' "$failures" >&2
    exit 1
fi
if [[ "$check_only" -eq 1 ]]; then
    printf 'PASS: %d playground tracks match the canonical runner and policy files.\n' "$found"
elif [[ "$dry_run" -eq 1 ]]; then
    printf '(dry-run) %d track runner(s) would change.\n' "$changes"
else
    printf 'Done: %d track runner(s) updated.\n' "$changes"
fi
