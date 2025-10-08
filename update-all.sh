#!/bin/sh
# update-all.sh — drive ./update.sh across C and C++ compiler lists
# Portable: POSIX sh, uses only awk, paste, cut, printf, test, getopts

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

# Parse options
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
[ -f "$c_list_file" ] || { printf 'Error: C list not found: %s\n' "$c_list_file" >&2; exit 2; }
[ -f "$cxx_list_file" ] || { printf 'Error: C++ list not found: %s\n' "$cxx_list_file" >&2; exit 2; }
[ -x "$driver" ] || { printf 'Error: driver not executable: %s\n' "$driver" >&2; exit 2; }

# Sanitize lists: drop comments and blank lines, trim whitespace
# Works on BSD/GNU awk
sanitize() {
    awk 'NF && $1 !~ /^#/ { print $1 }' "$1"
}

# Ensure at least one entry remains after sanitization
c_count=$(sanitize "$c_list_file" | wc -l | awk '{print $1}')
x_count=$(sanitize "$cxx_list_file" | wc -l | awk '{print $1}')
[ "$c_count" -gt 0 ] || { printf 'Error: no C compilers listed in %s\n' "$c_list_file" >&2; exit 3; }
[ "$x_count" -gt 0 ] || { printf 'Error: no C++ compilers listed in %s\n' "$cxx_list_file" >&2; exit 3; }

# Pair rows with paste. If one file is shorter, paste emits empty fields.
# Reuse the last nonempty value for whichever side is empty.
# The loop runs as many rows as the longer list.
# Works with BSD/GNU paste (-d) and POSIX read.
last_c=""
last_x=""

# Use a subshell so we can set IFS without affecting the parent
(
    IFS='|'
    # shellcheck disable=SC2002
    paste -d '|' \
        "$(sanitize "$c_list_file" | sed 's/.*/&/')" \
        "$(sanitize "$cxx_list_file" | sed 's/.*/&/')" 2>/dev/null \
    || {
        # If the command substitution above confuses some shells, fall back to temp files
        tmpc=$(mktemp 2>/dev/null || mktemp -t c_list) || exit 1
        tmpx=$(mktemp 2>/dev/null || mktemp -t x_list) || exit 1
        sanitize "$c_list_file" >"$tmpc"
        sanitize "$cxx_list_file" >"$tmpx"
        paste -d '|' "$tmpc" "$tmpx"
        rm -f "$tmpc" "$tmpx"
    }
) | while IFS='|' read -r c x; do
    # If paste produced fewer rows on the shorter side, it yields an empty field.
    if [ -n "${c:-}" ]; then last_c=$c; fi
    if [ -n "${x:-}" ]; then last_x=$x; fi

    # Reuse last seen nonempty
    [ -n "${c:-}" ] || c=$last_c
    [ -n "${x:-}" ] || x=$last_x

    # Final guard
    if [ -z "$c" ] || [ -z "$x" ]; then
        printf 'Warning: skipping empty pair: C="%s" CXX="%s"\n' "$c" "$x" >&2
        continue
    fi

    printf 'Updating repositories with: %s : %s\n' "$c" "$x"
    "$driver" \
      -c "$c" \
      -x "$x" \
      -f "$clang_format_name" \
      -t "$clang_tidy_name" \
      -k "$cppcheck_name" \
      -s "$sanitizers"
done
