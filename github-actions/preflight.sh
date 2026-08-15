#!/usr/bin/env bash
# Reproduce as much of the GitHub Actions p101 stack job as the current host
# can provide before any managed repository is pushed.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

cc="${P101_GITHUB_CC:-}"
cxx="${P101_GITHUB_CXX:-}"
clang_format="${P101_GITHUB_CLANG_FORMAT:-}"
clang_tidy="${P101_GITHUB_CLANG_TIDY:-}"
cppcheck="${P101_GITHUB_CPPCHECK:-}"
out_dir=""
clean_builds=1
preflight_toolchain=""

usage() {
  cat <<'EOF'
Usage: ./github-actions/preflight.sh [-c <cc>] [-x <cxx>] [-o <dir>] [--keep-builds]

Build the clean local candidate revisions with the same strict clang-oriented
path used by GitHub Actions, then satisfy the complete governed acceptance
graph from exact input-bound evidence. A node runs whenever its admitted source
bytes, tools, platform, environment, or dependency evidence changed. Clean
commits ahead of upstream are admitted only through a temporary lock;
repos.lock is never modified.

Options:
  -c <cc>     C compiler. Default: Homebrew LLVM clang on macOS, otherwise clang.
  -x <cxx>    C++ compiler. Default: the matching clang++.
  -o <dir>    Evidence directory. Default: a new directory under /tmp.
  --keep-builds
               Preserve existing generated build trees. By default they are
               removed before the strict rebuild to control disk usage and
               prevent stale compiler artifacts from influencing the receipt.
               If macOS needs a repaired compiler driver, build trees that
               record that preflight-only driver are also removed on exit.
  -h, --help  Show this help.

Environment overrides:
  P101_GITHUB_CLANG_FORMAT, P101_GITHUB_CLANG_TIDY, P101_GITHUB_CPPCHECK

This is strong local evidence, not proof about another operating system. The
actual macOS, Linux, and FreeBSD jobs remain authoritative for platform headers,
tool versions, kernels, runtimes, and package behavior. Repository installation
into privileged system prefixes is intentionally skipped.
EOF
}

while (($# > 0)); do
  case "$1" in
    -c)
      cc="${2:?Error: -c requires a compiler}"
      shift 2
      ;;
    -x)
      cxx="${2:?Error: -x requires a compiler}"
      shift 2
      ;;
    -o)
      out_dir="${2:?Error: -o requires a directory}"
      shift 2
      ;;
    --keep-builds)
      clean_builds=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

find_executable() {
  local requested="$1"
  local resolved

  if [[ "$requested" == */* ]]; then
    [[ -x "$requested" ]] || {
      printf 'Error: required executable is unavailable: %s\n' "$requested" >&2
      return 1
    }
    (CDPATH='' cd -- "$(dirname -- "$requested")" && printf '%s/%s\n' "$PWD" "$(basename -- "$requested")")
    return
  fi
  resolved="$(command -v "$requested" 2>/dev/null || true)"
  [[ -n "$resolved" ]] || {
    printf 'Error: required executable is unavailable: %s\n' "$requested" >&2
    return 1
  }
  printf '%s\n' "$resolved"
}

llvm_prefix=""
if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
  llvm_prefix="$(brew --prefix llvm 2>/dev/null || true)"
fi

if [[ -z "$cc" ]]; then
  if [[ -n "$llvm_prefix" && -x "$llvm_prefix/bin/clang" ]]; then
    cc="$llvm_prefix/bin/clang"
  else
    cc="clang"
  fi
fi
if [[ -z "$cxx" ]]; then
  if [[ -n "$llvm_prefix" && -x "$llvm_prefix/bin/clang++" ]]; then
    cxx="$llvm_prefix/bin/clang++"
  else
    cxx="clang++"
  fi
fi
if [[ -z "$clang_format" ]]; then
  if [[ -n "$llvm_prefix" && -x "$llvm_prefix/bin/clang-format" ]]; then
    clang_format="$llvm_prefix/bin/clang-format"
  else
    clang_format="clang-format"
  fi
fi
if [[ -z "$clang_tidy" ]]; then
  if [[ -n "$llvm_prefix" && -x "$llvm_prefix/bin/clang-tidy" ]]; then
    clang_tidy="$llvm_prefix/bin/clang-tidy"
  else
    clang_tidy="clang-tidy"
  fi
fi
[[ -n "$cppcheck" ]] || cppcheck="cppcheck"

cc="$(find_executable "$cc")"
cxx="$(find_executable "$cxx")"
clang_format="$(find_executable "$clang_format")"
clang_tidy="$(find_executable "$clang_tidy")"
cppcheck="$(find_executable "$cppcheck")"

if [[ -z "$out_dir" ]]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-github-preflight.XXXXXX")"
else
  mkdir -p "$out_dir"
fi
out_dir="$(CDPATH='' cd -P -- "$out_dir" && pwd -P)"
mkdir -p "$out_dir"

compiler_smoke()
{
  local compiler="$1"
  local language="$2"
  local source="$out_dir/compiler-smoke.${language//+/x}"
  local log="$out_dir/compiler-smoke.${language//+/x}.log"

  printf 'int main(void) { return 0; }\n' > "$source"
  "$compiler" -x "$language" -Werror -fsyntax-only "$source" > "$log" 2>&1
}

repair_macos_clang_driver()
{
  local original_cc="$cc"
  local original_cxx="$cxx"
  local sdk
  local toolchain="$PWD/.compiler-links/github-preflight-macos"

  [[ "$(uname -s)" == "Darwin" ]] || return 1
  "$original_cc" --version 2>/dev/null | head -n 1 | grep -qi clang || return 1
  sdk="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
  [[ -n "$sdk" && -d "$sdk" ]] || return 1

  printf 'int main(void) { return 0; }\n' > "$out_dir/compiler-smoke.cxx"
  "$original_cc" --no-default-config -isysroot "$sdk" \
    -x c -Werror -fsyntax-only "$out_dir/compiler-smoke.c" \
    > "$out_dir/compiler-smoke.repaired-c.log" 2>&1 || return 1
  "$original_cxx" --no-default-config -isysroot "$sdk" \
    -x c++ -Werror -fsyntax-only "$out_dir/compiler-smoke.cxx" \
    > "$out_dir/compiler-smoke.repaired-cxx.log" 2>&1 || return 1

  mkdir -p "$toolchain"
  rm -f "$toolchain/clang" "$toolchain/clang++" \
    "$toolchain/clang-extdef-mapping"
  {
    printf '#!/usr/bin/env bash\nexec '
    printf '%q ' "$original_cc" --no-default-config -isysroot "$sdk"
    printf '"$@"\n'
  } > "$toolchain/clang"
  {
    printf '#!/usr/bin/env bash\nexec '
    printf '%q ' "$original_cxx" --no-default-config -isysroot "$sdk"
    printf '"$@"\n'
  } > "$toolchain/clang++"
  chmod +x "$toolchain/clang" "$toolchain/clang++"
  if [[ -n "$llvm_prefix" && -x "$llvm_prefix/bin/clang-extdef-mapping" ]]; then
    {
      printf '#!/usr/bin/env bash\n'
      printf 'real_extdef=%q\n' "$llvm_prefix/bin/clang-extdef-mapping"
      printf 'sdk=%q\n' "$sdk"
      cat <<'EOF'
arguments=()
inserted=0
for argument in "$@"; do
  arguments+=("$argument")
  if [[ "$argument" == "--" ]]; then
    arguments+=(--no-default-config -isysroot "$sdk")
    inserted=1
  fi
done
if [[ "$inserted" -eq 0 ]]; then
  arguments+=(-- --no-default-config -isysroot "$sdk")
fi
exec "$real_extdef" "${arguments[@]}"
EOF
    } > "$toolchain/clang-extdef-mapping"
    chmod +x "$toolchain/clang-extdef-mapping"
  fi

  cc="$toolchain/clang"
  cxx="$toolchain/clang++"
  preflight_toolchain="$toolchain"
  printf 'Warning: compiler default configuration is unusable; using %s with the valid SDK %s.\n' \
    "$original_cc" "$sdk" >&2
}

cleanup_preflight_toolchain_builds()
{
  local _repository_url
  local repository_path
  local _repository_kind
  local build_tree

  [[ -n "$preflight_toolchain" ]] || return 0
  while IFS='|' read -r _repository_url repository_path _repository_kind; do
    [[ -n "$repository_path" && "$_repository_url" != \#* ]] || continue
    [[ -d "$repository_path/.git" ]] || continue
    while IFS= read -r -d '' build_tree; do
      [[ -f "$build_tree/CMakeCache.txt" ]] || continue
      grep -Fq "$preflight_toolchain/" "$build_tree/CMakeCache.txt" || continue
      if git -C "$repository_path" check-ignore -q -- \
           "${build_tree#"$repository_path"/}"; then
        rm -rf -- "$build_tree"
      else
        printf 'Warning: retained non-ignored preflight build tree: %s\n' \
          "$build_tree" >&2
      fi
    done < <(
      find "$repository_path" -type d \
        \( -name build -o -name 'build-*' \) -prune -print0
    )
  done < repos.txt
}

trap 'cleanup_preflight_toolchain_builds || true' EXIT

if ! compiler_smoke "$cc" c; then
  printf 'Compiler smoke failed for %s:\n' "$cc" >&2
  sed -n '1,40p' "$out_dir/compiler-smoke.c.log" >&2
  if ! repair_macos_clang_driver; then
    printf 'Error: the selected compiler cannot compile a strict trivial C translation unit.\n' >&2
    exit 2
  fi
fi
if ! compiler_smoke "$cxx" c++; then
  printf 'Compiler smoke failed for %s:\n' "$cxx" >&2
  sed -n '1,40p' "$out_dir/compiler-smoke.cxx.log" >&2
  printf 'Error: the selected compiler cannot compile a strict trivial C++ translation unit.\n' >&2
  exit 2
fi

if [[ -n "$(git status --porcelain=v1 --untracked-files=normal)" ]]; then
  printf 'Error: scripts must be committed before the GitHub preflight runs.\n' >&2
  git status --short >&2
  exit 2
fi

clean_managed_build_trees()
{
  local repository_url
  local repository_path
  local _repository_kind
  local removed=0
  local build_tree
  local relative_build_tree

  while IFS='|' read -r repository_url repository_path _repository_kind; do
    [[ -n "$repository_path" && "$repository_url" != \#* ]] || continue
    [[ -d "$repository_path/.git" ]] || continue

    while IFS= read -r -d '' build_tree; do
      relative_build_tree="${build_tree#"$repository_path"/}"
      if ! git -C "$repository_path" check-ignore -q -- "$relative_build_tree"; then
        printf 'Error: refusing to remove non-ignored build tree: %s\n' \
          "$build_tree" >&2
        return 2
      fi
      rm -rf -- "$build_tree"
      removed=$((removed + 1))
    done < <(
      find "$repository_path" -type d \
        \( -name build -o -name 'build-*' \) -prune -print0
    )
  done < repos.txt

  printf 'Removed %d generated managed-repository build tree(s).\n' "$removed"
}

if [[ "$clean_builds" -eq 1 ]]; then
  printf '==> remove generated managed-repository build trees\n'
  clean_managed_build_trees
fi

cmp .github/workflows/p101-stack.yml github-actions/p101-stack.yml
grep -Fq 'git config --global --add safe.directory "$(pwd -P)"' \
  .github/workflows/p101-stack.yml
./tests/test-github-actions-summary.sh

c_list="$out_dir/ci_c_compilers.txt"
cxx_list="$out_dir/ci_cxx_compilers.txt"
printf '%s\n' "$cc" > "$c_list"
printf '%s\n' "$cxx" > "$cxx_list"

printf 'p101 GitHub Actions preflight\n'
printf 'Host:         %s %s %s\n' "$(uname -s)" "$(uname -r)" "$(uname -m)"
printf 'C compiler:   %s\n' "$cc"
printf 'C++ compiler: %s\n' "$cxx"
printf 'Evidence:     %s\n' "$out_dir"
printf '\n==> strict update/build path\n'

set +e
set -o pipefail
P101_QUIET=1 ./update-all.sh \
  --level 3 \
  --format-check \
  --latest \
  --skip-install \
  --skip-acceptance \
  --matrix-output "$out_dir/compiler-matrix" \
  -C "$c_list" \
  -X "$cxx_list" \
  -f "$clang_format" \
  -t "$clang_tidy" \
  -k "$cppcheck" \
  2>&1 | tee "$out_dir/update-all.log"
update_status=${PIPESTATUS[0]}
set -e
if [[ "$update_status" -ne 0 ]]; then
  printf 'FAIL: strict update/build path exited %d.\n' "$update_status" >&2
  exit "$update_status"
fi

if [[ -n "$(git status --porcelain=v1 --untracked-files=normal)" ]]; then
  printf 'Error: the preflight changed tracked scripts files; commit the generated contract changes and rerun.\n' >&2
  git status --short >&2
  exit 2
fi

candidate_lock="$out_dir/repos.candidate.lock"
./workspace/repos-lock.py \
  --lock "$candidate_lock" \
  refresh \
  --require-clean \
  --allow-ahead
candidate_stack_contract="$out_dir/p101-stack-contract.candidate.json"
P101_STACK_REPOS_LOCK="$candidate_lock" \
  ./workspace/stack-contract.sh \
    --contract "$candidate_stack_contract" refresh

printf '\n==> complete governed acceptance graph\n'
host_build="$out_dir/workspace-build"
acceptance_jobs="${CMAKE_BUILD_PARALLEL_LEVEL:-}"
case "$acceptance_jobs" in
  ''|*[!0-9]*|0)
    acceptance_jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
    ;;
esac
case "$acceptance_jobs" in
  ''|*[!0-9]*|0)
    acceptance_jobs="$(sysctl -n hw.ncpu 2>/dev/null || true)"
    ;;
esac
case "$acceptance_jobs" in
  ''|*[!0-9]*|0) acceptance_jobs=2 ;;
esac
cmake -S workspace -B "$host_build" \
  -DCMAKE_C_COMPILER="$cc" \
  -DP101_WORKSPACE_LEVEL=3 \
  -DP101_ACCEPTANCE_CXX_COMPILER="$cxx" \
  -DP101_ACCEPTANCE_OUTPUT_DIR="$out_dir/acceptance" \
  -DP101_ACCEPTANCE_NO_CACHE=OFF
P101_REPOS_LOCK="$candidate_lock" \
P101_STACK_REPOS_LOCK="$candidate_lock" \
P101_STACK_CONTRACT="$candidate_stack_contract" \
  cmake --build "$host_build" --target p101_acceptance --parallel "$acceptance_jobs" \
  2>&1 | tee "$out_dir/acceptance.log"

candidate_receipt="$out_dir/workspace-candidate.json"
candidate_arguments=(
  --lock "$candidate_lock"
  candidate
  --receipt "$candidate_receipt"
  --candidate-stack-contract "$candidate_stack_contract"
  --acceptance-receipt "$out_dir/acceptance/receipt.json"
)
if [[ -f "$out_dir/compiler-matrix/summary.tsv" ]]; then
  candidate_arguments+=(
    --evidence "$out_dir/compiler-matrix/summary.tsv"
  )
fi
./workspace/repos-lock.py "${candidate_arguments[@]}" \
  | tee "$out_dir/workspace-candidate.log"

cat > "$out_dir/receipt.md" <<EOF
# p101 GitHub Actions local preflight

- Result: PASS
- Host: $(uname -s) $(uname -r) $(uname -m)
- C compiler: $cc
- C++ compiler: $cxx
- Candidate lock: repos.candidate.lock
- Candidate stack contract: p101-stack-contract.candidate.json
- Immutable candidate: workspace-candidate.json
- Acceptance summary: acceptance/summary.md

This receipt exercises the strict build and satisfies the complete governed
acceptance graph with exact input-bound evidence over the clean commits bound
by workspace-candidate.json. Reused nodes remain bound to source bytes, tools,
platform, environment, and dependency receipts. It does not
prove behavior on operating systems other than the host that produced it,
privileged system installation, or atomic rollback across independent Git
repositories.
EOF

printf '\nPASS: local candidate cleared the GitHub Actions preflight.\n'
printf 'Receipt: %s\n' "$out_dir/receipt.md"
