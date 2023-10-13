#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Define an array of repository URLs
repo_urls=(
    "https://github.com/programming101dev/lib_error.git"
    "https://github.com/programming101dev/lib_env.git"
    "https://github.com/programming101dev/lib_c.git"
    "https://github.com/programming101dev/lib_posix.git"
    "https://github.com/programming101dev/lib_posix_xsi.git"
    "https://github.com/programming101dev/lib_posix_optional.git"
    "https://github.com/programming101dev/lib_unix.git"
    "https://github.com/programming101dev/lib_fsm.git"
    "https://github.com/programming101dev/c-examples.git"
    "https://github.com/programming101dev/template-c.git"
    "https://github.com/programming101dev/template-cpp.git"
)

# Define an array of target directories
target_directories=(
    "../libraries/lib_error"
    "../libraries/lib_env"
    "../libraries/lib_c"
    "../libraries/lib_posix"
    "../libraries/lib_posix_xsi"
    "../libraries/lib_posix_optional"
    "../libraries/lib_unix"
    "../libraries/lib_fsm"
    "../examples/c-examples"
    "../templates/template-c"
    "../templates/template-cpp"
)

# Loop through the arrays and clone repositories
for ((i=0; i<${#repo_urls[@]}; i++)); do
    repo_url="${repo_urls[$i]}"
    repo_directory="${target_directories[$i]}"

    echo "$repo_directory"

    # Check if the directory already exists
    if [ -d "$repo_directory" ]; then
        echo "Repository '$repo_directory' already exists. Pulling changes."
        # Change to the repository directory and pull
        pushd "$repo_directory"
        git pull
        popd
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
