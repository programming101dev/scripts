# shellcheck shell=sh
# Build and locate the dependency-free C bootstrap helper.

p101_bootstrap_build()
{
  p101_bootstrap_scripts_root=$1
  p101_bootstrap_compiler=${2:-${CC:-cc}}
  p101_bootstrap_source="$p101_bootstrap_scripts_root/cmake/p101_compile_db.c"
  p101_bootstrap_directory="$p101_bootstrap_scripts_root/target/bootstrap"
  p101_bootstrap_binary="$p101_bootstrap_directory/p101-bootstrap"

  mkdir -p "$p101_bootstrap_directory" || return 1
  if [ ! -x "$p101_bootstrap_binary" ] ||
     [ "$p101_bootstrap_source" -nt "$p101_bootstrap_binary" ]
  then
    p101_bootstrap_temporary="$p101_bootstrap_binary.tmp.$$"
    trap 'rm -f "$p101_bootstrap_temporary"' EXIT HUP INT TERM
    "$p101_bootstrap_compiler" -std=c17 -Wall -Wextra -Werror -pedantic \
      "$p101_bootstrap_source" -o "$p101_bootstrap_temporary" || return 1
    chmod +x "$p101_bootstrap_temporary" || return 1
    mv "$p101_bootstrap_temporary" "$p101_bootstrap_binary" || return 1
    trap - EXIT HUP INT TERM
  fi
  printf '%s\n' "$p101_bootstrap_binary"
}
