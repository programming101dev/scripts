#!/usr/bin/env bash

create_symlinks()
{
    local current_dir
    current_dir="$(pwd)"
    local flags_dir="${current_dir}/../.flags"  # Absolute path to the flags directory
    local repos_file="repos.txt"  # File with repository information

    # Read target directories from repos.txt file
    local target_dirs=()
    while IFS='|' read -r repo_url dir repo_type; do
        target_dirs+=("$dir")
    done < "$repos_file"

    # Create symlinks in each target directory
    for dir in "${target_dirs[@]}"; do
        local symlink_target="${flags_dir}"
        local symlink_location="${dir}/.flags"

        # Check if symlink already exists and create it if not
        if [ -L "$symlink_location" ] || [ -e "$symlink_location" ]; then
            echo "Symlink at $symlink_location already exists. Skipping."
        else
            ln -s "$symlink_target" "$symlink_location"
            echo "Created symlink at $symlink_location"
        fi
    done
}

create_symlinks