#!/usr/bin/env bash

# Exit the script if any command fails
set -e

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

    # Return to the original directory
    popd || exit
done

# Check if sudoldconf command exists and run it if it does
if command -v ldconf &> /dev/null; then
    sudo ldconf
fi