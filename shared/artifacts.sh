# Shared discovery of non-instrumented build artifacts.
#
# Source from the scripts repository root.  Marker-selected artifacts are
# preferred, followed by deterministic conventional build directories.
# Discovery functions return 1 when no artifact is found.  Callers using
# `set -e` must guard optional lookups with an `if` or `|| true`.

p101_build_directory_admitted()
{
  case "$1" in
    *coverage*|*profile*) return 1 ;;
    *) return 0 ;;
  esac
}

p101_last_build_directory()
{
  p101_repository=$1
  for p101_marker in .last-build-dir .last-runtime-build-dir; do
    [ -f "$p101_repository/$p101_marker" ] || continue
    IFS= read -r p101_build_directory < "$p101_repository/$p101_marker"
    [ -n "$p101_build_directory" ] || continue
    p101_build_directory_admitted "$p101_build_directory" || continue
    printf '%s\n' "$p101_build_directory"
    return 0
  done
  return 1
}

p101_find_built_tool()
{
  p101_repository=$1
  p101_tool_name=$2
  p101_build_directory=$(
    p101_last_build_directory "$p101_repository" 2>/dev/null || true
  )
  if [ -n "$p101_build_directory" ] &&
     [ -x "$p101_repository/$p101_build_directory/$p101_tool_name" ]
  then
    printf '%s\n' \
      "$p101_repository/$p101_build_directory/$p101_tool_name"
    return 0
  fi

  for p101_candidate in \
    "$p101_repository/build/$p101_tool_name" \
    "$p101_repository/build-clang/$p101_tool_name" \
    "$p101_repository/build-gcc/$p101_tool_name" \
    "$p101_repository"/build-*/"$p101_tool_name"
  do
    p101_build_directory=$(basename -- "$(dirname -- "$p101_candidate")")
    p101_build_directory_admitted "$p101_build_directory" || continue
    [ -x "$p101_candidate" ] || continue
    printf '%s\n' "$p101_candidate"
    return 0
  done
  command -v "$p101_tool_name" 2>/dev/null
}

p101_find_compile_database()
{
  p101_repository=$1
  p101_build_directory=$(
    p101_last_build_directory "$p101_repository" 2>/dev/null || true
  )
  if [ -n "$p101_build_directory" ] &&
     [ -f "$p101_repository/$p101_build_directory/compile_commands.json" ]
  then
    printf '%s\n' \
      "$p101_repository/$p101_build_directory/compile_commands.json"
    return 0
  fi

  for p101_candidate in \
    "$p101_repository/build/compile_commands.json" \
    "$p101_repository/build-clang/compile_commands.json" \
    "$p101_repository/build-gcc/compile_commands.json" \
    "$p101_repository"/build-*/compile_commands.json
  do
    p101_build_directory=$(basename -- "$(dirname -- "$p101_candidate")")
    p101_build_directory_admitted "$p101_build_directory" || continue
    [ -f "$p101_candidate" ] || continue
    printf '%s\n' "$p101_candidate"
    return 0
  done
  return 1
}
