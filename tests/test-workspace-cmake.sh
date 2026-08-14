#!/bin/sh
set -eu

scripts_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
workspace_dir=$(CDPATH='' cd -- "$scripts_dir/.." && pwd -P)
work=$(mktemp -d "${TMPDIR:-/tmp}/p101-workspace-cmake-test.XXXXXX")

cleanup()
{
    rm -rf "$work"
}
trap cleanup EXIT HUP INT TERM

cat > "$work/CMakeLists.txt" <<EOF
cmake_minimum_required(VERSION 3.20)
project(p101_missing_dependency C)
set(P101_IN_TREE_DEPENDENCIES_ONLY ON)
set(P101_PUBLIC_LINK_DIRS_EXISTING "")
include("$scripts_dir/cmake/P101Linking.cmake")
_p101_resolve_link_items(resolved p101_deliberately_missing)
EOF

if cmake -S "$work" -B "$work/missing-build" >"$work/missing.log" 2>&1
then
    echo "missing in-tree p101 dependency unexpectedly configured" >&2
    exit 1
fi
grep -Fq "Logical dependency 'p101_deliberately_missing' is not an in-tree target" \
    "$work/missing.log"

cmake -S "$scripts_dir/workspace" -B "$work/default-host-build" \
    -DP101_WORKSPACE_ROOT="$workspace_dir" \
    -DP101_ACCEPTANCE_CXX_COMPILER= >"$work/default-configure.log"
grep -Fq 'P101_WORKSPACE_LEVEL:STRING=1' \
    "$work/default-host-build/CMakeCache.txt"

cmake -S "$scripts_dir/workspace" -B "$work/host-build" \
    -DP101_WORKSPACE_ROOT="$workspace_dir" \
    -DP101_WORKSPACE_LEVEL=2 \
    -DP101_ACCEPTANCE_CXX_COMPILER= >"$work/configure.log"
cmake --build "$work/host-build" --target help >"$work/targets.log"

for target in p101_record p101_json p101_tool_support p101_tool_event \
    p101_subprocess p101_host_runtime p101_host_tools \
    p101_tool_qualification p101_workspace_checks p101_acceptance
do
    grep -Eq "(^|[[:space:]])${target}([[:space:]]|$)" "$work/targets.log"
done

# Building the receipt target must first build the executable targets. This
# catches generators that would otherwise interpret TARGET_FILE paths as
# unrelated source files with no producing rule.
cmake --build "$work/host-build" --target p101_tool_qualification \
    --parallel 2 >"$work/qualification.log"
test -f "$work/host-build/host-tool-qualification.json"
cmake --build "$work/host-build" --target p101_tool_qualification \
    --parallel 2 >"$work/incremental-qualification.log"
if grep -Fq 'Qualifying the in-tree p101 host tools' \
    "$work/incremental-qualification.log"
then
    echo "unchanged host tools unexpectedly reran qualification" >&2
    exit 1
fi

grep -Fq 'P101_IN_TREE_DEPENDENCIES_ONLY:BOOL=ON' \
    "$work/host-build/CMakeCache.txt"
grep -Fq 'P101_USE_PROBED_FLAGS:BOOL=OFF' \
    "$work/host-build/CMakeCache.txt"
grep -Fq 'P101_VERIFY_INCREMENTAL_ACCEPTANCE:BOOL=ON' \
    "$work/host-build/CMakeCache.txt"
grep -Fq 'P101_WORKSPACE_LEVEL:STRING=2' \
    "$work/host-build/CMakeCache.txt"

mkdir -p "$work/fake-scripts"
cat > "$work/fake-scripts/check-after-update-all.sh" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$P101_TEST_ACCEPTANCE_LOG"
EOF
chmod +x "$work/fake-scripts/check-after-update-all.sh"
export P101_TEST_ACCEPTANCE_LOG="$work/acceptance-invocations.txt"
cmake -DSCRIPTS_ROOT="$work/fake-scripts" \
    -DOUTPUT="$work/acceptance" \
    -DC_COMPILER=cc -DCXX_COMPILER=c++ -DVERIFY_INCREMENTAL=OFF \
    -P "$scripts_dir/workspace/RunAcceptance.cmake"
if grep -Fq -- '--resume' "$P101_TEST_ACCEPTANCE_LOG"
then
    echo "fresh acceptance unexpectedly requested resume" >&2
    exit 1
fi
mkdir -p "$work/acceptance"
: > "$work/acceptance/receipt.json"
cmake -DSCRIPTS_ROOT="$work/fake-scripts" \
    -DOUTPUT="$work/acceptance" \
    -DC_COMPILER=cc -DCXX_COMPILER=c++ -DVERIFY_INCREMENTAL=OFF \
    -P "$scripts_dir/workspace/RunAcceptance.cmake"
tail -n 1 "$P101_TEST_ACCEPTANCE_LOG" | grep -Fq -- '--resume'
unset P101_TEST_ACCEPTANCE_LOG

cat > "$work/fake-scripts/check-after-update-all.sh" <<'EOF'
#!/bin/sh
set -eu
output=""
reused=0
elapsed=1000000000
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) output=$2; shift 2 ;;
        --resume) reused=60; elapsed=100000000; shift ;;
        *) shift ;;
    esac
done
mkdir -p "$output"
printf '{"schema":"p101-check-graph-receipt-v2","outcome":"clean","elapsed_ns":%s,"checks":{"completed":73},"cache":{"reused":%s}}\n' \
    "$elapsed" "$reused" > "$output/receipt.json"
printf '# summary\n' > "$output/summary.md"
printf '# profile\n' > "$output/profile.md"
EOF
chmod +x "$work/fake-scripts/check-after-update-all.sh"
cmake -DSCRIPTS_ROOT="$work/fake-scripts" \
    -DOUTPUT="$work/replayed-acceptance" \
    -DC_COMPILER=cc -DCXX_COMPILER=c++ \
    -DVERIFY_INCREMENTAL=ON \
    -DPERFORMANCE_POLICY="$scripts_dir/contracts/p101-performance-budget.json" \
    -P "$scripts_dir/workspace/RunAcceptance.cmake"
grep -Fq '"reused":0' "$work/replayed-acceptance/receipt.json"
grep -Fq '"reused":60' \
    "$work/replayed-acceptance/incremental/receipt.json"
grep -Fq '"passed":true' \
    "$work/replayed-acceptance/performance-receipt.json"

cat > "$work/full-receipt.json" <<'EOF'
{"schema":"p101-check-graph-receipt-v2","outcome":"clean","elapsed_ns":1000000000,"checks":{"completed":73},"cache":{"reused":0}}
EOF
cat > "$work/incremental-receipt.json" <<'EOF'
{"schema":"p101-check-graph-receipt-v2","outcome":"clean","elapsed_ns":100000000,"checks":{"completed":73},"cache":{"reused":60}}
EOF
cmake \
    -DPOLICY="$scripts_dir/contracts/p101-performance-budget.json" \
    -DFULL_RECEIPT="$work/full-receipt.json" \
    -DINCREMENTAL_RECEIPT="$work/incremental-receipt.json" \
    -DOUTPUT="$work/performance-receipt.json" \
    -P "$scripts_dir/workspace/VerifyAcceptancePerformance.cmake"
grep -Fq '"passed":true' "$work/performance-receipt.json"
sed 's/"reused":60/"reused":1/' "$work/incremental-receipt.json" \
    > "$work/slow-incremental-receipt.json"
if cmake \
    -DPOLICY="$scripts_dir/contracts/p101-performance-budget.json" \
    -DFULL_RECEIPT="$work/full-receipt.json" \
    -DINCREMENTAL_RECEIPT="$work/slow-incremental-receipt.json" \
    -DOUTPUT="$work/rejected-performance-receipt.json" \
    -P "$scripts_dir/workspace/VerifyAcceptancePerformance.cmake" \
    > "$work/performance-failure.log" 2>&1
then
    echo "lost incremental reuse unexpectedly passed its performance budget" >&2
    exit 1
fi
grep -Fq 'requires at least 50' "$work/performance-failure.log"

echo "PASS: workspace CMake host-tool graph"
