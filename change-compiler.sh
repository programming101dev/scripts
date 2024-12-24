#!/usr/bin/env bash

# Exit the script if any command fails
set -e

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitiers=""

usage()
{
    echo "Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>]"
    echo "  -c c compiler     Specify the C compiler name (e.g., gcc or clang)"
    echo "  -x cxx compiler   Specify the C++ compiler name (e.g., g++ or clang++)"
    echo "  -f clang-format   Specify the clang-format name (e.g., clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g., clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g., cppcheck)"
    echo "  -s sanitizers     Specify the sanitiers to use name (e.g. address,undefined)"
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

# Check if the C compiler argument is provided
if [ -z "$c_compiler" ]; then
  echo "Error: C compiler argument (-c) is required."
  usage
fi

# Check if the C++ compiler argument is provided
if [ -z "$cxx_compiler" ]; then
  echo "Error: C++ compiler argument (-x) is required."
  usage
fi

echo "$sanitiers" > $sanitiers.txt

# Read directories and types from repos.txt
while IFS='|' read -r repo_url dir repo_type; do
    echo "Working on $dir ($repo_type)"
    if pushd "$dir" >/dev/null 2>&1; then
        # Check if it's a C or C++ repository and execute the appropriate command
        if [ "$repo_type" = "c" ]; then
            ./change-compiler.sh -c "$c_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitiers"
        elif [ "$repo_type" = "cxx" ]; then
            ./change-compiler.sh -c "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitiers"
        fi
        popd >/dev/null 2>&1
    else
        echo "Directory $dir not found."
    fi
    echo ""
done < repos.txt

echo "Completed operations in all directories with c compiler: $c_compiler and cxx compiler: $cxx_compiler"
