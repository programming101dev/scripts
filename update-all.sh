#!/usr/bin/env bash

# Exit the script if any command fails
set -e

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"

# Function to display script usage
usage()
{
    echo "Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>]"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":f:t:k:" opt; do
  case $opt in
    c)
      c_compiler="$OPTARG"
      ;;
    x)
      cxx_compiler="$OPTARG"
      ;;
    f)
      clang_format_name="$OPTARG"
      ;;
    t)
      clang_tidy_name="$OPTARG"
      ;;
    k)
      cppcheck_name="$OPTARG"
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      usage
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      usage
      ;;
  esac
done

./clone-repos.sh

# Define the paths
flags_version="../.flags/version.txt"
current_version="./version.txt"
update=false

# Check if the version.txt exists in ../flags
if [ -f "$flags_version" ]; then
    if ! diff -q "$flags_version" "$current_version" > /dev/null; then
        update=true
    fi
else
    update=true
fi

# Compare the value of update variable
if [ "$update" = true ]; then
  ./check-compilers.sh
  ./generate-flags.sh
  ./link-flags.sh
  cp "$current_version" "$flags_version"
fi

# Read the C compilers from the file into an array
c_compilers=()
while IFS= read -r line; do
    c_compilers+=("$line")
done < "supported_c_compilers.txt"

# Read the C++ compilers from the file into an array
cxx_compilers=()
while IFS= read -r line; do
    cxx_compilers+=("$line")
done < "supported_cxx_compilers.txt"

# Get the length of the longest array
max_length=${#c_compilers[@]}
if [ ${#cxx_compilers[@]} -gt $max_length ]; then
    max_length=${#cxx_compilers[@]}
fi

# Loop through the arrays
for (( i = 0; i < max_length; i++ )); do
    # If the current index is greater than the length of the C compiler array,
    # we use the last element of the C compiler array
    c_compiler_index=$(( i < ${#c_compilers[@]} ? i : ${#c_compilers[@]} - 1 ))

    # Similarly, for the C++ compiler array
    cxx_compiler_index=$(( i < ${#cxx_compilers[@]} ? i : ${#cxx_compilers[@]} - 1 ))

    echo "${c_compilers[$c_compiler_index]} : ${cxx_compilers[$cxx_compiler_index]}"

    ./generate-cmakelists.sh
    ./change-compiler.sh -c "${c_compilers[$c_compiler_index]}" -x "${cxx_compilers[$cxx_compiler_index]}" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
    ./build.sh
done
