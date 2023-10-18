#!/usr/bin/env bash

# Default values
dest_dir=""
template_name=""

# Function to display usage information
usage() {
  echo "Usage: $0 -t <template_name> -d <destination_directory>"
  exit 1
}

# Parse command line options
while getopts ":t:d:" opt; do
  case $opt in
    t)
      template_name="$OPTARG"
      ;;
    d)
      dest_dir="$OPTARG"
      ;;
    \?)
      usage
      ;;
  esac
done

# Check if the destination directory is provided
if [ -z "$dest_dir" ]; then
  echo "Error: Destination directory (-d) not specified."
  usage
fi

# Check if the template name is provided
if [ -z "$template_name" ]; then
  echo "Error: Template name (-t) not specified."
  usage
fi

# Construct the source directory path
source_dir="../templates/template-$template_name"

# Check if the source directory exists
if [ ! -d "$source_dir" ]; then
  echo "Error: Template directory '$source_dir' does not exist."
  exit 1
fi

# Check if the destination directory exists; if not, create it
if [ ! -d "$dest_dir" ]; then
  mkdir -p "$dest_dir"
fi

# List of files and directories to copy
files_to_copy=("flags" ".clang-format" "build.sh" "change-compiler.sh" "files.txt" "generate-cmakelists.sh" "generate-flags.sh" "README.md")

# Copy files and directories to the destination directory
for item in "${files_to_copy[@]}"; do
  source_item="$source_dir/$item"
  dest_item="$dest_dir/$item"

  if [ -e "$source_item" ]; then
    if [ ! -e "$dest_item" ]; then
      if [ -d "$source_item" ]; then
        cp -r "$source_item" "$dest_item"
      else
        cp "$source_item" "$dest_item"
      fi
      echo "Copied $item to $dest_dir"
    else
      echo "$item already exists in $dest_dir. Skipping."
    fi
  else
    echo "$item not found in the template directory. Skipping."
  fi
done

echo "Copy operation complete."
