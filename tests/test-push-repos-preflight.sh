#!/usr/bin/env bash
# Prove that managed repositories cannot be pushed before the required
# GitHub Actions preflight succeeds.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-push-preflight.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/scripts/distribution" "$sandbox/libraries"
cp distribution/push-repos.sh "$sandbox/scripts/distribution/push-repos.sh"
chmod +x "$sandbox/scripts/distribution/push-repos.sh"

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

printf 'PASS: managed repository pushes require a successful GitHub Actions preflight.\n'
