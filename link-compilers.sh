#!/usr/bin/env bash

create_symlinks() {
    local current_dir
    current_dir="$(pwd)"
    local c_compilers_file="${current_dir}/supported_c_compilers.txt"  # Absolute path to the C compilers file
    local cxx_compilers_file="${current_dir}/supported_cxx_compilers.txt"  # Absolute path to the C++ compilers file
    local repos_file="repos.txt"  # File with repository information

    # Read target directories from repos.txt file
    local target_dirs=()
    while IFS='|' read -r repo_url dir repo_type; do
        target_dirs+=("$dir")
    done < "$repos_file"

    # Create symlinks in each target directory for both files
    for dir in "${target_dirs[@]}"; do
        # For supported_c_compilers.txt
        local symlink_target_c="${c_compilers_file}"
        local symlink_location_c="${dir}/supported_c_compilers.txt"

        # For supported_cxx_compilers.txt
        local symlink_target_cxx="${cxx_compilers_file}"
        local symlink_location_cxx="${dir}/supported_cxx_compilers.txt"

        # Check if C compilers symlink already exists and create it if not
        if [ -L "$symlink_location_c" ] || [ -e "$symlink_location_c" ]; then
            echo "Symlink for C compilers at $symlink_location_c already exists. Skipping."
        else
            ln -s "$symlink_target_c" "$symlink_location_c"
            echo "Created symlink for C compilers at $symlink_location_c"
        fi

        # Check if C++ compilers symlink already exists and create it if not
        if [ -L "$symlink_location_cxx" ] || [ -e "$symlink_location_cxx" ]; then
            echo "Symlink for C++ compilers at $symlink_location_cxx already exists. Skipping."
        else
            ln -s "$symlink_target_cxx" "$symlink_location_cxx"
            echo "Created symlink for C++ compilers at $symlink_location_cxx"
        fi
    done
}

create_symlinks
