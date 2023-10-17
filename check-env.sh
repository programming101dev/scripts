#!/usr/bin/env bash

# Function to check if a tool is in the PATH
check_tool() {
    if command -v "$1" >/dev/null 2>&1; then
        echo "$1 found in PATH"
        return 0
    else
        echo "$1 not found in PATH"
        return 1
    fi
}

# List of tools to check
tools=("cmake" "gcc" "clang" "clang-format" "clang-tidy" "cppcheck")

# Initialize a counter for missing tools
missing_count=0

# Loop through the list of tools
for tool in "${tools[@]}"; do
    check_tool "$tool" || ((missing_count++))
done

# Return the count of missing tools
echo "Total missing tools: $missing_count"

# Exit with the count of missing tools as the status code
exit "$missing_count"

