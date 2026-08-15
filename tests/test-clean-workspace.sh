#!/usr/bin/env bash
# Verify manifest-driven, all-or-nothing workspace cleanup.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "test-clean-workspace.sh — exercise safe transient workspace cleanup."
    exit 0 ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-clean-workspace.XXXXXX")"
trap 'rm -rf -- "$sandbox"' EXIT
scripts="$sandbox/workspace/scripts"
repository="$sandbox/workspace/libraries/example"
mkdir -p "$scripts/workspace" "$scripts/target" "$repository"
scripts="$(CDPATH='' cd "$scripts" && pwd -P)"
repository="$(CDPATH='' cd "$repository" && pwd -P)"
workspace="$(CDPATH='' cd "$scripts/.." && pwd -P)"
cp ./workspace/clean-workspace.sh "$scripts/workspace/"
chmod +x "$scripts/workspace/clean-workspace.sh"

git -C "$scripts" init --quiet
git -C "$scripts" config user.name "p101 cleanup test"
git -C "$scripts" config user.email "cleanup-test@invalid.example"
printf '%s|../libraries/example|c\n' "https://invalid.example/example.git" > "$scripts/repos.txt"
printf '*\n!.gitignore\n' > "$scripts/target/.gitignore"
printf '.compiler-links/\n' > "$scripts/.gitignore"
git -C "$scripts" add workspace/clean-workspace.sh repos.txt target/.gitignore .gitignore
git -C "$scripts" commit --quiet -m initial

git -C "$repository" init --quiet
git -C "$repository" config user.name "p101 cleanup test"
git -C "$repository" config user.email "cleanup-test@invalid.example"
cat > "$repository/.gitignore" <<'EOF'
/build-*
test/build-*
fuzz/findings/
__pycache__/
.flags/
CMakeCache.txt
*.o
compile_commands.json
.last-build-dir
.last-runtime-build-dir
EOF
printf 'source\n' > "$repository/source.c"
printf 'helper\n' > "$repository/build-helper.sh"
mkdir -p "$repository/fuzz/corpus"
mkdir -p "$repository/__pycache__" "$repository/component/fuzz/artifacts"
printf 'seed\n' > "$repository/fuzz/corpus/seed"
printf 'tracked bytecode\n' > "$repository/__pycache__/tracked.pyc"
git -C "$repository" add .gitignore source.c fuzz/corpus/seed
git -C "$repository" add --force build-helper.sh __pycache__/tracked.pyc
git -C "$repository" commit --quiet -m initial

mkdir -p "$repository/build-clang/_deps/package" "$repository/test/build-clang"
mkdir -p "$repository/fuzz/findings" "$repository/__pycache__" "$repository/.flags"
printf 'cache\n' > "$repository/CMakeCache.txt"
printf 'object\n' > "$repository/generated.o"
printf '[]\n' > "$repository/build-clang/compile_commands.json"
ln -s build-clang/compile_commands.json "$repository/compile_commands.json"
printf 'build-clang\n' > "$repository/.last-build-dir"
printf 'runtime\n' > "$repository/.last-runtime-build-dir"
mkdir -p "$scripts/target/repository-build-cache" "$scripts/.compiler-links"
printf 'cache\n' > "$scripts/target/repository-build-cache/value"
mkdir -p "$workspace/.flags" "$workspace/.p101-audit-debug.fixture"
printf 'coverage\n' > "$workspace/.coverage"
printf 'preserve\n' > "$workspace/notes.txt"

printf 'dirty\n' > "$repository/not-generated.txt"
if (cd "$scripts" && ./workspace/clean-workspace.sh --dry-run \
    > "$sandbox/dirty.out" 2> "$sandbox/dirty.err"); then
  printf 'FAIL: dirty repository was accepted\n' >&2
  exit 1
fi
grep -Fq 'REFUSED dirty repository:' "$sandbox/dirty.err"
[[ -d "$repository/build-clang" ]]
rm "$repository/not-generated.txt"

if ! (cd "$scripts" && ./workspace/clean-workspace.sh --dry-run \
    > "$sandbox/dry-run.out" 2> "$sandbox/dry-run.err"); then
  cat "$sandbox/dry-run.err" >&2
  exit 1
fi
grep -Fq "WOULD REMOVE: $repository/build-clang" "$sandbox/dry-run.out"
grep -Fq "WOULD REMOVE: $scripts/target/repository-build-cache" "$sandbox/dry-run.out"
grep -Fq "WOULD REMOVE: $workspace/.flags" "$sandbox/dry-run.out"
grep -Fq "WOULD REMOVE: $workspace/.p101-audit-debug.fixture" "$sandbox/dry-run.out"
[[ -d "$repository/build-clang" ]]
[[ -d "$scripts/target/repository-build-cache" ]]

if ! (cd "$scripts" && ./workspace/clean-workspace.sh --all \
    > "$sandbox/apply.out" 2> "$sandbox/apply.err"); then
  cat "$sandbox/apply.err" >&2
  exit 1
fi
grep -Fq 'Workspace cleanup complete:' "$sandbox/apply.out"
[[ ! -e "$repository/build-clang" ]]
[[ ! -e "$repository/test/build-clang" ]]
[[ ! -e "$repository/fuzz/findings" ]]
[[ ! -e "$repository/__pycache__" ]]
[[ ! -e "$repository/.flags" ]]
[[ ! -e "$repository/CMakeCache.txt" ]]
[[ ! -e "$repository/generated.o" ]]
[[ ! -e "$repository/compile_commands.json" ]]
[[ ! -e "$repository/.last-build-dir" ]]
[[ ! -e "$repository/.last-runtime-build-dir" ]]
[[ ! -e "$scripts/target/repository-build-cache" ]]
[[ ! -e "$scripts/.compiler-links" ]]
[[ ! -e "$workspace/.flags" ]]
[[ ! -e "$workspace/.coverage" ]]
[[ ! -e "$workspace/.p101-audit-debug.fixture" ]]
[[ -f "$workspace/notes.txt" ]]
[[ -f "$scripts/target/.gitignore" ]]
[[ -f "$repository/source.c" ]]
[[ -f "$repository/build-helper.sh" ]]
[[ -f "$repository/__pycache__/tracked.pyc" ]]
[[ -d "$repository/component/fuzz/artifacts" ]]
[[ -f "$repository/fuzz/corpus/seed" ]]
[[ -z "$(git -C "$scripts" status --porcelain)" ]]
[[ -z "$(git -C "$repository" status --porcelain)" ]]

printf 'PASS: workspace cleanup removes only governed transient paths\n'
