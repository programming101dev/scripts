#!/usr/bin/env bash
# Verify compiler-pair/configuration artifact identities are stable and closed.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-build-lane.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

make_compiler() {
  local path="$1"
  local identity="$2"
  cp "$sandbox/compiler-template" "$path"
  chmod +x "$path"
  printf '%s\n' "$identity" > "$path.identity"
}

cat > "$sandbox/compiler-template" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
identity="$(< "$0.identity")"
case "${1:-}" in
  -dumpmachine) printf '%s-target\n' "$identity" ;;
  --version) printf '%s version 1.0\n' "$identity" ;;
  *) exit 0 ;;
esac
EOF
make_compiler "$sandbox/clang" clang-c
make_compiler "$sandbox/clang++" clang-cxx
make_compiler "$sandbox/gcc" gcc-c
make_compiler "$sandbox/g++" gcc-cxx

flags_root="$sandbox/flags"
mkdir -p "$flags_root/clang" "$flags_root/clang++"
printf '%s\n' '-Wall' > "$flags_root/clang/warning_flags.txt"
printf '%s\n' '-Wall' > "$flags_root/clang++/warning_flags.txt"
printf '%s\n' '/tmp/probe.first/probe.c' > "$flags_root/clang/clang-c.log"
printf '%s\n' '/tmp/probe.first/probe.cc' > "$flags_root/clang++/clang-cxx.log"
printf '%s\n' '/tmp/response.first.txt' > "$flags_root/clang/conflicts.txt"
printf '%s\n' '/tmp/response.first.txt' > "$flags_root/clang++/conflicts.txt"

lane_a="$(P101_BUILD_LANE_FLAGS_ROOT="$flags_root" \
  ./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" \
  -s 'undefined,address' -P 1)"
lane_b="$(P101_BUILD_LANE_FLAGS_ROOT="$flags_root" \
  ./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" \
  -s ' address, undefined,address ' -P 1)"
[[ "$lane_a" == "$lane_b" ]]
[[ "$lane_a" == clang__clang++__quality-level3-maximal-profile-san__* ]]

clean_lane="$(./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++")"
coverage_lane="$(./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" -C 1)"
flags_lane="$(CFLAGS=-fno-common ./workspace/build-lane.sh \
  -c "$sandbox/clang" -x "$sandbox/clang++")"
gcc_lane="$(./workspace/build-lane.sh -c "$sandbox/gcc" -x "$sandbox/g++")"
medium_lane="$(./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" -L 2)"
[[ "$clean_lane" != "$lane_a" ]]
[[ "$clean_lane" != "$coverage_lane" ]]
[[ "$clean_lane" != "$flags_lane" ]]
[[ "$clean_lane" != "$gcc_lane" ]]
[[ "$clean_lane" != "$medium_lane" ]]

runtime_a="$(./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" \
  -s address -C 1 -P 1 -K runtime)"
runtime_b="$(./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" \
  -K runtime)"
runtime_c="$(CFLAGS=-pg LDFLAGS=-pg ./workspace/build-lane.sh \
  -c "$sandbox/clang" -x "$sandbox/clang++" -K runtime)"
[[ "$runtime_a" == "$runtime_b" ]]
[[ "$runtime_a" == "$runtime_c" ]]
[[ "$runtime_a" == clang__clang++__runtime-level1-maximal-clean__* ]]

# Diagnostic logs are not compiler configuration and contain nondeterministic
# probe paths. They must not invalidate an otherwise identical artifact lane.
printf '%s\n' '/tmp/probe.second/probe.c' > "$flags_root/clang/clang-c.log"
printf '%s\n' '/tmp/probe.second/probe.cc' > "$flags_root/clang++/clang-cxx.log"
printf '%s\n' '/tmp/response.second.txt' > "$flags_root/clang/conflicts.txt"
printf '%s\n' '/tmp/response.second.txt' > "$flags_root/clang++/conflicts.txt"
lane_after_log_change="$(P101_BUILD_LANE_FLAGS_ROOT="$flags_root" \
  ./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" \
  -s 'undefined,address' -P 1)"
[[ "$lane_after_log_change" == "$lane_a" ]]

# A consumed flag record is semantic input and must still invalidate the lane.
printf '%s\n' '-Wall -Wextra' > "$flags_root/clang/warning_flags.txt"
lane_after_flag_change="$(P101_BUILD_LANE_FLAGS_ROOT="$flags_root" \
  ./workspace/build-lane.sh -c "$sandbox/clang" -x "$sandbox/clang++" \
  -s 'undefined,address' -P 1)"
[[ "$lane_after_flag_change" != "$lane_a" ]]

printf 'PASS: build lanes bind compiler pairs and complete instrumentation configuration.\n'
