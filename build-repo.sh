#!/usr/bin/env bash

# Exit the script if any command fails
set -e

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"
skip_ldconfig=false  # Track if -s was passed

usage()
{
    echo "Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-s]"
    echo "  -c c compiler     Specify the C compiler name (e.g., gcc or clang)"
    echo "  -x cxx compiler   Specify the C++ compiler name (e.g., g++ or clang++)"
    echo "  -f clang-format   Specify the clang-format name (e.g., clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g., clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g., cppcheck)"
    echo "  -s sanitizers     Specify the sanitizers to use (e.g., address,undefined)"
    echo "  -S                Skip running ldconfig/update_dyld_shared_cache (converted to -S for install.sh)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":c:x:f:t:k:s:S" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    S) skip_ldconfig=true ;;
    \?) echo "Invalid option: -$OPTARG" >&2; usage ;;
    :) echo "Option -$OPTARG requires an argument." >&2; usage ;;
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

echo "$sanitizers" > sanitizers.txt

# Read directories and types from repos.txt
while IFS='|' read -r repo_url dir repo_type; do
    echo "Working on $dir ($repo_type)"

    if pushd "$dir" >/dev/null 2>&1; then
      # Check if it's a C or C++ repository and execute the appropriate command
      if [ "$repo_type" = "c" ]; then
          ./change-compiler.sh -c "$c_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"
      elif [ "$repo_type" = "cxx" ]; then
          ./change-compiler.sh -c "$cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"
      fi

      if [ -f "uninstall.sh" ]; then
        ./uninstall.sh
      fi

      ./build.sh

      if [ -f "install.sh" ]; then
        if [ "$skip_ldconfig" = true ]; then
          ./install.sh -s  # Convert -s to -S for install.sh
        else
          ./install.sh
        fi
      fi

      # **Skip ldconfig if -s was provided**
#      if [ "$skip_ldconfig" = false ]; then
#        if command -v ldconfig >/dev/null; then
#            sudo ldconfig
#        elif command -v update_dyld_shared_cache >/dev/null; then
#            sudo update_dyld_shared_cache -force
#        fi
#      fi

      popd >/dev/null 2>&1
    else
      echo "Directory $dir not found."
    fi
    echo ""
done < repos.txt

echo "Completed operations in all directories with C compiler: $c_compiler and C++ compiler: $cxx_compiler"
