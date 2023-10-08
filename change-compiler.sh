#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Parse command-line options
while getopts "c:" opt; do
    case "$opt" in
        c) compiler="$OPTARG";;
        \?) echo "Usage: $0 [-c compiler (e.g. gcc/clang)]"; exit 1;;
    esac
done

# Check if the -c option has been provided
if [ -z "$compiler" ]; then
    echo "Error: -c option is required."
    exit 1
fi

# List of directories
directories=(
    "lib_error"
    "lib_env"
    "lib_c"
    "lib_posix"
    "lib_posix_xsi"
    "lib_posix_optional"
    "lib_unix"
    "lib_fsm"
)

# Loop through the directories
for dir in "${directories[@]}"; do
    # Change to the directory
    pushd "../libraries/$dir" || exit

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
    cmake -S . -B "$build_directory" -DCMAKE_C_COMPILER="$compiler"

    # Return to the original directory
    popd || exit
done

echo "CMake configuration completed with compiler: $compiler"
