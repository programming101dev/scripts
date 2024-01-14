#!/usr/bin/env bash

# Exit the script if any command fails
set -e

c_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"

usage()
{
    echo "Usage: $0 -c <C compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>]"
    echo "  -c c compiler     Specify the c compiler name (e.g. gcc or clang)"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":c:f:t:k:" opt; do
  case $opt in
    c)
      c_compiler="$OPTARG"
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

# List of directories
directories=(
    "libraries/lib_error"
    "libraries/lib_env"
    "libraries/lib_c"
    "libraries/lib_posix"
    "libraries/lib_posix_xsi"
    "libraries/lib_posix_optional"
    "libraries/lib_unix"
    "libraries/lib_fsm"
    "examples/lib_error_examples"
    "examples/lib_env_examples"
    "examples/lib_c_examples"
    "examples/lib_posix_examples"
    "examples/lib_posix_xsi_examples"
    "examples/lib_posix_optional_examples"
    "examples/lib_unix_examples"
    "examples/lib_fsm_examples"
)

# Loop through the directories
for dir in "${directories[@]}"; do
    echo "Working on $dir"
    # Change to the directory
    pushd "../$dir" || exit

    # Construct the full path to the 'build' directory
    build_directory="build"

    # Check if the 'build' directory exists
    if [ -d "$build_directory" ]; then
        # If it exists, delete it
        rm -r "$build_directory"
        echo "Deleted 'build' directory in $dir."
    fi

    # Create the 'build' directory
    mkdir -p "$build_directory"

    # Run cmake configure with the specified compiler
    cmake -S . -B "$build_directory" -DCMAKE_C_COMPILER="$c_compiler" -DCLANG_FORMAT_NAME="$clang_format_name" -DCLANG_TIDY_NAME="$clang_tidy_name" -DCPPCHECK_NAME="$cppcheck_name" -DCMAKE_BUILD_TYPE=Debug

    # Return to the original directory
    popd || exit
    echo ""
done

echo "CMake configuration completed with compiler: $c_compiler"
