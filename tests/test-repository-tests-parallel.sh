#!/usr/bin/env bash
# Verify bounded repository-test workers and deterministic reporting.
set -euo pipefail

scripts_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
work="$(mktemp -d "${TMPDIR:-/tmp}/p101-repository-tests-parallel.XXXXXX")"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/scripts/checks" "$work/repos/alpha/test" "$work/repos/bravo/test" "$work/repos/charlie/test" "$work/probe"
cp "$scripts_root/checks/check-repository-tests.sh" "$work/scripts/checks/"
cp "$scripts_root/checks/write-repository-test-receipt.py" "$work/scripts/checks/"
cat > "$work/scripts/repos.txt" <<'EOF'
|../repos/alpha|c
|../repos/bravo|c
|../repos/charlie|c
EOF

for name in alpha bravo; do
  : > "$work/repos/$name/test/CMakeLists.txt"
  cat > "$work/repos/$name/test.sh" <<EOF
#!/usr/bin/env bash
set -eu
# A repository is allowed to read stdin. The orchestration worklist must not be
# connected to this process or later repositories disappear from the run.
cat >/dev/null
: > "\$P101_TEST_PROBE_DIR/$name"
attempt=0
while [ ! -f "\$P101_TEST_PROBE_DIR/alpha" ] || [ ! -f "\$P101_TEST_PROBE_DIR/bravo" ]; do
  attempt=\$((attempt + 1))
  [ "\$attempt" -lt 100 ] || { echo "workers did not overlap" >&2; exit 1; }
  sleep 0.01
done
printf '%s passed\n' "$name"
EOF
  chmod +x "$work/repos/$name/test.sh"
done

: > "$work/repos/charlie/test/CMakeLists.txt"
cat > "$work/repos/charlie/test.sh" <<'EOF'
#!/usr/bin/env bash
echo "intentional complete diagnostic"
exit 1
EOF
chmod +x "$work/repos/charlie/test.sh"

set +e
(
  cd "$work/scripts"
  P101_TEST_PROBE_DIR="$work/probe" ./checks/check-repository-tests.sh \
    -j 2 --skip-fuzz -o "$work/output"
) > "$work/stdout.txt" 2>&1
status=$?
set -e

[ "$status" -eq 1 ] || { cat "$work/stdout.txt"; echo "expected repository failure" >&2; exit 1; }
grep -q '^Repository test workers: 2$' "$work/stdout.txt" || {
  cat "$work/stdout.txt"
  echo "worker-count receipt missing" >&2
  exit 1
}
grep -q 'intentional complete diagnostic' "$work/stdout.txt" || {
  cat "$work/stdout.txt"
  echo "complete failure diagnostic missing" >&2
  exit 1
}
grep -q '| alpha | PASS | SKIP | [0-9][0-9]* |' "$work/output/summary.md"
grep -q '| bravo | PASS | SKIP | [0-9][0-9]* |' "$work/output/summary.md"
grep -q '| charlie | FAIL | SKIP | [0-9][0-9]* |' "$work/output/summary.md"
python3 - "$work/output/receipt.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    receipt = json.load(stream)
statuses = {
    row["repository"]: row["unit"] for row in receipt["repositories"]
}
assert statuses == {"alpha": "PASS", "bravo": "PASS", "charlie": "FAIL"}
assert receipt["passed"] is False
PY

reported="$(awk -F'|' '/^\\| (alpha|bravo|charlie) / { gsub(/^ +| +$/, "", $2); print $2 }' "$work/output/summary.md")"
[ "$reported" = "$(printf 'alpha\nbravo\ncharlie')" ] || {
  printf 'repository report order changed:\n%s\n' "$reported" >&2
  exit 1
}

compiler="$(command -v clang 2>/dev/null || command -v cc)"
launcher="$work/launcher"
mkdir -p "$launcher/build-main" "$launcher/test"
cp "$scripts_root/../templates/template-c/test.sh" "$launcher/test.sh"
cat > "$launcher/config.cmake" <<'EOF'
set(PROJECT_LANGUAGE C)
EOF
cat > "$launcher/build-main/CMakeCache.txt" <<EOF
CMAKE_C_COMPILER:FILEPATH=$compiler
DETECTED_SANITIZERS:STRING=
EOF
printf 'build-main\n' > "$launcher/.last-build-dir"
cat > "$launcher/test/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.20)
project(test_launcher_identity C)
enable_testing()
add_executable(test_launcher_identity test_launcher_identity.c)
add_test(NAME test_launcher_identity COMMAND test_launcher_identity)
EOF
cat > "$launcher/test/test_launcher_identity.c" <<'EOF'
int main(void)
{
    return 0;
}
EOF
ln -s "$compiler" "$work/compiler-alias"
(
  cd "$launcher"
  P101_TEST_CC="$work/compiler-alias" ./test.sh
) > "$work/compiler-identity.log" 2>&1 || {
  cat "$work/compiler-identity.log"
  echo "test launcher rejected an alias of the configured compiler" >&2
  exit 1
}

echo "repository test parallelism passed"
