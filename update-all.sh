# Exit the script if any command fails
set -e

./check-env.sh

clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"

# Function to display script usage
usage()
{
    echo "Usage: $0 [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>]"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    exit 1
}

# Parse command-line options using getopt
while getopts ":f:t:k:" opt; do
  case $opt in
    f)
      clang_format_name="$OPTARG"
      ;;
    t)
      clang_tidy_name="$OPTARG"
      ;;
    k)
      cppcheck_name="$OPTARG"
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

./clone.sh

# Load the supported_c_compilers.txt and supported_cxx_compilers.txt files
supported_c_compilers=($(cat supported_c_compilers.txt))
supported_cxx_compilers=($(cat supported_cxx_compilers.txt))

# Initialize separate counters for C and C++ compilers
c_counter=0
cxx_counter=0

# Calculate the maximum number of iterations needed
max_c_iterations=${#supported_c_compilers[@]}
max_cxx_iterations=${#supported_cxx_compilers[@]}
max_iterations=$((max_c_iterations > max_cxx_iterations ? max_c_iterations : max_cxx_iterations))

# Loop through the maximum number of iterations
for ((i=0; i<max_iterations; i++)); do
    # Get the current C compiler and C++ compiler, or use the last one if the list is shorter
    current_c_compiler="${supported_c_compilers[c_counter]:-${supported_c_compilers[-1]}}"
    current_cxx_compiler="${supported_cxx_compilers[cxx_counter]:-${supported_cxx_compilers[-1]}}"

    if [ -n "$current_c_compiler" ]; then
        echo "Running update.sh with -c $current_c_compiler -x $current_cxx_compiler"
        ./change-compiler.sh -c "$current_c_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
        ./build.sh

        pushd ../examples/c-examples
        ./generate-makefiles.sh -c "$current_c_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
        ./run-makefiles.sh
        popd

        pushd ../templates/template-c
        ./generate-cmakelists.sh
        ./change-compiler.sh -c "$current_c_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
        ./build.sh
        popd

        pushd ../templates/template-cpp
        ./generate-cmakelists.sh
        ./change-compiler.sh -c "$current_cxx_compiler" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
        ./build.sh
        popd
    fi

    # Increment the counters
    ((c_counter++))
    ((cxx_counter++))

    # Reset the counters when reaching the end of a list
    if [ "$c_counter" -ge "$max_c_iterations" ]; then
        c_counter=0
    fi

    if [ "$cxx_counter" -ge "$max_cxx_iterations" ]; then
        cxx_counter=0
    fi
done

exit 0
