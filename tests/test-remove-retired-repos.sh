#!/usr/bin/env bash
# Verify manifest-based retired-repository cleanup and its loss-prevention gates.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "test-remove-retired-repos.sh — exercise safe retired-repository cleanup."
    exit 0 ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-retired-repos.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/workspace/scripts/distribution" "$sandbox/workspace/libraries" "$sandbox/remotes"
cp ./distribution/remove-retired-repos.sh "$sandbox/workspace/scripts/distribution/"
chmod +x "$sandbox/workspace/scripts/distribution/remove-retired-repos.sh"

create_repository() {
  local name="$1"
  local remote="$sandbox/remotes/$name.git"
  local publisher="$sandbox/publisher-$name"
  local destination="$sandbox/workspace/libraries/$name"

  git init --quiet --bare "$remote"
  git init --quiet "$publisher"
  git -C "$publisher" config user.name "p101 cleanup test"
  git -C "$publisher" config user.email "cleanup-test@invalid.example"
  printf '%s\n' "$name" > "$publisher/value.txt"
  printf 'build-*\n.env\n' > "$publisher/.gitignore"
  git -C "$publisher" add value.txt .gitignore
  git -C "$publisher" commit --quiet -m initial
  git -C "$publisher" branch -M main
  git -C "$publisher" remote add origin "$remote"
  git -C "$publisher" push --quiet -u origin main
  git --git-dir="$remote" symbolic-ref HEAD refs/heads/main
  git clone --quiet "$remote" "$destination"
}

create_repository active
create_repository retired-clean
create_repository retired-dirty
create_repository retired-secret
ln -s "$sandbox/workspace/libraries/active" \
  "$sandbox/workspace/libraries/retired-symlink"
printf '%s|../libraries/active|c\n' "$sandbox/remotes/active.git" \
  > "$sandbox/workspace/scripts/repos.txt"
printf 'local work\n' >> "$sandbox/workspace/libraries/retired-dirty/value.txt"
mkdir -p "$sandbox/workspace/libraries/retired-clean/build-clang"
printf 'generated\n' > "$sandbox/workspace/libraries/retired-clean/build-clang/output.o"
printf 'credential\n' > "$sandbox/workspace/libraries/retired-secret/.env"

(
  cd "$sandbox/workspace/scripts"
  ./distribution/remove-retired-repos.sh > "$sandbox/dry-run.out" 2> "$sandbox/dry-run.err" || status=$?
  [[ "${status:-0}" -eq 1 ]]
)
[[ -d "$sandbox/workspace/libraries/active" ]]
[[ -d "$sandbox/workspace/libraries/retired-clean" ]]
[[ -d "$sandbox/workspace/libraries/retired-dirty" ]]
[[ -d "$sandbox/workspace/libraries/retired-secret" ]]
[[ -L "$sandbox/workspace/libraries/retired-symlink" ]]
grep -Fq 'WOULD REMOVE:' "$sandbox/dry-run.out"
grep -Fq 'BLOCKED (dirty):' "$sandbox/dry-run.err"
grep -Fq 'BLOCKED (contains ignored files):' "$sandbox/dry-run.err"

(
  cd "$sandbox/workspace/scripts"
  printf 'q\n' | ./distribution/remove-retired-repos.sh --apply --yes --interactive \
    > "$sandbox/apply.out" 2> "$sandbox/apply.err" || status=$?
  [[ "${status:-0}" -eq 1 ]]
)
[[ -d "$sandbox/workspace/libraries/active" ]]
[[ ! -e "$sandbox/workspace/libraries/retired-clean" ]]
[[ -d "$sandbox/workspace/libraries/retired-dirty" ]]
[[ -d "$sandbox/workspace/libraries/retired-secret" ]]
[[ -L "$sandbox/workspace/libraries/retired-symlink" ]]
grep -Fq 'Retired repository cleanup aborted.' "$sandbox/apply.err"

git -C "$sandbox/workspace/libraries/retired-dirty" restore value.txt
(
  cd "$sandbox/workspace/scripts"
  ./distribution/remove-retired-repos.sh --apply --yes > "$sandbox/final.out" \
    2> "$sandbox/final.err" || status=$?
  [[ "${status:-0}" -eq 1 ]]
)
[[ ! -e "$sandbox/workspace/libraries/retired-dirty" ]]
[[ -d "$sandbox/workspace/libraries/retired-secret" ]]
[[ -d "$sandbox/workspace/libraries/active" ]]
[[ -L "$sandbox/workspace/libraries/retired-symlink" ]]

printf 'PASS: retired repositories are removed only when clean and fully pushed\n'
