# shellcheck shell=sh
# Shared compiler-name, map, and pairing mechanics.
#
# This file is sourced by both POSIX-sh and Bash entry points.  It deliberately
# performs no setup at load time. Resolution returns 1 when a compiler is not
# found; matrix callers must treat that as a skipped capability, not an
# unguarded command-substitution failure under `set -e`.

p101_derive_cxx_name()
{
  p101_compiler_name=$(basename -- "$1")
  case "$p101_compiler_name" in
    gcc*) printf 'g++%s\n' "${p101_compiler_name#gcc}" ;;
    clang*) printf 'clang++%s\n' "${p101_compiler_name#clang}" ;;
    *) printf '\n' ;;
  esac
}

p101_compiler_map_lookup()
{
  p101_map_file=$1
  p101_requested=$2
  [ -f "$p101_map_file" ] || return 1
  awk -F= -v name="$p101_requested" '
    $1 == name {
      print substr($0, index($0, "=") + 1)
      exit
    }
  ' "$p101_map_file"
}

p101_resolve_compiler()
{
  p101_requested=$1
  p101_map_file=${2:-compiler_paths.txt}
  p101_resolved=

  case "$p101_requested" in
    /*)
      if [ -x "$p101_requested" ]; then
        printf '%s\n' "$p101_requested"
        return 0
      fi
      ;;
    *)
      p101_resolved=$(p101_compiler_map_lookup \
        "$p101_map_file" "$p101_requested" 2>/dev/null || true)
      if [ -n "$p101_resolved" ] && [ -x "$p101_resolved" ]; then
        printf '%s\n' "$p101_resolved"
        return 0
      fi
      p101_resolved=$(command -v "$p101_requested" 2>/dev/null || true)
      if [ -n "$p101_resolved" ] && [ -x "$p101_resolved" ]; then
        case "$p101_resolved" in
          /*) printf '%s\n' "$p101_resolved" ;;
          *)
            p101_resolved_dir=$(dirname -- "$p101_resolved")
            p101_resolved_base=$(basename -- "$p101_resolved")
            p101_resolved_dir=$(CDPATH='' cd -- "$p101_resolved_dir" && pwd -P) || return 1
            printf '%s/%s\n' "$p101_resolved_dir" "$p101_resolved_base"
            ;;
        esac
        return 0
      fi
      ;;
  esac

  printf "Error: could not resolve compiler '%s' (not executable, mapped, or in PATH)\n" \
    "$p101_requested" >&2
  return 1
}

p101_find_compiler_by_basename()
{
  p101_wanted=$1
  p101_list_file=$2
  [ -f "$p101_list_file" ] || return 1
  awk -v want="$p101_wanted" '
    /^[[:space:]]*(#|$)/ { next }
    {
      line=$0
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      n=split(line, parts, "/")
      if(parts[n] == want) {
        print line
        exit
      }
    }
  ' "$p101_list_file"
}
