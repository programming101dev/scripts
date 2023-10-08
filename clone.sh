#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Define an associative array for repositories and their target directories
declare -A repo_directories=(
    ["https://github.com/programming101dev/lib_error.git"]="../libraries/lib_error"
    ["https://github.com/programming101dev/lib_env.git"]="../libraries/lib_env"
    ["https://github.com/programming101dev/lib_c.git"]="../libraries/lib_c"
    ["https://github.com/programming101dev/lib_posix.git"]="../libraries/lib_posix"
    ["https://github.com/programming101dev/lib_posix_xsi.git"]="../libraries/lib_posix_xsi"
    ["https://github.com/programming101dev/lib_posix_optional.git"]="../libraries/lib_posix_optional"
    ["https://github.com/programming101dev/lib_unix.git"]="../libraries/lib_unix"
    ["https://github.com/programming101dev/lib_fsm.git"]="../libraries/lib_fsm"
    ["https://github.com/programming101dev/c-examples.git"]="../examples/c-examples"
    ["https://github.com/programming101dev/template-c.git"]="../templates/template-c"
    ["https://github.com/programming101dev/template-cpp.git"]="../templates/template-cpp"
)

# Loop through the associative array and clone repositories
for repo_url in "${!repo_directories[@]}"; do
    # Extract the target directory for this repository
    repo_directory="${repo_directories[$repo_url]}"

    echo $repo_directory

    # Check if the directory already exists
    if [ -d "$repo_directory" ]; then
        echo "Repository '$repo_directory' already exists. Pulling changes."
        # Change to the repository directory and pull
        (cd "$repo_directory" && git pull)
    else
        # Clone the repository
        git clone "$repo_url" "$repo_directory"

        # Check if the clone was successful
        if [ $? -eq 0 ]; then
            echo "Cloned repository '$repo_directory' successfully."
        else
            echo "Failed to clone repository '$repo_directory'."
        fi
    fi
done

echo "All repositories have been cloned."
