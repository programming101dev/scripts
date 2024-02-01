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
    echo "  -c c compiler     Specify the c compiler name (e.g. gcc or clang)"
    echo "  -x c++ compiler   Specify the c++ compiler name (e.g. gcc++ or clang++)"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":c:x:f:t:k:" opt; do
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

./check-env.sh -c "$c_compiler" -x "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
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
  ./link-compilers.sh
  cp "$current_version" "$flags_version"
fi

./generate-cmakelists.sh
./change-compiler.sh -c "$c_compiler" -x "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
./build.sh
