#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Initialize an array to hold the combined repo and directory strings
combined_repos=()

# Read each line from the repos.txt file
while IFS= read -r line; do
    combined_repos+=("$line")
done < repos.txt

# Process each combined repo and directory
for combined in "${combined_repos[@]}"; do
    IFS='|' read -ra ADDR <<< "$combined"
    repo_url="${ADDR[0]}"
    target_directory="${ADDR[1]}"

    # Check if the directory already exists
    if [ -d "$target_directory" ]; then
        echo "Repository '$target_directory' already exists. Pulling changes."
        # Change to the repository directory and pull
        pushd "$target_directory"
        git pull
        popd
    else
        echo "Repository '$target_directory' does not exist. Cloning $repo_url."
        git clone "$repo_url" "$target_directory"

        # Check if the clone was successful
        if [ $? -eq 0 ]; then
            echo "Cloned repository '$target_directory' successfully."
        else
            echo "Failed to clone repository '$target_directory'."
        fi
    fi
done

echo "All repositories have been cloned."
