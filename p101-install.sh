#!/usr/bin/env bash

# List of directories
directories=(
    "lib_error"
    "lib_env"
    "lib_c"
    "lib_posix"
    "lib_posix_xsi"
    "lib_posix_optional"
    "lib_unix"
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

    # Optional: Add additional commands or actions here

    # Return to the original directory
    popd || exit
done
