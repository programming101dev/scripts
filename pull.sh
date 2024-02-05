#!/usr/bin/env bash

# Get the name of the current directory
dir_name=$(basename "$(pwd)")

# Perform git pull and capture the output
output=$(git pull)

# Check if the output contains "Already up to date." message
if [[ $output == *"Already up to date."* ]]; then
  echo "$dir_name is already up to date."
  exit 0
else
  echo "Updates were pulled in $dir_name. Please re-run the script."
  exit 1
fi
