#!/bin/bash

# Exit the script if any command fails
set -e

# Define the source files
c_compiler_file="supported_c_compilers.txt"
cxx_compiler_file="supported_cxx_compilers.txt"

# Define the target directories
c_target_dir="../templates/template-c"
cxx_target_dir="../templates/template-cxx"

# Check if the source files exist
if [ ! -f "$c_compiler_file" ]; then
    echo "Error: File '$c_compiler_file' not found."
    exit 1
fi

if [ ! -f "$cxx_compiler_file" ]; then
    echo "Error: File '$cxx_compiler_file' not found."
    exit 1
fi

# Create the target directories if they don't exist
mkdir -p "$c_target_dir"
mkdir -p "$cxx_target_dir"

# Create symlinks in the target directories
ln -sf "$(realpath "$c_compiler_file")" "$c_target_dir/$c_compiler_file"
ln -sf "$(realpath "$cxx_compiler_file")" "$cxx_target_dir/$cxx_compiler_file"

echo "Symlinks created successfully."
