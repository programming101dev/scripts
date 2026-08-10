#!/usr/bin/env bash
# Prove that managed repositories cannot be pushed before the required
# GitHub Actions preflight succeeds.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-push-preflight.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/scripts/distribution" "$sandbox/libraries"
cp distribution/push-repos.sh "$sandbox/scripts/distribution/push-repos.sh"
cp distribution/publish-workspace.sh "$sandbox/scripts/distribution/publish-workspace.sh"
chmod +x "$sandbox/scripts/distribution/push-repos.sh"
chmod +x "$sandbox/scripts/distribution/publish-workspace.sh"

if grep -Fq 'git add -A' distribution/publish-workspace.sh; then
  echo "publication path must not select source files for a commit" >&2
  exit 1
fi
if grep -Eq 'find .*\.git.*-delete' distribution/publish-workspace.sh; then
  echo "publication path must not delete Git lock files" >&2
  exit 1
fi

remote="$sandbox/lib_one.git"
repository="$sandbox/libraries/lib_one"
git init --quiet --bare "$remote"
git clone --quiet "$remote" "$repository"
git -C "$repository" config user.name "p101 push test"
git -C "$repository" config user.email "push-test@invalid.example"
printf 'published\n' > "$repository/value.txt"
git -C "$repository" add value.txt
git -C "$repository" commit --quiet -m published
git -C "$repository" branch -M main
git -C "$repository" push --quiet -u origin main
published="$(git -C "$repository" rev-parse HEAD)"

printf 'candidate\n' > "$repository/value.txt"
git -C "$repository" add value.txt
git -C "$repository" commit --quiet -m candidate
candidate="$(git -C "$repository" rev-parse HEAD)"
printf '%s|../libraries/lib_one|c\n' "$remote" > "$sandbox/scripts/repos.txt"

cat > "$sandbox/scripts/preflight.sh" <<'EOF'
#!/usr/bin/env bash
printf 'preflight invoked\n' >> preflight.calls
exit 7
EOF
chmod +x "$sandbox/scripts/preflight.sh"

set +e
(
  cd "$sandbox/scripts"
  P101_PUSH_PREFLIGHT=./preflight.sh ./distribution/push-repos.sh --yes
) > "$sandbox/refused.log" 2>&1
status=$?
set -e
[[ "$status" -eq 7 ]]
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$published" ]]
grep -Fq 'Running required GitHub Actions preflight before any push' "$sandbox/refused.log"

cat > "$sandbox/scripts/preflight.sh" <<'EOF'
#!/usr/bin/env bash
printf 'preflight invoked\n' >> preflight.calls
exit 0
EOF
chmod +x "$sandbox/scripts/preflight.sh"
(
  cd "$sandbox/scripts"
  P101_PUSH_PREFLIGHT=./preflight.sh ./distribution/push-repos.sh --yes
) > "$sandbox/pushed.log" 2>&1
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$candidate" ]]
grep -Fq 'GitHub Actions preflight: PASS' "$sandbox/pushed.log"

printf 'override\n' > "$repository/value.txt"
git -C "$repository" add value.txt
git -C "$repository" commit --quiet -m override
override="$(git -C "$repository" rev-parse HEAD)"
cat > "$sandbox/scripts/preflight.sh" <<'EOF'
#!/usr/bin/env bash
exit 9
EOF
chmod +x "$sandbox/scripts/preflight.sh"
(
  cd "$sandbox/scripts"
  P101_PUSH_PREFLIGHT=./preflight.sh \
    ./distribution/push-repos.sh --yes --skip-preflight
) > "$sandbox/override.log" 2>&1
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$override" ]]
grep -Fq 'preflight explicitly disabled' "$sandbox/override.log"

printf 'uncommitted\n' > "$repository/value.txt"
set +e
(
  cd "$sandbox/scripts"
  P101_PUSH_PREFLIGHT=./preflight.sh \
    ./distribution/push-repos.sh --yes --dry-run
) > "$sandbox/dirty-push.log" 2>&1
dirty_push_status=$?
(
  cd "$sandbox/scripts"
  ./distribution/publish-workspace.sh --dry-run
) > "$sandbox/dirty-publish.log" 2>&1
dirty_publish_status=$?
set -e
[[ "$dirty_push_status" -eq 2 ]]
[[ "$dirty_publish_status" -eq 1 ]]
grep -Fq 'uncommitted changes' "$sandbox/dirty-push.log"
grep -Fq 'review and commit it before publication' "$sandbox/dirty-publish.log"
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$override" ]]
[[ "$(git -C "$repository" rev-parse HEAD)" == "$override" ]]

git -C "$repository" checkout -- value.txt
mkdir -p "$sandbox/scripts/workspace"
cat > "$sandbox/scripts/workspace/repos-lock.py" <<'EOF'
#!/usr/bin/env bash
touch dry-run-mutated-lock
exit 99
EOF
cat > "$sandbox/scripts/workspace/stack-contract.py" <<'EOF'
#!/usr/bin/env bash
touch dry-run-mutated-contract
exit 99
EOF
chmod +x "$sandbox/scripts/workspace/repos-lock.py"
chmod +x "$sandbox/scripts/workspace/stack-contract.py"
scripts_remote="$sandbox/scripts.git"
git init --quiet --bare "$scripts_remote"
git -C "$sandbox/scripts" init --quiet
git -C "$sandbox/scripts" config user.name "p101 push test"
git -C "$sandbox/scripts" config user.email "push-test@invalid.example"
git -C "$sandbox/scripts" add .
git -C "$sandbox/scripts" commit --quiet -m scripts
git -C "$sandbox/scripts" branch -M main
git -C "$sandbox/scripts" remote add origin "$scripts_remote"
git -C "$sandbox/scripts" push --quiet -u origin main
(
  cd "$sandbox/scripts"
  ./distribution/publish-workspace.sh --dry-run --skip-preflight
) > "$sandbox/publication-dry-run.log" 2>&1
[[ ! -e "$sandbox/scripts/dry-run-mutated-lock" ]]
[[ ! -e "$sandbox/scripts/dry-run-mutated-contract" ]]
grep -Fq 'would refresh and commit the workspace lock' "$sandbox/publication-dry-run.log"

printf 'PASS: publication requires clean, precommitted repositories and a successful preflight.\n'
