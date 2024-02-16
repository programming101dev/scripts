#!/usr/bin/env bash

create_symlinks() {
    local current_dir
    current_dir="$(pwd)"
    local c_compilers_file="${current_dir}/supported_c_compilers.txt"  # Absolute path to the C compilers file
    local cxx_compilers_file="${current_dir}/supported_cxx_compilers.txt"  # Absolute path to the C++ compilers file
    local repos_file="repos.txt"  # File with repository information

    # Read target directories and types from repos.txt file
    while IFS='|' read -r repo_url dir repo_type; do
        # Determine which compiler file to link based on repo_type
        case "$repo_type" in
            c)
                local symlink_target="${c_compilers_file}"
                local symlink_location="${dir}/supported_c_compilers.txt"
                ;;
            cxx)
                local symlink_target="${cxx_compilers_file}"
                local symlink_location="${dir}/supported_cxx_compilers.txt"
                ;;
            *)
                echo "Unsupported repo type for $dir. Skipping."
                continue
                ;;
        esac

        # Check if symlink already exists and create it if not
        if [ -L "$symlink_location" ] || [ -e "$symlink_location" ]; then
            echo "Symlink at $symlink_location already exists. Skipping."
        else
            ln -s "$symlink_target" "$symlink_location"
            echo "Created symlink at $symlink_location"
        fi
    done < "$repos_file"
}

create_symlinks
