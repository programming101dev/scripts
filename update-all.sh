#!/bin/sh

# Exit the script if any command fails
set -e

clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"

# Function to display script usage
usage() {
    echo "Usage: $0 [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>]"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-format-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    echo "  -s sanitizers     Specify the sanitizers to use (e.g. address,undefined)"
    exit 1
}

# Parse command-line options (POSIX-compliant)
while getopts "f:t:k:s:" opt; do
  case "$opt" in
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    \?) echo "Invalid option: -$OPTARG" >&2; usage ;;
    :) echo "Option -$OPTARG requires an argument." >&2; usage ;;
  esac
done

# Read compilers from supported files using a loop (POSIX-compliant)
c_compilers=""
while IFS= read -r line || [ -n "$line" ]; do
    c_compilers="$c_compilers $line"
done < "supported_c_compilers.txt"

cxx_compilers=""
while IFS= read -r line || [ -n "$line" ]; do
    cxx_compilers="$cxx_compilers $line"
done < "supported_cxx_compilers.txt"

# Convert space-separated list to an array-like iteration
set -- $c_compilers
c_count=$#

set -- $cxx_compilers
cxx_count=$#

# Determine the max number of iterations
max_length=$c_count
if [ "$cxx_count" -gt "$max_length" ]; then
    max_length=$cxx_count
fi

# Run update.sh once per compiler pair
i=0
while [ "$i" -lt "$max_length" ]; do
    # Get compiler at index i, defaulting to the last element if out of range
    c_compiler=$(echo $c_compilers | cut -d' ' -f$(( i + 1 )) 2>/dev/null || echo $c_compiler)
    cxx_compiler=$(echo $cxx_compilers | cut -d' ' -f$(( i + 1 )) 2>/dev/null || echo $cxx_compiler)

    echo "Updating repositories with: $c_compiler : $cxx_compiler"

    ./update.sh -c "$c_compiler" -x "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"

    i=$((i + 1))
done
