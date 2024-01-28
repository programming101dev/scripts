#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Function to detect system architecture
detect_architecture()
{
    local sys_name
    local architecture

    sys_name=$(uname -s)
    architecture=$(uname -m)

    if [[ $sys_name == "Darwin" ]]; then
        case $architecture in
            "arm64") echo "arm64";;
            "x86_64") echo "x86_64";;
            *) echo "unknown";;
        esac
    else
        echo "not_darwin"
    fi
}

# Function to check if a flag is supported by the compiler
is_flag_supported() {
    local compiler="$1"
    local flag="$2"
    local supported_flags_ref="$3"

    if ("$compiler" "$flag" -Werror -Wunknown-warning-option -E - < /dev/null &> /dev/null); then
        echo "Flag '$flag' is supported by $compiler."
        eval "$supported_flags_ref+=('$flag')"
    else
        echo "Flag '$flag' is not supported by $compiler."
    fi
}

# Function to process compiler flags
process_compiler_flags()
{
    local compiler="$1"
    local flag_category="$2"
    local flags=("${!3}")
    local supported_flags=()

    for flag in "${flags[@]}"; do
        is_flag_supported "$compiler" "$flag" supported_flags
    done

    # Concatenate the flags, trimming the trailing space
    local flags_string
    flags_string=$(IFS=" "; echo "${supported_flags[*]}")

    # Write to file without trailing space
    printf "%s" "$flags_string" > "../.flags/${compiler}/${flag_category}_flags.txt"
}

# Main processing function
process_flags()
{
    local compiler="$1"
    local darwin_architecture="$2"
    local language="$3"

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Warning-Options.html
    local warning_flags=(
      "-Wno-poison-system-directories"
      "-Wno-invalid-command-line-argument"
      "-Wno-unused-command-line-argument"
      "-Werror"
      #"-Wfatal-errors"
      "-Wpedantic"
      "-pedantic"
      "-pedantic-errors"
      "-Wall"
      "-Wextra"
      "-Wabi"
      "-Wchar-subscripts"
      "-Wdouble-promotion"
      "-Wformat=2"
      "-Wformat-overflow=2"
      "-Wformat-nonliteral"
      "-Wformat-security"
      "-Wformat-signedness"
      "-Wformat-truncation=2"
      "-Wformat-y2k"
      "-Wnonnull"
      "-Wnonnull-compare"
      "-Wnull-dereference"
      "-Winfinite-recursion"
      "-Winit-self"
      "-Wimplicit-fallthrough=3"
      "-Wignored-qualifiers"
      "-Wmain"
      "-Wmisleading-indentation"
      "-Wmissing-attributes"
      "-Wmissing-braces"
      "-Wmissing-include-dirs"
      "-Wmismatched-dealloc"
      "-Wmultistatement-macros"
      "-Wparentheses"
      "-Wsequence-point"
      "-Wreturn-type"
      "-Wshift-negative-value"
      "-Wshift-overflow=2"
      "-Wswitch"
      "-Wswitch-default"
      "-Wswitch-enum"
      "-Wsync-nand"
      "-Wtrivial-auto-var-init"
      "-Wunused-but-set-parameter"
      "-Wunused-but-set-variable"
      "-Wunused-function"
      "-Wunused-label"
      "-Wunused-local-typedefs"
      "-Wunused-parameter"
      "-Wno-unused-result"
      "-Wunused-variable"
      "-Wunused-const-variable=2"
      "-Wunused-value"
      "-Wunused"
      "-Wuninitialized"
      "-Wmaybe-uninitialized"
      "-Wunknown-pragmas"
      "-Wstrict-aliasing"
      "-Wstrict-overflow=5"
      "-Wstring-compare"
      "-Wstringop-overflow=4"
      "-Wstrict-flex-arrays"
      "-Wsuggest-attribute=pure"
      "-Wsuggest-attribute=const"
      "-Wsuggest-attribute=noreturn"
      "-Wmissing-noreturn"
      "-Wsuggest-attribute=malloc"
      "-Wsuggest-attribute=format"
      "-Wmissing-format-attribute"
      "-Wsuggest-attribute=cold"
      "-Walloc-zero"
      "-Walloca"
      "-Warith-conversion"
      "-Warray-bounds=2"
      "-Warray-compare"
      "-Warray-parameter=2"
      "-Wattribute-alias=2"
      "-Wbidi-chars=any"
      "-Wbool-compare"
      "-Wbool-operation"
      "-Wduplicated-branches"
      "-Wduplicated-cond"
      "-Wframe-address"
      "-Wzero-length-bounds"
      "-Wtautological-compare"
      "-Wtrampolines"
      "-Wfloat-equal"
      "-Wshadow"
      "-Wshadow=global"
      "-Wfree-nonheap-object"
      "-Wunsafe-loop-optimizations"
      "-Wpointer-arith"
      "-Wtsan"
      "-Wtype-limits"
      "-Wcomment"
      "-Wcomments"
      "-Wtrigraphs"
      "-Wundef"
      "-Wunused-macros"
      "-Wcast-qual"
      "-Wcast-align=strict"
      "-Wcast-function-type"
      "-Wwrite-strings"
      "-Wclobbered"
      "-Wconversion"
      "-Wdangling-else"
      "-Wdangling-pointer=2"
      "-Wdate-time"
      "-Wempty-body"
      "-Wenum-compare"
      "-Wenum-conversion"
      "-Wsign-compare"
      "-Wsign-conversion"
      "-Wfloat-conversion"
      "-Wsizeof-array-div"
      "-Wsizeof-pointer-div"
      "-Wsizeof-pointer-memaccess"
      "-Wmemset-elt-size"
      "-Wmemset-transposed-args"
      "-Waddress"
      "-Wlogical-op"
      "-Wlogical-not-parentheses"
      "-Waggregate-return"
      "-Wmissing-declarations"
      "-Wmissing-field-initializers"
      "-Wnormalized=nfc"
      "-Wopenacc-parallelism"
      "-Wopenmp-simd"
      "-Wpacked"
      "-Wpacked-not-aligned"
      #"-Wpadded"
      "-Wno-padded"
      "-Wredundant-decls"
      "-Wrestrict"
      "-Winline"
      "-Wint-in-bool-context"
      "-Winvalid-pch"
      "-Winvalid-utf8"
      "-Wlong-long"
      "-Wvector-operation-performance"
      "-Wvla"
      "-Wvla-parameter"
      "-Wvolatile-register-var"
      "-Wxor-used-as-pow"
      "-Wdisabled-optimization"
      "-Woverlength-strings"
      "-Wextra-tokens"
      "-Weverything"
      "-Wno-unsafe-buffer-usage"
      ###-Wstack-protector
    )

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Static-Analyzer-Options.html
    local analyzer_flags=(
        "--analyzer"
        "-Xanalyzer"
        "-fanalyzer"
        "-Wanalyzer-allocation-size"
        "-Wanalyzer-deref-before-check"
        "-Wanalyzer-double-fclose"
        "-Wanalyzer-double-free"
        "-Wanalyzer-exposure-through-output-file"
        "-Wanalyzer-exposure-through-uninit-copy"
        "-Wanalyzer-fd-access-mode-mismatch"
        "-Wanalyzer-fd-double-close"
        "-Wanalyzer-fd-leak"
        "-Wanalyzer-fd-phase-mismatch"
        "-Wanalyzer-fd-type-mismatch"
        "-Wanalyzer-fd-use-after-close"
        "-Wanalyzer-fd-use-without-check"
        "-Wanalyzer-file-leak"
        "-Wanalyzer-free-of-non-heap"
        "-Wanalyzer-imprecise-fp-arithmetic"
        "-Wanalyzer-infinite-recursion"
        "-Wanalyzer-jump-through-null"
        "-Wanalyzer-malloc-leak"
        "-Wanalyzer-mismatching-deallocation"
        "-Wanalyzer-null-argument"
        "-Wanalyzer-null-dereference"
        "-Wanalyzer-out-of-bounds"
        "-Wanalyzer-possible-null-argument"
        "-Wanalyzer-possible-null-dereference"
        "-Wanalyzer-putenv-of-auto-var"
        "-Wanalyzer-shift-count-negative"
        "-Wanalyzer-shift-count-overflow"
        "-Wanalyzer-stale-setjmp-buffer"
        "-Wanalyzer-symbol-too-complex"
        "-Wanalyzer-too-complex"
        "-fanalyzer-transitivity"
        "-Wanalyzer-unsafe-call-within-signal-handler"
        "-Wanalyzer-use-after-free"
        "-Wanalyzer-use-of-pointer-in-stale-stack-frame"
        "-Wanalyzer-use-of-uninitialized-value"
        "-Wanalyzer-va-arg-type-mismatch"
        "-Wanalyzer-va-list-exhausted"
        "-Wanalyzer-va-list-leak"
        "-Wanalyzer-va-list-use-after-va-end"
        "-fanalyzer-verbosity=3"
        "-Wanalyzer-write-to-const"
        "-Wanalyzer-write-to-string-literal"
    )

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Debugging-Options.html
    local debug_flags=(
        "-g"
        "-g3"
        "-ggdb"
        "-ggdb3"
        "-gbtf"
        "-gctf2"
        "-fvar-tracking"
        "-fvar-tracking-assignments"
        "-gdescribe-die"
        "-gpubnames"
        "-ggnu-pubnames"
        "-fdebug-types-section"
        "-grecord-gcc-switches"
        "-gas-loc-support"
        "-gas-locview-support"
        "-gcolumn-info"
        "-gstatement-frontiers"
        "-gvariable-location-views"
        "-ginternal-reset-location-views"
        "-ginline-points"
        "-feliminate-unused-debug-types"
        #"-fdebug-macro"
        "-glldb"
        "-fno-discard-value-names"
    )
##-femit-class-debug-always

    local optimization_flags=(
      "-O0"
    )

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Optimize-Options.html
    local optimize_flags=(
    "-fno-fast-math"
    "-fstrict-float-cast-overflow"
    "-fmath-errno"
    "-ftrapping-math"
    "-fhonor-infinities"
    "-fhonor-nans"
    "-fnoapprox-func"
    "-fsigned-zeros"
    "-fno-associative-math"
    "-fno-reciprocal-math"
    "-fno-unsafe-math-optimizations"
    "-fnofinite-math-only"
    "-frounding-math"
    "-ffp-model=strict"
    "-ffp-exception-behavior=strict"
    "-ffp-eval-method=source"
    "-fprotect-parens"
    "-fexcess-precision:standard"
    "-fno-cx-limited-range"
    "-fno-cx-fortran-rules"
    )

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Instrumentation-Options.html
    local instrumentation_flags=(
      #"-fsanitize=address"
      "-fsanitize=memory"
      #"-fsanitize=thread"
      #"-fsanitize=hwaddress"
      "-fsanitize=pointer-compare"
      "-fsanitize=pointer-subtract"
      "-fsanitize=shadow-call-stack"
#      "-fsanitize=leak"
      "-fsanitize=undefined"
      "-fsanitize=shift"
      "-fsanitize=shift-exponent"
      "-fsanitize=shift-base"
      "-fsanitize=integer-divide-by-zero"
      "-fsanitize=unreachable"
      "-fsanitize=vla-bound"
      "-fsanitize=null"
      "-fsanitize=return"
      "-fsanitize=signed-integer-overflow"
      "-fsanitize=bounds"
      "-fsanitize=bounds-strict"
      "-fsanitize=alignment"
      #"-fsanitize=object-size"
      "-fsanitize=float-divide-by-zero"
      "-fsanitize=float-cast-overflow"
      "-fsanitize=nonnull-attribute"
      "-fsanitize=returns-nonnull-attribute"
      "-fsanitize=bool"
      "-fsanitize=enum"
      "-fsanitize=pointer-overflow"
      "-fsanitize=builtin"
      "-fsanitize-address-use-after-scope"
      "-fsanitize-undefined-trap-on-error"
      #"-fsanitize-coverage=trace-pc"
      "-fharden-compares"
      "-fharden-conditional-branches"
      "-fstack-protector"
      "-fstack-protector-all"
      "-fstack-protector-strong"
      "-fstack-check"
      "-fstack-clash-protection"
      "-fno-stack-limit"
      #"-fsplit-stack"
      "-fsanitize=dataflow"
      "-fsanitize=cfi"
      "-fsanitize=function"
      #"-fsanitize=safe-stack"
      "-fno-sanitize-recover=all"
      "-fsanitize-trap=all"
      "-fno-sanitize-ignorelist"
      #-f[no-]sanitize-coverage=
      #-f[no-]sanitize-address-outline-instrumentation
      #-f[no-]sanitize-stats
      "-fsanitize-cfi-cross-dso"
      "-fsanitize-cfi-icall-generalize-pointers"
      "-fsanitize-cfi-icall-experimental-normalize-integers"
      "-ftls-model=global-dynamic"
      #-femulated-tls
      #-mhwdiv=
      #-m[no-]crc
      #-mgeneral-regs-only
      "-faddrsig"
      "-funique-internal-linkage-names"
      "-fbasic-block-sections=all"
      ###-Winterference-size
    )

    if [[ $language == "c" ]]; then
        warning_flags+=("-Wbad-function-cast")
        warning_flags+=("-Wdeclaration-after-statement")
        warning_flags+=("-Wenum-int-mismatch")
        warning_flags+=("-Wimplicit")
        warning_flags+=("-Wjump-misses-init")
        warning_flags+=("-Wmissing-parameter-type")
        warning_flags+=("-Wmissing-prototypes")
        warning_flags+=("-Wnested-externs")
        warning_flags+=("-Wold-style-declaration")
        warning_flags+=("-Wold-style-definition")
        warning_flags+=("-Wpointer-sign")
        warning_flags+=("-Wstrict-prototypes")
        warning_flags+=("-Wc++-compat")
        warning_flags+=("-Wabsolute-value")
        warning_flags+=("-Wduplicate-decl-specifier")
        warning_flags+=("-Wimplicit-function-declaration")
        warning_flags+=("-Wimplicit-int")
        warning_flags+=("-Wincompatible-pointer-types")
        warning_flags+=("-Wint-conversion")
        warning_flags+=("-Woverride-init")
        warning_flags+=("-Wpointer-to-int-cast")
    else
      warning_flags+=("-Wduplicate-decl-specifier")
      warning_flags+=("-Wimplicit")
      warning_flags+=("-Wtraditional")
      warning_flags+=("-Wtraditional-conversion")
      warning_flags+=("-Wdeclaration-after-statement")
      warning_flags+=("-Wabsolute-value")
      warning_flags+=("-Wbad-function-cast")
      warning_flags+=("-Wenum-int-mismatch")
      warning_flags+=("-Wjump-misses-init")
      warning_flags+=("-Wstrict-prototypes")
      warning_flags+=("-Wold-style-declaration")
      warning_flags+=("-Wmissing-parameter-type")
      warning_flags+=("-Wmissing-prototypes")
      warning_flags+=("-Woverride-init")
      warning_flags+=("-Wnested-externs")
      warning_flags+=("-Wpointer-sign")
      warning_flags+=("-Wambiguous-member-template")
      warning_flags+=("-Wbind-to-temporary-copy")
      instrumentation_flags+=("-fsanitize=vptr")
      instrumentation_flags+=("-fvtable-verify=preinit")
      instrumentation_flags+=("-fvtv-debug")
      instrumentation_flags+=("-fstrict-vtable-pointers")
      instrumentation_flags+=("-fwhole-program-vtables")
      instrumentation_flags+=("-f[no]split-lto-unit")
      instrumentation_flags+=("-fforce-emit-vtables")
      instrumentation_flags+=("-fno-assume-sane-operator-new")
      instrumentation_flags+=("-fassume-nothrow-exception-dtor")
    fi

    if [[ $compiler == "arm64" ]]; then
        instrumentation_flags+=("-fcf-protection=null")
    else
        instrumentation_flags+=("-fcf-protection=full")
    fi

    echo "Checking: $compiler"

    # Prepare directory
    local flag_dir="../.flags/${compiler}"
    mkdir -p "$flag_dir"
    rm -f "$flag_dir"/*

    # Process each flag category
    process_compiler_flags "$compiler" "warning" warning_flags[@]
    process_compiler_flags "$compiler" "analyzer" analyzer_flags[@]
    process_compiler_flags "$compiler" "debug" debug_flags[@]
    process_compiler_flags "$compiler" "optimization" optimization_flags[@]
    process_compiler_flags "$compiler" "instrumentation" instrumentation_flags[@]
}

darwin_architecture=$(detect_architecture)

# Read the list of supported compilers and process each
supported_c_compilers=()
while IFS= read -r line; do
    supported_c_compilers+=("$line")
done < supported_c_compilers.txt

supported_cxx_compilers=()
while IFS= read -r line; do
    supported_cxx_compilers+=("$line")
done < supported_cxx_compilers.txt

# Process C compilers
for compiler in "${supported_c_compilers[@]}"; do
    process_flags "$compiler" "$darwin_architecture" "c"
done

# Process C++ compilers
for compiler in "${supported_cxx_compilers[@]}"; do
    process_flags "$compiler" "$darwin_architecture" "cxx"
done
