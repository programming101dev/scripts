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

usage() {
  cat <<'EOF'
Usage: ./github-actions/preflight.sh [-c <cc>] [-x <cxx>] [-o <dir>]

Build the clean local candidate revisions with the same strict clang-oriented
path used by GitHub Actions, then run the complete governed acceptance graph
without its evidence cache. Clean commits ahead of upstream are admitted only
through a temporary lock; repos.lock is never modified.

Options:
  -c <cc>     C compiler. Default: Homebrew LLVM clang on macOS, otherwise clang.
  -x <cxx>    C++ compiler. Default: the matching clang++.
  -o <dir>    Evidence directory. Default: a new directory under /tmp.
  -h, --help  Show this help.

Environment overrides:
  P101_GITHUB_CLANG_FORMAT, P101_GITHUB_CLANG_TIDY, P101_GITHUB_CPPCHECK

This is strong local evidence, not proof about another operating system. The
actual macOS, Linux, and FreeBSD jobs remain authoritative for platform headers,
tool versions, kernels, runtimes, and package behavior.
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
  local toolchain="$out_dir/toolchain"

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
  cat > "$toolchain/clang" <<'EOF'
#!/usr/bin/env bash
exec "${P101_PREFLIGHT_REAL_CC:?}" --no-default-config \
  -isysroot "${P101_PREFLIGHT_MACOS_SDK:?}" "$@"
EOF
  cat > "$toolchain/clang++" <<'EOF'
#!/usr/bin/env bash
exec "${P101_PREFLIGHT_REAL_CXX:?}" --no-default-config \
  -isysroot "${P101_PREFLIGHT_MACOS_SDK:?}" "$@"
EOF
  chmod +x "$toolchain/clang" "$toolchain/clang++"
  if [[ -n "$llvm_prefix" && -x "$llvm_prefix/bin/clang-extdef-mapping" ]]; then
    cat > "$toolchain/clang-extdef-mapping" <<'EOF'
#!/usr/bin/env bash
arguments=()
inserted=0
for argument in "$@"; do
  arguments+=("$argument")
  if [[ "$argument" == "--" ]]; then
    arguments+=(--no-default-config -isysroot "${P101_PREFLIGHT_MACOS_SDK:?}")
    inserted=1
  fi
done
if [[ "$inserted" -eq 0 ]]; then
  arguments+=(-- --no-default-config -isysroot "${P101_PREFLIGHT_MACOS_SDK:?}")
fi
exec "${P101_PREFLIGHT_REAL_EXTDEF:?}" "${arguments[@]}"
EOF
    chmod +x "$toolchain/clang-extdef-mapping"
    export P101_PREFLIGHT_REAL_EXTDEF="$llvm_prefix/bin/clang-extdef-mapping"
  fi

  export P101_PREFLIGHT_REAL_CC="$original_cc"
  export P101_PREFLIGHT_REAL_CXX="$original_cxx"
  export P101_PREFLIGHT_MACOS_SDK="$sdk"
  cc="$toolchain/clang"
  cxx="$toolchain/clang++"
  printf 'Warning: compiler default configuration is unusable; using %s with the valid SDK %s.\n' \
    "$original_cc" "$sdk" >&2
}

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

./checks/check-github-actions-template.sh
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
  --latest \
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

printf '\n==> complete governed acceptance graph\n'
P101_REPOS_LOCK="$candidate_lock" \
  ./check-after-update-all.sh \
  -c "$cc" \
  -x "$cxx" \
  --no-cache \
  -o "$out_dir/acceptance" \
  2>&1 | tee "$out_dir/check-after-update-all.log"

cat > "$out_dir/receipt.md" <<EOF
# p101 GitHub Actions local preflight

- Result: PASS
- Host: $(uname -s) $(uname -r) $(uname -m)
- C compiler: $cc
- C++ compiler: $cxx
- Candidate lock: repos.candidate.lock
- Acceptance summary: acceptance/summary.md

This receipt exercises the strict build and complete governed acceptance graph
over the clean local candidate commits. It does not prove behavior on operating
systems other than the host that produced it.
EOF

printf '\nPASS: local candidate cleared the GitHub Actions preflight.\n'
printf 'Receipt: %s\n' "$out_dir/receipt.md"
