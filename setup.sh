#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Initialize compiler variable
compiler=""

# Function to display script usage
usage() {
    echo "Usage: $0 -c <COMPILER>"
    echo "  -c COMPILER   Specify the compiler name (e.g., gcc or clang)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":c:" opt; do
  case $opt in
    c)
      compiler="$OPTARG"
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      usage
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      usage
      ;;
  esac
done

# Check if the compiler argument is provided
if [ -z "$compiler" ]; then
  echo "Error: Compiler argument (-c) is required."
  usage
fi

# Call the specified scripts with any additional arguments
./clone.sh
./generate-flags.sh
./change-compiler.sh -c "$compiler"
./build.sh
