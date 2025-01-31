#!/usr/bin/env bash

# Exit the script if any command fails
set -e

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"

# Function to display script usage
usage()
{
    echo "Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>]"
    echo "  -c c compiler     Specify the c compiler name (e.g. gcc or clang)"
    echo "  -x c++ compiler   Specify the c++ compiler name (e.g. gcc++ or clang++)"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    echo "  -s sanitizers     Specify the sanitizers to use name (e.g. address,undefined)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":c:x:f:t:k:s:" opt; do
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

# Check if the compiler argument is provided
if [ -z "$c_compiler" ]; then
  echo "Error: c compiler argument (-c) is required."
  usage
fi

# Check if the compiler argument is provided
if [ -z "$cxx_compiler" ]; then
  echo "Error: c++ compiler argument (-x) is required."
  usage
fi

./pull.sh
./check-env.sh -c "$c_compiler" -x "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"
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

# Ensure the compiler is listed in supported_c_compilers.txt
if ! grep -Fxq "$c_compiler" supported_c_compilers.txt; then
    echo "Error: The specified compiler '$c_compiler' is not in supported_c_compilers.txt."
    echo "Supported compilers:"
    cat supported_c_compilers.txt
    exit 1
fi

# Ensure the C++ compiler is listed in supported_cxx_compilers.txt
if ! grep -Fxq "$cxx_compiler" supported_cxx_compilers.txt; then
    echo "Error: The specified C++ compiler '$cxx_compiler' is not in supported_cxx_compilers.txt."
    echo "Supported C++ compilers:"
    cat supported_cxx_compilers.txt
    exit 1
fi

./link-flags.sh
./link-compilers.sh

./build-repo.sh -c "$c_compiler" -x "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers" -S

