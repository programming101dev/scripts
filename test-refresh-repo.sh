#!/usr/bin/env bash
# Exercise the shared repository refresh contract without network access.
set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

case " $* " in
    *" --help "*|*" -h "*)
        printf '%s\n' "test-refresh-repo.sh — exercise explicit upstream refresh and fast-forward safety."
        exit 0
        ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-refresh-repo.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

remote="$sandbox/remote.git"
publisher="$sandbox/publisher"
consumer="$sandbox/consumer"

git init --quiet --bare "$remote"
git init --quiet "$publisher"
git -C "$publisher" config user.name "p101 refresh test"
git -C "$publisher" config user.email "refresh-test@invalid.example"
printf 'one\n' > "$publisher/value.txt"
git -C "$publisher" add value.txt
git -C "$publisher" commit --quiet -m one
git -C "$publisher" branch -M main
git -C "$publisher" remote add origin "$remote"
git -C "$publisher" push --quiet -u origin main
git --git-dir="$remote" symbolic-ref HEAD refs/heads/main

git clone --quiet "$remote" "$consumer"
git -C "$consumer" config user.name "p101 refresh test"
git -C "$consumer" config user.email "refresh-test@invalid.example"

# Deliberately make the configured fetch refspec useless. The shared helper
# must refresh the branch named by branch.main.merge explicitly.
git -C "$consumer" config --unset-all remote.origin.fetch
git -C "$consumer" config --add remote.origin.fetch \
    '+refs/heads/not-main:refs/remotes/origin/not-main'

P101_GIT_RETRY_ATTEMPTS=1 ./refresh-repo.sh "$consumer" > "$sandbox/current.out"
grep -Fq 'already up to date' "$sandbox/current.out"

printf 'two\n' >> "$publisher/value.txt"
git -C "$publisher" add value.txt
git -C "$publisher" commit --quiet -m two
git -C "$publisher" push --quiet
published_head="$(git -C "$publisher" rev-parse HEAD)"

status=0
P101_GIT_RETRY_ATTEMPTS=1 ./refresh-repo.sh "$consumer" > "$sandbox/updated.out" || status=$?
[[ "$status" -eq 1 ]]
[[ "$(git -C "$consumer" rev-parse HEAD)" == "$published_head" ]]
grep -Fq 'fast-forwarded from' "$sandbox/updated.out"

printf 'three\n' >> "$publisher/value.txt"
git -C "$publisher" add value.txt
git -C "$publisher" commit --quiet -m three
git -C "$publisher" push --quiet
published_head="$(git -C "$publisher" rev-parse HEAD)"
cp ./pull.sh ./refresh-repo.sh "$consumer/"
status=0
(cd "$sandbox" && "$consumer/pull.sh" > "$sandbox/pull.out") || status=$?
[[ "$status" -eq 1 ]]
[[ "$(git -C "$consumer" rev-parse HEAD)" == "$published_head" ]]

printf 'four\n' >> "$publisher/value.txt"
git -C "$publisher" add value.txt
git -C "$publisher" commit --quiet -m four
git -C "$publisher" push --quiet
published_head="$(git -C "$publisher" rev-parse HEAD)"
mkdir "$sandbox/clone-driver"
cp ./clone-repos.sh ./refresh-repo.sh "$sandbox/clone-driver/"
chmod +x "$sandbox/clone-driver/clone-repos.sh" "$sandbox/clone-driver/refresh-repo.sh"
printf '%s|%s|c\n' "$remote" "$consumer" > "$sandbox/clone-driver/repos.txt"
(cd "$sandbox" && "$sandbox/clone-driver/clone-repos.sh" > "$sandbox/clone.out")
[[ "$(git -C "$consumer" rev-parse HEAD)" == "$published_head" ]]
grep -Fq 'Refreshing configured upstream' "$sandbox/clone.out"

printf 'consumer\n' >> "$consumer/value.txt"
git -C "$consumer" add value.txt
git -C "$consumer" commit --quiet -m consumer
consumer_head="$(git -C "$consumer" rev-parse HEAD)"

printf 'publisher\n' >> "$publisher/value.txt"
git -C "$publisher" add value.txt
git -C "$publisher" commit --quiet -m publisher
git -C "$publisher" push --quiet

status=0
P101_GIT_RETRY_ATTEMPTS=1 ./refresh-repo.sh "$consumer" \
    > "$sandbox/diverged.out" 2> "$sandbox/diverged.err" || status=$?
[[ "$status" -eq 3 ]]
[[ "$(git -C "$consumer" rev-parse HEAD)" == "$consumer_head" ]]
grep -Fq 'cannot fast-forward' "$sandbox/diverged.err"

# Interactive orchestration retries the repository that failed instead of
# aborting the whole update. Use a deterministic fake refresh helper here:
# the first call fails, and the second succeeds after the operator presses
# Enter. The clone driver must not mutate or stash the repository itself.
mkdir "$sandbox/interactive-driver"
cp ./clone-repos.sh "$sandbox/interactive-driver/"
chmod +x "$sandbox/interactive-driver/clone-repos.sh"
printf '%s|%s|c\n' "$remote" "$consumer" \
    > "$sandbox/interactive-driver/repos.txt"
cat > "$sandbox/interactive-driver/refresh-repo.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'refresh\n' >> "$P101_TEST_REFRESH_INVOCATIONS"
if [[ -f "$P101_TEST_REFRESH_FAIL_ONCE" ]]; then
    rm -f "$P101_TEST_REFRESH_FAIL_ONCE"
    exit 3
fi
exit 0
EOF
chmod +x "$sandbox/interactive-driver/refresh-repo.sh"
export P101_TEST_REFRESH_INVOCATIONS="$sandbox/interactive-refresh.txt"
export P101_TEST_REFRESH_FAIL_ONCE="$sandbox/interactive-fail-once"
touch "$P101_TEST_REFRESH_FAIL_ONCE"
printf '\n' | "$sandbox/interactive-driver/clone-repos.sh" --interactive \
    > "$sandbox/interactive.out" 2> "$sandbox/interactive.err"
[[ "$(wc -l < "$P101_TEST_REFRESH_INVOCATIONS")" -eq 2 ]]
grep -Fq 'FAILED: refresh' "$sandbox/interactive.err"
grep -Fq 'Retrying repository refresh' "$sandbox/interactive.err"

mkdir "$sandbox/snapshot"
printf 'gitdir: /definitely/missing/p101-refresh-test\n' > "$sandbox/snapshot/.git"
./refresh-repo.sh --allow-snapshot "$sandbox/snapshot" > "$sandbox/snapshot.out"
grep -Fq 'source snapshot without usable Git metadata' "$sandbox/snapshot.out"

printf 'PASS: shared repository refresh contract\n'
