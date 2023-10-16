#!/usr/bin/env bash

# Exit the script if any command fails
set -e

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
    # Change to the directory
    pushd "../$dir" || continue

    # Check if the 'build' directory exists
    if [ ! -d "build" ]; then
        # If it doesn't exist, create it and run cmake configure
        mkdir build
        cmake -S . -B build
    fi

    # Run cmake build with clean first
    cmake --build build --clean-first

    # Run cmake install with sudo
    sudo cmake --install build

    # Retrieve the owner of the 'build' directory
    if [ "$(uname -s)" = "Darwin" ]; then
        build_owner=$(stat -f "%Su" build)
    else
        build_owner=$(ls -ld "build" | awk '{print $3}')  # Linux)
    fi

    # Change the ownership of install_manifest.txt to match 'build' directory owner
    sudo chown "$build_owner" build/install_manifest.txt

    # Check if the command 'ldconfig' exists on the system
    if command -v ldconfig >/dev/null; then
        # 'ldconfig' exists, run it with sudo
        sudo ldconfig
    elif command -v update_dyld_shared_cache >/dev/null; then
        # 'ldconfig' doesn't exist, but 'update_dyld_shared_cache' does, run it with sudo
        sudo update_dyld_shared_cache -force
    fi

    # Return to the original directory
    popd || exit
done
