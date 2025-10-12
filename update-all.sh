#!/bin/sh
# update-all.sh — drive ./update.sh across C and C++ compiler lists
# Portable: POSIX sh; uses awk, printf, getopts

set -eu

# Defaults
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"

c_list_file="supported_c_compilers.txt"
cxx_list_file="supported_cxx_compilers.txt"
driver="./update.sh"

usage() {
    printf '%s\n' \
"Usage: $0 [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-C <c-list>] [-X <cxx-list>] [-u <update.sh>]
  -f clang-format   Name of clang-format, default ${clang_format_name}
  -t clang-tidy     Name of clang-tidy,   default ${clang_tidy_name}
  -k cppcheck       Name of cppcheck,     default ${cppcheck_name}
  -s sanitizers     Comma list,           default ${sanitizers}
  -C file           C compilers list,     default ${c_list_file}
  -X file           C++ compilers list,   default ${cxx_list_file}
  -u file           Path to update.sh,    default ${driver}"
    exit 1
}

# Options
while getopts "f:t:k:s:C:X:u:h" opt; do
  case "$opt" in
    f) clang_format_name=$OPTARG ;;
    t) clang_tidy_name=$OPTARG ;;
    k) cppcheck_name=$OPTARG ;;
    s) sanitizers=$OPTARG ;;
    C) c_list_file=$OPTARG ;;
    X) cxx_list_file=$OPTARG ;;
    u) driver=$OPTARG ;;
    h|*) usage ;;
  esac
done

# Preconditions
[ -f "$c_list_file" ]   || { printf 'Error: C list not found: %s\n' "$c_list_file" >&2; exit 2; }
[ -f "$cxx_list_file" ] || { printf 'Error: C++ list not found: %s\n' "$cxx_list_file" >&2; exit 2; }

# Resolve driver (name or path)
case "$driver" in
  /*|./*|../*)
    [ -x "$driver" ] || { printf 'Error: driver not executable: %s\n' "$driver" >&2; exit 2; }
    ;;
  *)
    driver_path=$(command -v "$driver" 2>/dev/null || true)
    [ -n "$driver_path" ] && driver=$driver_path
    [ -x "$driver" ] || { printf 'Error: driver not found/executable: %s\n' "$driver" >&2; exit 2; }
    ;;
esac

# Count non-empty, non-comment lines (both GNU/BSD awk ok)
count_nonempty() {
  awk 'NF && $1 !~ /^#/' "$1" | wc -l | awk '{print $1}'
}

c_count=$(count_nonempty "$c_list_file")
x_count=$(count_nonempty "$cxx_list_file")
[ "$c_count" -gt 0 ] || { printf 'Error: no C compilers listed in %s\n' "$c_list_file" >&2; exit 3; }
[ "$x_count" -gt 0 ] || { printf 'Error: no C++ compilers listed in %s\n' "$cxx_list_file" >&2; exit 3; }

# Pair lines by repeating last seen entry when one list is shorter.
# Output lines as "C|CXX".
awk '
  FNR==NR { if (NF && $1 !~ /^#/) C[++nC]=$1; next }
           { if (NF && $1 !~ /^#/) X[++nX]=$1 }
  END     {
             if (nC==0 || nX==0) exit 1
             max = (nC>nX)?nC:nX
             for (i=1;i<=max;i++) {
               c = (i<=nC)?C[i]:C[nC]
               x = (i<=nX)?X[i]:X[nX]
               if (c=="" || x=="") continue
               printf "%s|%s\n", c, x
             }
           }
' "$c_list_file" "$cxx_list_file" | while IFS='|' read -r c x; do
    printf 'Updating repositories with: %s : %s\n' "$c" "$x"
    # Quote all args to preserve spaces if any tool names are absolute paths.
    "$driver" \
      -c "$c" \
      -x "$x" \
      -f "$clang_format_name" \
      -t "$clang_tidy_name" \
      -k "$cppcheck_name" \
      -s "$sanitizers"
done
