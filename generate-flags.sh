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
is_flag_supported()
{
    local compiler="$1"
    local tmp_src="$2"
    local flag="$3"
    local supported_flags_ref="$4"
    local extra_flags=""

    # If the compiler is Clang, set the extra flags
    if [[ "$compiler" == clang* ]]; then
        extra_flags="-Wno-invalid-command-line-argument -Wno-unused-command-line-argument"
    fi

    # Attempt to compile and link the temporary source file with the specified flag
    if $compiler -Werror $extra_flags $flag -o /tmp/test_output "$tmp_src" &> /dev/null; then
        if [[ -f /tmp/test_output ]]; then
            echo "Flag '$flag' is supported by $compiler."
            eval "$supported_flags_ref+=('$flag')"
        else
            echo "Flag '$flag' is not supported by the linker. Compilation succeeded but the output is missing."
        fi
    else
        echo "Flag '$flag' is not supported by $compiler."
    fi

    rm -f /tmp/test_output
}

# Function to process compiler flags
process_compiler_flags()
{
    local compiler="$1"
    local tmp_src="$2"
    local category="$3"
    shift 3
    local flags=("$@")
    local supported_flags=()

    for flag in "${flags[@]}"; do
        is_flag_supported "$compiler" "$tmp_src" "$flag" supported_flags
    done

    # Concatenate the flags
    local flags_string
    flags_string=$(IFS=" "; echo "${supported_flags[*]}")

    # Write to file
    printf "%s" "$flags_string" > "../.flags/${compiler}/${category}_flags.txt"
}

process_sanitizer_category()
{
    local compiler="$1"
    local tmp_src="$2"
    local category_name="$3"
    local flags_array_name="$4"
    local supported_flags=()

    # Convert the string into an actual array
    eval "local flags_array=(\"\${${flags_array_name}[@]}\")"

    # Check the first flag
    is_flag_supported "$compiler" "$tmp_src" "${flags_array[0]}" supported_flags

    # Then, check the rest of the flags
    for i in "${!flags_array[@]}"; do
        if [[ $i -ne 0 ]]; then  # Skip the first element
            is_flag_supported "$compiler" "$tmp_src" "${flags_array[$i]}" supported_flags
        fi
    done

    # Concatenate the supported flags
    local flags_string
    flags_string=$(IFS=" "; echo "${supported_flags[*]}")

    # Write to file
    printf "%s" "$flags_string" > "../.flags/${compiler}/${category_name}_flags.txt"
}

# Main processing function
process_flags()
{
    local compiler="$1"
    local tmp_src="$2"
    local darwin_architecture="$3"
    local language="$4"

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Static-Analyzer-Options.html
    local analyzer_flags=(
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
#        "-Wanalyzer-too-complex"
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
        "-fvar-tracking"
        "-fvar-tracking-assignments"
        "-gdescribe-die"
        "-gpubnames"
        "-ggnu-pubnames"
        "-fdebug-types-section"
        "-grecord-gcc-switches"
        "-gas-loc-support"
#        "-gas-locview-support"
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

    local instrumentation_flags=(
      "-faddrsig"
      "-fbasic-block-sections=all"
      "-fharden-compares"
      "-fharden-conditional-branches"
      "-fno-sanitize-ignorelist"
      "-fno-sanitize-recover=all"
      "-fno-stack-limit"
      "-fsanitize-trap=all"
      "-fsanitize=unreachable"
      "-fstack-check"
#      "-fstack-clash-protection"
      "-fstack-protector"
      "-fstack-protector-all"
      "-fstack-protector-strong"
      "-ftls-model=global-dynamic"
      "-funique-internal-linkage-names"

      #"-fsanitize=object-size"
      #"-fsanitize-coverage=trace-pc"
      #"-fsplit-stack"
      #-f[no-]sanitize-coverage=
      #-f[no-]sanitize-address-outline-instrumentation
      #-f[no-]sanitize-stats
      #-femulated-tls
      #-mhwdiv=
      #-m[no-]crc
      #-mgeneral-regs-only
      ###-Winterference-size

      "-fvtable-verify=preinit"
      "-fvtv-debug"
      "-fstrict-vtable-pointers"
      "-fwhole-program-vtables"
      "-fforce-emit-vtables"
      "-fno-assume-sane-operator-new"
      "-fassume-nothrow-exception-dtor"
    )

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Warning-Options.html
    # https://clang.llvm.org/docs/DiagnosticsReference.html#wanalyzer-incompatible-plugin
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
#      "-Wstrict-overflow=5"
      "-Wstring-compare"
      "-Wstringop-overflow=4"
      "-Wstrict-flex-arrays"
      "-Wsuggest-attribute=pure"
      "-Wsuggest-attribute=const"
      "-Wsuggest-attribute=noreturn"
      "-Wmissing-noreturn"
      "-Wsuggest-attribute=malloc"
      "-Wsuggest-attribute=returns_nonnull"
      "-Wno-suggest-attribute=returns_nonnull"
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
      # "-Wlong-long"
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
      "-W#pragma-messages"
      "-W#warnings"
      "-WCFString-literal"
      "-WCL4"
      "-Waddress-of-packed-member"
      "-Waddress-of-temporary"
      "-Walign-mismatch"
      "-Walloca-with-align-alignof"
      "-Walways-inline-coroutine"
      "-Wambiguous-ellipsis"
      "-Wambiguous-macro"
      "-Wanalyzer-incompatible-plugin"
      "-Wanon-enum-enum-conversion"
      "-Wapinotes"
      "-Wargument-outside-range"
      "-Wargument-undefined-behaviour"
      "-Warray-bounds-pointer-arithmetic"
      "-Wasm-operand-widths"
      "-Wassign-enum"
      "-Wassume"
      "-Wat-protocol"
      "-Watimport-in-framework-header"
      "-Watomic-access"
      "-Watomic-alignment"
      "-Watomic-implicit-seq-cst"
      "-Watomic-memory-ordering"
      "-Watomic-properties"
      "-Wattribute-packed-for-bitfield"
      "-Wattribute-warning"
      "-Wattributes"
      "-Wauto-decl-extensions"
      "-Wauto-import"
      "-Wavailability"
      "-Wavr-rtlib-linking-quirks"
      "-Wbackend-plugin"
      "-Wbackslash-newline-escape"
      "-Wbinary-literal"
      "-Wbit-int-extension"
      "-Wbitfield-constant-conversion"
      "-Wbitfield-enum-conversion"
      "-Wbitfield-width"
      "-Wbitwise-conditional-parentheses"
      "-Wbitwise-instead-of-logical"
      "-Wbitwise-op-parentheses"
      "-Wblock-capture-autoreleasing"
      "-Wbool-conversion"
      "-Wbool-operation"
      "-Wbraced-scalar-init"
      "-Wbranch-protection"
      "-Wbridge-cast"
      "-Wbuiltin-assume-aligned-alignment"
      "-Wbuiltin-macro-redefined"
      "-Wbuiltin-memcpy-chk-size"
      "-Wbuiltin-requires-header"
      "-Wcalled-once-parameter"
      "-Wcast-calling-convention"
      "-Wcast-function-type-strict"
      "-Wcast-of-sel-type"
      "-Wcast-qual-unrelated"
      "-Wno-thread-safety-analysis"
      "-Wno-thread-safety-negative"
      "-Wbad-function-cast"
      "-Wdeclaration-after-statement"
      "-Wenum-int-mismatch"
      "-Wimplicit"
      "-Wjump-misses-init"
      "-Wmissing-parameter-type"
      "-Wmissing-prototypes"
      "-Wnested-externs"
      "-Wold-style-declaration"
      "-Wold-style-definition"
      "-Wpointer-sign"
      "-Wstrict-prototypes"
      "-Wc++-compat"
      "-Wabsolute-value"
      "-Wduplicate-decl-specifier"
      "-Wimplicit-function-declaration"
      "-Wimplicit-int"
      "-Wincompatible-pointer-types"
      "-Wint-conversion"
      "-Woverride-init"
      "-Wpointer-to-int-cast"
      "-Wambiguous-member-template"
      "-Wbind-to-temporary-copy"
      "-Wabstract-final-class"
      "-Wabstract-vbase-init"
      "-Wambiguous-delete"
      "-Wambiguous-reversed-operator"
      "-Wanonymous-pack-parens"
      "-Wauto-disable-vptr-sanitizer"
      "-Wauto-storage-class"
      "-Wauto-var-id"
      "-Wbind-to-temporary-copy"
      "-Wbinding-in-condition"
      "-Wcall-to-pure-virtual-from-ctor-dtor"
    )

    # https://gcc.gnu.org/onlinedocs/gcc-13.2.0/gcc/Instrumentation-Options.html
    local address_sanitizer_flags=(
        "-fsanitize=address"
        "-fsanitize-address-use-after-scope"
        "-fsanitize=leak"
    )

    local cfi_sanitizer_flags=(
        "-fsanitize=cfi"
        "-fsanitize-cfi-cross-dso"
        "-fsanitize-cfi-icall-generalize-pointers"
        "-fsanitize-cfi-icall-experimental-normalize-integers"
    )

    local dataflow_sanitizer_flags=(
        "-fsanitize=dataflow"
    )

    local hwaddress_sanitizer_flags=(
        "-fsanitize=hwaddress"
    )

    local memory_sanitizer_flags=(
        "-fsanitize=memory"
    )

    local pointer_overflow_sanitizer_flags=(
          "-fsanitize=pointer-overflow"
      )

    local safe_stack_flags=(
          "--fsanitize=safe-stack"
      )

    local thread_sanitizer_flags=(
          "-fsanitize=thread"
      )

    local undefined_sanitizer_flags=(
        "-fsanitize=undefined"
        "-fsanitize=alignment"
        "-fsanitize=bool"
        "-fsanitize=bounds"
        "-fsanitize=bounds-strict"
        "-fsanitize=builtin"
        "-fsanitize=enum"
        "-fsanitize=float-cast-overflow"
        "-fsanitize=float-divide-by-zero"
        "-fsanitize=function"
        "-fsanitize=integer"
        "-fsanitize=integer-divide-by-zero"
        "-fsanitize=nonnull-attribute"
        "-fsanitize=null"
        "-fsanitize=pointer-compare"
        "-fsanitize=pointer-subtract"
        "-fsanitize=return"
        "-fsanitize=returns-nonnull-attribute"
        "-fsanitize=shift"
        "-fsanitize=shift-exponent"
        "-fsanitize=shift-base"
        "-fsanitize=signed-integer-overflow"
        "-fsanitize-undefined-trap-on-error"
        "-fsanitize=vla-bound"
    )

#    if [[ $language == "c" ]]; then
#        warning_flags+=("-Wbad-function-cast")
#        warning_flags+=("-Wdeclaration-after-statement")
#        warning_flags+=("-Wenum-int-mismatch")
#        warning_flags+=("-Wimplicit")
#        warning_flags+=("-Wjump-misses-init")
#        warning_flags+=("-Wmissing-parameter-type")
#        warning_flags+=("-Wmissing-prototypes")
#        warning_flags+=("-Wnested-externs")
#        warning_flags+=("-Wold-style-declaration")
#        warning_flags+=("-Wold-style-definition")
#        warning_flags+=("-Wpointer-sign")
#        warning_flags+=("-Wstrict-prototypes")
#        warning_flags+=("-Wc++-compat")
#        warning_flags+=("-Wabsolute-value")
#        warning_flags+=("-Wduplicate-decl-specifier")
#        warning_flags+=("-Wimplicit-function-declaration")
#        warning_flags+=("-Wimplicit-int")
#        warning_flags+=("-Wincompatible-pointer-types")
#        warning_flags+=("-Wint-conversion")
#        warning_flags+=("-Woverride-init")
#        warning_flags+=("-Wpointer-to-int-cast")
#    else
#        warning_flags+=("-Wambiguous-member-template")
#        warning_flags+=("-Wbind-to-temporary-copy")
#        warning_flags+=("-Wabstract-final-class")
#        warning_flags+=("-Wabstract-vbase-init")
#        warning_flags+=("-Wambiguous-delete")
#        warning_flags+=("-Wambiguous-reversed-operator")
#        warning_flags+=("-Wanonymous-pack-parens")
#        warning_flags+=("-Wauto-disable-vptr-sanitizer")
#        warning_flags+=("-Wauto-storage-class")
#        warning_flags+=("-Wauto-var-id")
#        warning_flags+=("-Wbind-to-temporary-copy")
#        warning_flags+=("-Wbinding-in-condition")
#        warning_flags+=("-Wcall-to-pure-virtual-from-ctor-dtor")

        # C++ options
        #instrumentation_flags+=("-fsanitize=vptr")

        # VTable
        #instrumentation_flags+=("-fvtable-verify=preinit")
#        instrumentation_flags+=("-fvtv-debug")
#        instrumentation_flags+=("-fstrict-vtable-pointers")
#        instrumentation_flags+=("-fwhole-program-vtables")
#        instrumentation_flags+=("-fforce-emit-vtables")

        # linker
        #instrumentation_flags+=("-f[no]split-lto-unit")

        # memory options
#        instrumentation_flags+=("-fno-assume-sane-operator-new")
#        instrumentation_flags+=("-fassume-nothrow-exception-dtor")
 #   fi

    if [[ "$darwin_architecture" == "arm64" ]]; then
        instrumentation_flags+=("-fcf-protection=null")

        if [[ "$compiler" == "gcc-13" ]]; then
            address_sanitizer_flags=()
        fi
    else
        instrumentation_flags+=("-fcf-protection=full")
        instrumentation_flags+=("-fsanitize=shadow-call-stack")
    fi

    echo "Checking: $compiler"

    # Prepare directory
    local flag_dir="../.flags/${compiler}"
    mkdir -p "$flag_dir"
    rm -f "$flag_dir"/*

    # Process each flag category
    process_compiler_flags "$compiler" "$tmp_src" "analyzer" "${analyzer_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_src" "debug" "${debug_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_src" "instrumentation" "${instrumentation_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_src" "optimization" "${optimization_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_src" "optimize" "${optimize_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_src" "warning" "${warning_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_src" "address_sanitizer" "address_sanitizer_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "cfi_sanitizer" "cfi_sanitizer_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "dataflow_sanitizer" "dataflow_sanitizer_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "hwaddress_sanitizer" "hwaddress_sanitizer_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "memory_sanitizer" "memory_sanitizer_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "pointer_overflow_sanitizer" "pointer_overflow_sanitizer_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "safe_stack_flags" "safe_stack_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "thread_sanitizer_flags" "thread_sanitizer_flags"
    process_sanitizer_category "$compiler" "$tmp_src" "undefined_sanitizer" "undefined_sanitizer_flags"
}

darwin_architecture=$(detect_architecture)

# Create a temporary source file at the start
tmp_c_src=$(mktemp "/tmp/test_src_XXXXXX.c")
echo "int main(void) { return 0; }" > "$tmp_c_src"

tmp_cxx_src=$(mktemp "/tmp/test_src_XXXXXX.cpp")
echo "#include <iostream>

class SimpleClass {
public:
    virtual void greet() {
        std::cout << \"Hello, world!\" << std::endl;
    }
    virtual ~SimpleClass() {} // Including a virtual destructor for good practice
};

int main() {
    SimpleClass obj;
    obj.greet();
    return 0;
}" > "$tmp_cxx_src"

trap "rm -f '$tmp_c_src' '$tmp_cxx_src'" EXIT

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
    process_flags "$compiler" "$tmp_c_src" "$darwin_architecture" "c"
done

# Process C++ compilers
for compiler in "${supported_cxx_compilers[@]}"; do
    process_flags "$compiler" "$tmp_cxx_src" "$darwin_architecture" "cxx"
done
