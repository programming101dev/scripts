#!/usr/bin/env bash
# Verify bounded repository-test workers and deterministic reporting.
set -euo pipefail

scripts_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
work="$(mktemp -d "${TMPDIR:-/tmp}/p101-repository-tests-parallel.XXXXXX")"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/scripts/checks" "$work/scripts/contracts" "$work/scripts/shared" \
  "$work/repos/alpha/test" "$work/repos/bravo/test" \
  "$work/repos/charlie/test" "$work/probe"
cp "$scripts_root/checks/check-repository-tests.sh" "$work/scripts/checks/"
cp "$scripts_root/checks/write-repository-test-receipt.py" "$work/scripts/checks/"
cp "$scripts_root/shared/compilers.sh" "$work/scripts/shared/"
cat > "$work/scripts/contracts/repository-test-costs.tsv" <<'EOF'
# Repository|Expected seconds
*|1
alpha|3
bravo|2
EOF
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

reported="$(awk -F'|' '
  {
    name = $2
    gsub(/^ +| +$/, "", name)
    if(name ~ /^(alpha|bravo|charlie)$/) {
      print name
    }
  }
' "$work/output/summary.md")"
[ "$reported" = "$(printf 'alpha\nbravo\ncharlie')" ] || {
  printf 'repository report order changed:\n%s\n' "$reported" >&2
  exit 1
}

cat > "$work/unit-evidence.tsv" <<'EOF'
library	status
alpha	PASS
bravo	PASS
charlie	FAIL
EOF
cat > "$work/conflicting-unit-evidence.tsv" <<'EOF'
library	status
charlie	PASS
EOF
chmod -x "$work/repos/alpha/test.sh" "$work/repos/bravo/test.sh"
cat > "$work/repos/charlie/test.sh" <<'EOF'
#!/usr/bin/env bash
set -eu
printf 'charlie reran because its conformance evidence failed\n'
EOF
chmod +x "$work/repos/charlie/test.sh"
(
  cd "$work/scripts"
  ./checks/check-repository-tests.sh -j 2 --skip-fuzz \
    --unit-evidence "$work/unit-evidence.tsv" \
    --unit-evidence "$work/conflicting-unit-evidence.tsv" \
    -o "$work/reused-output"
) > "$work/reused-stdout.txt" 2>&1 || {
  cat "$work/reused-stdout.txt"
  echo "checked unit evidence was not reusable" >&2
  exit 1
}
grep -q '| alpha | REUSED | SKIP | [0-9][0-9]* |' \
  "$work/reused-output/summary.md"
grep -q '| bravo | REUSED | SKIP | [0-9][0-9]* |' \
  "$work/reused-output/summary.md"
grep -q '| charlie | PASS | SKIP | [0-9][0-9]* |' \
  "$work/reused-output/summary.md"
grep -q 'charlie reran because its conformance evidence failed' \
  "$work/reused-output/charlie-test.log"

cp "$work/scripts/contracts/repository-test-costs.tsv" "$work/scripts/contracts/repository-test-costs.valid"
printf 'broken|not-a-number\n' >> "$work/scripts/contracts/repository-test-costs.tsv"
set +e
(
  cd "$work/scripts"
  ./checks/check-repository-tests.sh -j 1 --skip-fuzz -o "$work/invalid-cost-output"
) > "$work/invalid-cost.log" 2>&1
invalid_cost_status=$?
set -e
[ "$invalid_cost_status" -eq 2 ] || {
  cat "$work/invalid-cost.log"
  echo "malformed repository cost contract was not rejected" >&2
  exit 1
}
grep -q 'invalid repository cost row' "$work/invalid-cost.log" || {
  cat "$work/invalid-cost.log"
  echo "malformed repository cost diagnostic missing" >&2
  exit 1
}
mv "$work/scripts/contracts/repository-test-costs.valid" "$work/scripts/contracts/repository-test-costs.tsv"

compiler="$(command -v clang 2>/dev/null || command -v cc)"
launcher="$work/launcher"
mkdir -p "$launcher/build-main" "$launcher/test"
cat > "$launcher/test.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

compiler_identity() {
  path="$1"
  case "$path" in
    */*) ;;
    *) path="$(command -v "$path")" ;;
  esac
  while [ -L "$path" ]; do
    target="$(readlink "$path")"
    case "$target" in
      /*) path="$target" ;;
      *) path="$(dirname "$path")/$target" ;;
    esac
  done
  directory="$(CDPATH='' cd -P -- "$(dirname "$path")" && pwd -P)"
  printf '%s/%s' "$directory" "$(basename "$path")"
}

main_bd="$(cat .last-build-dir)"
cached="$(sed -n 's/^CMAKE_C_COMPILER:[^=]*=//p' "$main_bd/CMakeCache.txt" | head -1)"
requested="${P101_TEST_CC:-$cached}"
[ "$(compiler_identity "$cached")" = "$(compiler_identity "$requested")" ] || {
  printf 'configured and requested compilers differ\n' >&2
  exit 1
}
cmake -S test -B test/build -DCMAKE_C_COMPILER="$cached"
cmake --build test/build
ctest --test-dir test/build --output-on-failure
EOF
chmod +x "$launcher/test.sh"
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
