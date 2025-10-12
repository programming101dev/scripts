#!/usr/bin/env bash
set -euo pipefail

# Detect the operating system
OS="$(uname)"

# Candidate compiler names (most specific first)
c_compilers=(gcc13 gcc-13 gcc-14 gcc-15 clang clang-21 clang-20 clang-19 clang-18 clang-17 clang-16 clang-15 clang21 clang20 clang19 clang18 clang17 clang16 clang15 clang-devel)
cxx_compilers=(g++13 g++-13 g++-14 g++-15 clang++ clang++-21 clang++-20 clang++-19 clang++-18 clang++-17 clang++-16 clang++-15 clang++21 clang++20 clang++19 clang++18 clang++17 clang++16 clang++15 clang++-devel)

# Append generic GCC only if not macOS
if [[ "$OS" != "Darwin" ]]; then
  c_compilers+=(gcc)
  cxx_compilers+=(g++)
fi

# Compile-test a compiler: writes a tiny main and checks it can produce an exe
_can_compile() {
  local cc="$1" lang="$2"
  local tmpdir
  tmpdir="$(mktemp -d 2>/dev/null || mktemp -d -t ccprobe)"
  # Bake the path into the trap NOW; don't reference locals later under set -u
  trap "rm -rf '$tmpdir' 2>/dev/null || true" RETURN

  local src="$tmpdir/t.$lang" exe="$tmpdir/a.out"
  if [[ "$lang" == "c" ]]; then
    printf 'int main(void){return 0;}\n' >"$src"
  else
    printf 'int main(){return 0;}\n' >"$src"
  fi

  # Prefer silent compile; avoid pulling in nonstandard libs
  if "$cc" -x "$lang" "$src" -o "$exe" >/dev/null 2>&1; then
    [[ -x "$exe" ]]
  else
    return 1
  fi
}

# Remove duplicates while preserving order
dedupe() {
  awk '!seen[$0]++' <(printf "%s\n" "$@")
}

# Filter out Apple stub GCC/G++ on macOS
filter_apple_stub() {
  local cc path
  while read -r cc; do
    [[ -n "$cc" ]] || continue
    if [[ "$OS" == "Darwin" ]]; then
      path="$(command -v "$cc" || true)"
      if [[ "$path" == "/usr/bin/gcc" || "$path" == "/usr/bin/g++" ]]; then
        continue
      fi
    fi
    printf '%s\n' "$cc"
  done
}

# Probe list and write supported_<type>.txt
# Args: <type> <lang> <names...>
probe_list() {
  local type="$1" lang="$2"; shift 2
  local out="supported_${type}.txt"
  : >"$out"

  local name path
  while read -r name; do
    [[ -n "$name" ]] || continue
    if path="$(command -v "$name" 2>/dev/null)"; then
      if _can_compile "$path" "$lang"; then
        printf '%s\n' "$name" >>"$out"
      fi
    fi
  done < <(dedupe "$@" | filter_apple_stub)

  if [[ ! -s "$out" ]]; then
    echo "No working ${type} found. Wrote empty ${out}." >&2
    exit 1
  fi
  echo "Supported ${type} compilers have been written to ${out}"
}

probe_list "c_compilers"   "c"   "${c_compilers[@]}"
probe_list "cxx_compilers" "c++" "${cxx_compilers[@]}"
