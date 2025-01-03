#!/usr/bin/env bash

# Exit the script if any command fails
set -e

clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"

# Function to display script usage
usage()
{
    echo "Usage: $0 [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>]"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    echo "  -s sanitizers     Specify the sanitizers to use name (e.g. address,undefined)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":f:t:k:s:" opt; do
  case $opt in
    f)
      clang_format_name="$OPTARG"
      ;;
    t)
      clang_tidy_name="$OPTARG"
      ;;
    k)
      cppcheck_name="$OPTARG"
      ;;
    s)
      sanitizers="$OPTARG"
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

./pull.sh
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
  cp "$current_version" "$flags_version"
fi

./link-flags.sh
./link-compilers.sh

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
    c_compiler="${c_compilers[$c_compiler_index]}"
    cxx_compiler="${cxx_compilers[$cxx_compiler_index]}"

    ./check-env.sh -c "$c_compiler" -x "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"
    ./generate-cmakelists.sh
    ./change-compiler.sh -c "$c_compiler" -x "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"
    ./build.sh
done
