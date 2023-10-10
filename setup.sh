#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Initialize compiler variable
c_compiler=""
cxx_compiler=""

# Function to display script usage
usage() {
    echo "Usage: $0 -c <C COMPILER> -x <C++ OMPILER>"
    echo "  -c C COMPILER     Specify the c compiler name (e.g., gcc or clang)"
    echo "  -x C++ COMPILER   Specify the c++ compiler name (e.g., gcc++ or clang++)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":c:x:" opt; do
  case $opt in
    c)
      c_compiler="$OPTARG"
      ;;
    x)
      cxx_compiler="$OPTARG"
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
if [ -z "$c_compiler" ]; then
  echo "Error: C Compiler argument (-c) is required."
  usage
fi

# Check if the compiler argument is provided
if [ -z "$cxx_compiler" ]; then
  echo "Error: C++ Compiler argument (-x) is required."
  usage
fi

./clone.sh
./generate-flags.sh
./change-compiler.sh -c "$c_compiler"
./build.sh

pushd ../examples/c-examples
./generate-flags.sh
./generate-makefiles.sh -c $c_compiler
./run-makefiles.sh
popd

pushd ../templates/template-c
./generate-flags.sh
./generate-cmakelists.sh -c $c_compiler
cmake -S . -B build -DCMAKE_C_COMPILER=$c_compiler
cmake --build build --clean-first
popd

pushd ../templates/template-cpp
./generate-flags.sh
./generate-cmakelists.sh -c $cxx_compiler
cmake -S . -B build -DCMAKE_CXX_COMPILER=$cxx_compiler
cmake --build build --clean-first
popd
