#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Define the target directory where you want to clone repositories
target_directory=".."

# Array of GitHub repositories to clone
repositories=(
    "https://github.com/programming101dev/lib_error.git"
    "https://github.com/programming101dev/lib_env.git"
    "https://github.com/programming101dev/lib_c.git"
    "https://github.com/programming101dev/lib_posix.git"
    "https://github.com/programming101dev/lib_posix_xsi.git"
    "https://github.com/programming101dev/lib_posix_optional.git"
    "https://github.com/programming101dev/lib_unix.git"
    "https://github.com/programming101dev/lib_fsm.git"
    "https://github.com/programming101dev/c-examples.git"
)

# Loop through the array and clone repositories
for repo_url in "${repositories[@]}"; do
    # Extract the repository name from the URL
    repo_name=$(basename "$repo_url" .git)

    # Construct the full path to the target directory
    full_target_directory="$target_directory/$repo_name"

    # Check if the directory already exists
    if [ -d "$full_target_directory" ]; then
        echo "Repository '$repo_name' already exists. Pulling changes."
        # Change to the repository directory and pull
        (cd "$full_target_directory" && git pull)    else
        # Clone the repository
        git clone "$repo_url" "$full_target_directory"

        # Check if the clone was successful
        if [ $? -eq 0 ]; then
            echo "Cloned repository '$repo_name' successfully."
        else
            echo "Failed to clone repository '$repo_name'."
        fi
    fi
done

echo "All repositories have been cloned."
