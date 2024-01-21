#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Read directories and types from repos.txt
repos=()
while IFS='|' read -r repo_url dir repo_type; do
    repos+=("$dir|$repo_type")
done < "repos.txt"

# Loop through the directories and repo types
for repo in "${repos[@]}"; do
    IFS='|' read -r dir repo_type <<< "$repo"

    echo "Processing $dir ($repo_type)"
    pushd "$dir" || continue

    ./build.sh

    if [ -f "install.sh" ]; then
      ./install.sh
    fi

    popd || exit
done

echo "Build process completed for all directories."
