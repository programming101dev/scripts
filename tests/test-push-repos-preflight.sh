#!/usr/bin/env bash
# Prove that managed repositories cannot be pushed before the required
# GitHub Actions preflight succeeds.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

unset P101_REPOS_LOCK P101_STACK_REPOS_LOCK P101_STACK_CONTRACT

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-push-preflight.XXXXXX")"
cleanup() {
  status=$?
  if [[ "$status" -ne 0 && "${P101_KEEP_TEST_SANDBOX:-0}" == 1 ]]; then
    printf 'Preserved failed test sandbox: %s\n' "$sandbox" >&2
  else
    rm -rf "$sandbox"
  fi
}
trap cleanup EXIT
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
if grep -Fq 'git push "${push_arguments[@]}" origin "$qualification_commit:$candidate_ref"' \
  distribution/publish-workspace.sh; then
  printf 'FAIL: scripts qualification push must support Bash 3.2 with an empty option list\n' >&2
  exit 1
fi
if grep -Fq 'changed_paths" !=' distribution/publish-workspace.sh; then
  printf 'FAIL: qualification verification must admit an already-current lock contract\n' >&2
  exit 1
fi
if ! grep -Fq 'qualification commit changes forbidden path' \
  distribution/publish-workspace.sh; then
  printf 'FAIL: qualification verification must reject paths outside the lock contract\n' >&2
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
) > "$sandbox/unqualified-moving-head.log" 2>&1
unqualified_moving_head_status=$?
set -e
[[ "$unqualified_moving_head_status" -eq 2 ]]
grep -Fq 'requires an immutable qualified candidate' \
  "$sandbox/unqualified-moving-head.log"
[[ ! -e "$sandbox/scripts/preflight.calls" ]]

set +e
(
  cd "$sandbox/scripts"
  P101_PUSH_PREFLIGHT=./preflight.sh ./distribution/push-repos.sh --yes \
    --skip-qualification
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
  P101_PUSH_PREFLIGHT=./preflight.sh ./distribution/push-repos.sh --yes \
    --skip-qualification
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
    ./distribution/push-repos.sh --yes --skip-preflight --skip-qualification
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
cp workspace/repos-lock.py "$sandbox/scripts/workspace/repos-lock.py"
cat > "$sandbox/scripts/workspace/stack-contract.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
contract=""
while (($# > 0)); do
  case "$1" in
    --contract)
      contract="$2"
      shift 2
      ;;
    --scripts-root)
      shift 2
      ;;
    refresh)
      if [[ -n "$contract" ]]; then
        mkdir -p "$(dirname -- "$contract")"
        printf '{"schema":"test-stack-contract"}\n' > "$contract"
        exit 0
      fi
      touch dry-run-mutated-contract
      exit 99
      ;;
    *)
      shift
      ;;
  esac
done
exit 2
EOF
chmod +x "$sandbox/scripts/workspace/repos-lock.py"
chmod +x "$sandbox/scripts/workspace/stack-contract.sh"
printf 'target/\n' > "$sandbox/scripts/.gitignore"
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
[[ ! -e "$sandbox/scripts/dry-run-mutated-contract" ]]
grep -Fq 'validated immutable candidate' "$sandbox/publication-dry-run.log"
grep -Fq 'would refresh and commit the workspace lock' \
  "$sandbox/publication-dry-run.log"

atomic="$sandbox/atomic"
mkdir -p "$atomic/scripts/distribution" "$atomic/scripts/workspace" \
  "$atomic/libraries" "$atomic/evidence"
cp distribution/push-repos.sh "$atomic/scripts/distribution/push-repos.sh"
cp distribution/publish-workspace.sh \
  "$atomic/scripts/distribution/publish-workspace.sh"
cp workspace/repos-lock.py "$atomic/scripts/workspace/repos-lock.py"
cat > "$atomic/scripts/workspace/stack-contract.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
contract=""
while (($# > 0)); do
  case "$1" in
    --contract)
      contract="$2"
      shift 2
      ;;
    --scripts-root)
      shift 2
      ;;
    refresh)
      mkdir -p "$(dirname -- "$contract")"
      printf '{"schema":"test-stack-contract"}\n' > "$contract"
      exit 0
      ;;
    verify)
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done
exit 2
EOF
chmod +x "$atomic/scripts/distribution/push-repos.sh" \
  "$atomic/scripts/distribution/publish-workspace.sh" \
  "$atomic/scripts/workspace/repos-lock.py" \
  "$atomic/scripts/workspace/stack-contract.sh"

atomic_scripts_remote="$atomic/scripts.git"
git init --quiet --bare "$atomic_scripts_remote"
git -C "$atomic/scripts" init --quiet
git -C "$atomic/scripts" config user.name "p101 atomic push test"
git -C "$atomic/scripts" config user.email "atomic-push@invalid.example"
git -C "$atomic/scripts" branch -M main
git -C "$atomic/scripts" remote add origin "$atomic_scripts_remote"

atomic_remote="$atomic/lib_one.git"
atomic_repository="$atomic/libraries/lib_one"
git init --quiet --bare "$atomic_remote"
git clone --quiet "$atomic_remote" "$atomic_repository"
git -C "$atomic_repository" config user.name "p101 atomic push test"
git -C "$atomic_repository" config user.email "atomic-push@invalid.example"
printf 'published\n' > "$atomic_repository/value.txt"
git -C "$atomic_repository" add value.txt
git -C "$atomic_repository" commit --quiet -m published
git -C "$atomic_repository" branch -M main
git -C "$atomic_repository" push --quiet -u origin main
printf 'candidate\n' > "$atomic_repository/value.txt"
git -C "$atomic_repository" add value.txt
git -C "$atomic_repository" commit --quiet -m candidate
atomic_candidate="$(git -C "$atomic_repository" rev-parse HEAD)"

printf '%s|../libraries/lib_one|c\n' "$atomic_remote" \
  > "$atomic/scripts/repos.txt"
git -C "$atomic/scripts" add .
git -C "$atomic/scripts" commit --quiet -m scripts
git -C "$atomic/scripts" push --quiet -u origin main
(
  cd "$atomic/scripts"
  ./workspace/repos-lock.py \
    --lock "$atomic/evidence/repos.candidate.lock" \
    refresh --require-clean --allow-ahead
  printf '{"schema":"test-stack-contract"}\n' \
    > "$atomic/evidence/p101-stack-contract.candidate.json"
  python3 - "$atomic/evidence/repos.candidate.lock" \
    "$atomic/evidence/acceptance.json" \
    "$atomic/evidence/p101-stack-contract.candidate.json" <<'PY'
import hashlib
import json
import pathlib
import sys

lock = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
stack_contract = pathlib.Path(sys.argv[3])
document = {
    "schema": "p101-check-graph-receipt-v2",
    "outcome": "clean",
    "host": {
        "system": "Darwin",
        "release": "test",
        "machine": "test",
        "python": "test",
    },
    "workspace_lock": {
        "valid": True,
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
    },
    "stack_contract": {
        "valid": True,
        "contract_sha256": hashlib.sha256(stack_contract.read_bytes()).hexdigest(),
    },
}
encoded = json.dumps(
    document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
).encode("utf-8")
document["receipt_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
output.write_text(json.dumps(document) + "\n", encoding="utf-8")
PY
  ./workspace/repos-lock.py \
    --lock "$atomic/evidence/repos.candidate.lock" \
    candidate \
    --receipt "$atomic/evidence/workspace-candidate.json" \
    --candidate-stack-contract \
      "$atomic/evidence/p101-stack-contract.candidate.json" \
    --acceptance-receipt "$atomic/evidence/acceptance.json"
) > "$atomic/candidate.log"

IFS='|' read -r atomic_candidate_id atomic_lock_digest \
  atomic_stack_contract_digest atomic_candidate_ref < <(
  cd "$atomic/scripts"
  ./workspace/repos-lock.py candidate-qualification \
    --candidate "$atomic/evidence/workspace-candidate.json"
)
atomic_scripts_candidate="$(git -C "$atomic/scripts" rev-parse HEAD)"
atomic_platform_receipts=()
for platform in linux macos freebsd; do
  platform_receipt="$atomic/evidence/$platform-qualification.json"
  platform_acceptance="$atomic/evidence/$platform-acceptance.json"
  python3 - "$atomic/evidence/acceptance.json" \
    "$platform_acceptance" "$platform" <<'PY'
import hashlib
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
platform_name = sys.argv[3]
system = {"linux": "Linux", "macos": "Darwin", "freebsd": "FreeBSD"}[
    platform_name
]
document = json.loads(source.read_text(encoding="utf-8"))
document["host"]["system"] = system
document.pop("receipt_digest")
encoded = json.dumps(
    document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
).encode("utf-8")
document["receipt_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
output.write_text(json.dumps(document) + "\n", encoding="utf-8")
PY
  atomic_platform_receipts+=(--platform-receipt "$platform_receipt")
  (
    cd "$atomic/scripts"
    ./workspace/repos-lock.py platform-qualification \
      --candidate-id "$atomic_candidate_id" \
      --candidate-lock-sha256 "$atomic_lock_digest" \
      --candidate-stack-contract-sha256 "$atomic_stack_contract_digest" \
      --qualification-ref "$atomic_candidate_ref" \
      --scripts-commit "$atomic_scripts_candidate" \
      --platform "$platform" \
      --github-repository programming101dev/scripts \
      --github-run-id 12345 \
      --github-run-attempt 1 \
      --acceptance-receipt "$platform_acceptance" \
      --receipt "$platform_receipt"
  ) > "$atomic/$platform-qualification.log"
done
(
  cd "$atomic/scripts"
  ./workspace/repos-lock.py aggregate-qualification \
    --candidate-id "$atomic_candidate_id" \
    --candidate-lock-sha256 "$atomic_lock_digest" \
    --candidate-stack-contract-sha256 "$atomic_stack_contract_digest" \
    --qualification-ref "$atomic_candidate_ref" \
    --scripts-commit "$atomic_scripts_candidate" \
    "${atomic_platform_receipts[@]}" \
    --receipt "$atomic/evidence/qualified.json"
) > "$atomic/aggregate-qualification.log"

(
  cd "$atomic/scripts"
  ./distribution/publish-workspace.sh --dry-run \
    --resume "$atomic/evidence/workspace-candidate.json"
) > "$atomic/resume-dry-run.log"
grep -Fq 'resuming immutable workspace candidate' "$atomic/resume-dry-run.log"
grep -Fq 'validated immutable candidate' "$atomic/resume-dry-run.log"

printf 'moved\n' > "$atomic_repository/later.txt"
git -C "$atomic_repository" add later.txt
git -C "$atomic_repository" commit --quiet -m moved
set +e
(
  cd "$atomic/scripts"
  ./distribution/push-repos.sh --yes \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --qualification "$atomic/evidence/qualified.json"
) > "$atomic/moved.log" 2>&1
atomic_moved_status=$?
set -e
[[ "$atomic_moved_status" -eq 2 ]]
grep -Fq 'does not match candidate' "$atomic/moved.log"
[[ "$(git --git-dir="$atomic_remote" rev-parse refs/heads/main)" != "$atomic_candidate" ]]

git -C "$atomic_repository" reset --hard "$atomic_candidate" >/dev/null
set +e
(
  cd "$atomic/scripts"
  ./distribution/push-repos.sh --yes \
    --candidate "$atomic/evidence/workspace-candidate.json"
) > "$atomic/unqualified-default.log" 2>&1
unqualified_default_status=$?
set -e
[[ "$unqualified_default_status" -eq 2 ]]
grep -Fq 'default-branch candidate publication requires --qualification' \
  "$atomic/unqualified-default.log"
(
  cd "$atomic/scripts"
  ./distribution/push-repos.sh --yes \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --candidate-ref "$atomic_candidate_ref"
) > "$atomic/candidate-ref.log" 2>&1
[[ "$(git --git-dir="$atomic_remote" rev-parse "$atomic_candidate_ref")" == "$atomic_candidate" ]]
[[ "$(git --git-dir="$atomic_remote" rev-parse refs/heads/main)" != "$atomic_candidate" ]]
grep -Fq 'temporary candidate qualification refs only' \
  "$atomic/candidate-ref.log"
(
  cd "$atomic/scripts"
  ./distribution/push-repos.sh --yes \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --qualification "$atomic/evidence/qualified.json"
) > "$atomic/exact-push.log" 2>&1
[[ "$(git --git-dir="$atomic_remote" rev-parse refs/heads/main)" == "$atomic_candidate" ]]
grep -Fq 'Immutable candidate preflight evidence: PASS' "$atomic/exact-push.log"

(
  cd "$atomic/scripts"
  ./distribution/push-repos.sh --yes \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --qualification "$atomic/evidence/qualified.json"
) > "$atomic/resume.log" 2>&1
grep -Fq 'Already published at validated commit' "$atomic/resume.log"

cp "$atomic/evidence/repos.candidate.lock" "$atomic/scripts/repos.lock"
mkdir -p "$atomic/scripts/contracts"
printf '{"schema":"test-stack-contract"}\n' \
  > "$atomic/scripts/contracts/p101-stack-contract.json"
git -C "$atomic/scripts" add repos.lock contracts/p101-stack-contract.json
git -C "$atomic/scripts" commit --quiet -m 'complete transaction'
atomic_scripts_completion="$(git -C "$atomic/scripts" rev-parse HEAD)"
git -C "$atomic/scripts" push --quiet
(
  cd "$atomic/scripts"
  ./distribution/publish-workspace.sh --dry-run \
    --resume "$atomic/evidence/workspace-candidate.json"
) > "$atomic/resume-after-scripts-push.log"
grep -Fq 'validated immutable candidate' \
  "$atomic/resume-after-scripts-push.log"
set +e
(
  cd "$atomic/scripts"
  ./workspace/repos-lock.py verify-candidate \
    --candidate "$atomic/evidence/workspace-candidate.json"
) > "$atomic/strict-descendant.log" 2>&1
strict_descendant_status=$?
set -e
[[ "$strict_descendant_status" -eq 2 ]]
(
  cd "$atomic/scripts"
  ./workspace/repos-lock.py verify-candidate \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --allow-scripts-descendant
) > "$atomic/admitted-descendant.log"
printf 'not a completion artifact\n' > "$atomic/scripts/unrelated.txt"
git -C "$atomic/scripts" add unrelated.txt
git -C "$atomic/scripts" commit --quiet -m unrelated
set +e
(
  cd "$atomic/scripts"
  ./workspace/repos-lock.py verify-candidate \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --allow-scripts-descendant
) > "$atomic/unrelated-descendant.log" 2>&1
unrelated_descendant_status=$?
set -e
[[ "$unrelated_descendant_status" -eq 2 ]]
grep -Fq 'outside the transaction completion artifacts' \
  "$atomic/unrelated-descendant.log"
git -C "$atomic/scripts" reset --hard "$atomic_scripts_completion" >/dev/null
(
  cd "$atomic/scripts"
  ./workspace/repos-lock.py complete-candidate \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --receipt "$atomic/evidence/completion.json" \
    --qualification "$atomic/evidence/qualified.json" \
    --stack-contract "$atomic/scripts/contracts/p101-stack-contract.json"
) > "$atomic/completion.log"
grep -Fq 'workspace candidate completed' "$atomic/completion.log"
grep -Fq '"passed": true' "$atomic/evidence/completion.json"

atomic_intruder="$atomic/intruder"
git clone --quiet --branch main "$atomic_remote" "$atomic_intruder"
git -C "$atomic_intruder" config user.name "p101 remote drift test"
git -C "$atomic_intruder" config user.email "remote-drift@invalid.example"
printf 'remote drift\n' > "$atomic_intruder/remote.txt"
git -C "$atomic_intruder" add remote.txt
git -C "$atomic_intruder" commit --quiet -m 'remote drift'
git -C "$atomic_intruder" push --quiet
set +e
(
  cd "$atomic/scripts"
  ./distribution/push-repos.sh --yes \
    --candidate "$atomic/evidence/workspace-candidate.json" \
    --qualification "$atomic/evidence/qualified.json"
) > "$atomic/remote-drift.log" 2>&1
atomic_remote_drift_status=$?
set -e
[[ "$atomic_remote_drift_status" -eq 2 ]]
grep -Fq 'remote moved after candidate validation' "$atomic/remote-drift.log"

printf 'PASS: publication requires one immutable, preflighted, exact-revision candidate.\n'
