#!/usr/bin/env bash
set -e

# Probe one flag
is_flag_supported() {
    local compiler="$1"
    local tmp_src="$2"
    local flag="$3"
    local supported_flags_ref="$4"

    local log="../.flags/${compiler}.txt"
    mkdir -p "$(dirname "$log")"

    local probe_args=(-c -o /tmp/test_output.o)

    local extra_flags=()
    if [[ "$compiler" == clang* ]]; then
        extra_flags+=(
            -Werror
            -Werror=unknown-warning-option
            -Qunused-arguments
            -Wno-error=unused-command-line-argument
            -Wno-unused-command-line-argument
        )
    else
        extra_flags+=(-Werror)
    fi

    if "$compiler" "${probe_args[@]}" "${extra_flags[@]}" "$flag" "$tmp_src" >>"$log" 2>&1; then
        echo -e "\033[32m$flag supported by $compiler\033[0m"
        eval "$supported_flags_ref+=('$flag')"
        rm -f /tmp/test_output.o
        return 1
    else
        echo -e "\033[31m$flag not supported by $compiler\033[0m"
        echo "$flag is not supported" >>"$log"
        echo "------------------------------" >>"$log"
        rm -f /tmp/test_output.o
        return 0
    fi
}

# Collect supported flags for a category and write a file
process_compiler_flags() {
    local compiler="$1"
    local tmp_src="$2"
    local category="$3"
    shift 3
    local flags=("$@")
    local supported_flags=()

    rm -f "../.flags/${compiler}.txt"

    for flag in "${flags[@]}"; do
        set +e
        is_flag_supported "$compiler" "$tmp_src" "$flag" supported_flags
        set -e
    done

    local flags_string
    flags_string=$(IFS=" "; echo "${supported_flags[*]}")
    mkdir -p "../.flags/${compiler}"
    printf "%s" "$flags_string" > "../.flags/${compiler}/${category}_flags.txt"
}

# Collect supported flags for a sanitizer category and write a file
process_sanitizer_category() {
    local compiler="$1"
    local tmp_src="$2"
    local category_name="$3"
    shift 3
    local flags=("$@")
    local supported_flags=()

    set +e
    is_flag_supported "$compiler" "$tmp_src" "${flags[0]}" supported_flags
    set -e

    for i in "${!flags[@]}"; do
        if [[ $i -ne 0 ]]; then
            set +e
            is_flag_supported "$compiler" "$tmp_src" "${flags[$i]}" supported_flags
            set -e
        fi
    done

    local flags_string
    flags_string=$(IFS=" "; echo "${supported_flags[*]}")
    mkdir -p "../.flags/${compiler}"
    printf "%s" "$flags_string" > "../.flags/${compiler}/${category_name}_sanitizer_flags.txt"
}

# Temporary sources
tmp_c_src=$(mktemp "/tmp/test_src_XXXXXX.c")
cat > "$tmp_c_src" <<'EOF'
#include <stdlib.h>
#include <stdio.h>
int main(void) {
    int *ptr = NULL;
    int arr[2] = {0, 1};
    int x = 10;
    int y = 0;
    int z;
    *ptr = 5;
    z = x / y;
    z = arr[5];
    int a = 2147483647;
    int b = a + 1;
    int *uaf = (int*)malloc(sizeof(int));
    if (!uaf) return 1;
    free(uaf);
    *uaf = 42;
    printf("%d %d %d %d\n", z, b, *ptr, *uaf);
    return 0;
}
EOF

tmp_cxx_src=$(mktemp "/tmp/test_src_XXXXXX.cpp")
cat > "$tmp_cxx_src" <<'EOF'
#include <iostream>
class SimpleClass {
public:
    virtual void greet() { std::cout << "Hello, world!\n"; }
    virtual ~SimpleClass() {}
};
int main() { SimpleClass obj; obj.greet(); return 0; }
EOF

trap "rm -f '$tmp_c_src' '$tmp_cxx_src' '*.gcno'" EXIT

# Compiler lists
supported_c_compilers=()
while IFS= read -r line; do
    [[ -n "$line" ]] && supported_c_compilers+=("$line")
done < supported_c_compilers.txt

supported_cxx_compilers=()
while IFS= read -r line; do
    [[ -n "$line" ]] && supported_cxx_compilers+=("$line")
done < supported_cxx_compilers.txt

# Flag sets, same as before
analyzer_flags=(
    "-fanalyzer"
    "-Wanalyzer-allocation-size"
    "-Wanalyzer-deref-before-check"
    "-Wanalyzer-double-fclose"
    "-Wanalyzer-double-free"
    "-Wanalyzer-exposure-through-output-file"
    "-Wanalyzer-exposure-through-uninit-copy"
    "-Wanalyzer-fd-access-mode-mismatch"
    "-Wanalyzer-fd-double-close"
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
    "-Wanalyzer-unsafe-call-within-signal-handler"
    "-Wanalyzer-use-after-free"
    "-Wanalyzer-use-of-pointer-in-stale-stack-frame"
    "-Wanalyzer-use-of-uninitialized-value"
    "-Wanalyzer-va-arg-type-mismatch"
    "-Wanalyzer-va-list-exhausted"
    "-Wanalyzer-va-list-leak"
    "-Wanalyzer-va-list-use-after-va-end"
    "-Wanalyzer-write-to-const"
    "-Wanalyzer-write-to-string-literal"
    "-Wanalyzer-exposure-through-uninit-copy"
)

code_generation_flags=(
    "-fdelete-dead-exceptions"
    "-fno-common"
    "-fno-verbose-asm"
    "-frecord-gcc-switches"
    "-fpic"
    "-fPIC"
    "-fno-plt"
    "-fno-fast-math"
    "-fstrict-float-cast-overflow"
    "-fmath-errno"
    "-ftrapping-math"
    "-fno-unsafe-math-optimizations"
)

debug_flags=(
    "-g" "-g1" "-g2"
    "-ggdb" "-ggdb0" "-ggdb1" "-ggdb2"
    "-fno-eliminate-unused-debug-symbols"
    "-femit-class-debug-always "
    "-fno-merge-debug-strings"
    "-fvar-tracking"
    "-fvar-tracking-assignments"
    "-gdescribe-dies"
    "-gpubnames"
    "-ggnu-pubname"
    "-grecord-gcc-switches"
    "-gno-strict-dwarf"
    "-gcolumn-info"
    "-gstatement-frontiers"
    "-gvariable-location-views"
    "-gno-internal-reset-location-views"
    "-ginline-points"
    "-fno-eliminate-unused-debug-types"
)

instrumentation_flags=(
    "-p" "-pg"
    "-fprofile-arcs"
    "--coverage" "-ftest-coverage"
    "-fprofile-abs-path"
    "-fprofile-update=prefer-atomic"
    "-fprofile-reproducible=multithreaded"
    "-fharden-compares"
    "-fharden-conditional-branches"
    "-fstack-protector-all"
    "-fstack-protector-explicit"
    "-fstack-check"
    "-fvtable-verify=std"
    "-finstrument-functions"
    "-finstrument-functions-once"
)

optimization_flags=("-O0")

warning_flags=( # unchanged, very long list follows
    "-Werror"
    "-Wpedantic"
    "-pedantic-errors"
    "-Wall"
    "-Wextra"
    "-Wabi"
    "-Wchar-subscripts"
    "-Wdouble-promotion"
    "-Wduplicate-decl-specifier"
    "-Wformat"
    "-Wformat=1"
    "-Wformat=2"
    "-Wformat-overflow"
    "-Wformat-overflow=1"
    "-Wformat-overflow=2"
    "-Wformat-nonliteral"
    "-Wformat-security"
    "-Wformat-signedness"
    "-Wformat-truncation"
    "-Wformat-truncation=1"
    "-Wformat-truncation=2"
    "-Wformat-y2k"
    "-Wnonnull"
    "-Wnonnull-compare"
    "-Wnull-dereference"
    "-Winfinite-recursion"
    "-Winit-self"
    "-Wimplicit"
    "-Wimplicit-fallthrough"
    "-Wimplicit-fallthrough=1"
    "-Wimplicit-fallthrough=2"
    "-Wimplicit-fallthrough=3"
    "-Wimplicit-fallthrough=4"
    "-Wimplicit-fallthrough=5"
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
    "-Wno-shift-overflow"
    "-Wshift-overflow=1"
    "-Wshift-overflow=2"
    "-Wswitch"
    "-Wswitch-default"
    "-Wswitch-enum"
    "-Wno-switch-bool"
    "-Wsync-nand"
    "-Wtrivial-auto-var-init"
    "-Wunused-but-set-parameter"
    "-Wunused-but-set-variable"
    "-Wunused-function"
    "-Wunused-label"
    "-Wunused-local-typedefs"
    "-Wunused-parameter"
    "-Wunused-variable"
    "-Wunused-const-variable"
    "-_UNUSED-const-variable=1"
    "-Wunused-const-variable=2"
    "-Wunused-value"
    "-Wunused"
    "-Wuninitialized"
    "-Wmaybe-uninitialized"
    "-Wunknown-pragmas"
    "-Wstrict-aliasing"
    "-Wstrict-aliasing=1"
    "-Wstrict-aliasing=2"
    "-Wstrict-aliasing=3"
    "-Wstring-compare"
    "-Wstringop-overflow"
    "-Wstringop-overflow=1"
    "-Wstringop-overflow=2"
    "-Wstringop-overflow=3"
    "-Wstringop-overflow=4"
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
    "-Walloca-larger-than"
    "-Warith-conversion"
    "-Warray-bounds"
    "-Warray-bounds=1"
    "-Warray-bounds=2"
    "-Warray-compare"
    "-Warray-parameter"
    "-Warray-parameter=1"
    "-Warray-parameter=2"
    "-Wattribute-alias=1"
    "-Wattribute-alias=2"
    "-Wno-attribute-alias"
    "-Wbidi-chars=unpaired,any,ucn"
    "-Wbool-compare"
    "-Wbool-operation"
    "-Wduplicated-branches"
    "-Wduplicated-cond"
    "-Wframe-address"
    "-Wzero-length-bounds"
    "-Wtautological-compare"
    "-Wtrampolines"
    "-Wfloat-equal"
    "-Wdeclaration-after-statement"
    "-Wshadow"
    "-Wshadow=global"
    "-Wshadow=local"
    "-Wshadow=compatible-local"
    "-Wfree-nonheap-object"
    "-Wunsafe-loop-optimizations"
    "-Wpointer-arith"
    "-Wtsan"
    "-Wtype-limits"
    "-Wabsolute-value"
    "-Wcomment"
    "-Wtrigraphs"
    "-Wundef"
    "-Wexpansion-to-defined"
    "-Wunused-macros"
    "-Wbad-function-cast"
    "-Wc++-compat"
    "-Wc++11-compat"
    "-Wc++14-compat"
    "-Wc++17-compat"
    "-Wc++20-compat"
    "-Wcast-qual"
    "-Wcast-align"
    "-Wcast-align=strict"
    "-Wcast-function-type"
    "-Wwrite-strings"
    "-Wclobbered"
    "-Wconversion"
    "-Wdangling-else"
    "-Wdangling-pointer"
    "-Wdangling-pointer=1"
    "-Wdangling-pointer=2"
    "-Wdate-time"
    "-Wempty-body"
    "-Wenum-compare"
    "-Wenum-conversion"
    "-Wenum-int-mismatch"
    "-Wjump-misses-init"
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
    "-Wstrict-prototypes"
    "-Wold-style-declaration"
    "-Wmissing-parameter-type"
    "-Wmissing-prototypes"
    "-Wmissing-declarations"
    "-Wmissing-field-initializers"
    "-Wnormalized=nfkc"
    "-Woverride-init"
    "-Wpacked"
    "-Wpacked-not-aligned"
    "-Wredundant-decls"
    "-Wrestrict"
    "-Wnested-externs"
    "-Winline"
    "-Winterference-size"
    "-Wint-in-bool-context"
    "-Winvalid-pch"
    "-Winvalid-utf8"
    "-Wlong-long"
    "-Wno-long-long"
    "-Wvariadic-macros"
    "-Wvector-operation-performance"
    "-Wvla"
    "-Wvla-parameter"
    "-Wvolatile-register-var"
    "-Wxor-used-as-pow"
    "-Wdisabled-optimization"
    "-Wpointer-sign"
    "-Wno-poison-system-directories"
    "-Wno-invalid-command-line-argument"
    "-Wno-unused-command-line-argument"
)

address_sanitizer_flags=(
    "-fsanitize=address"
    "-fsanitize-address-use-after-scope"
    "-fsanitize=pointer-compare"
    "-fsanitize=pointer-subtract"
    "-fsanitize-address-use-after-return=always"
)

cfi_sanitizer_flags=(
    "-fsanitize=cfi"
    "-fsanitize-cfi-cross-dso"
    "-fsanitize-cfi-icall-generalize-pointers"
    "-fsanitize-cfi-icall-experimental-normalize-integers"
    "-fsanitize-cfi-canonical-jump-tables"
    "-fsanitize=cfi-icall"
    "-fsanitize=function"
    "-fsanitize=cfi-cast-strict"
    "-fsanitize=cfi-derived-cast"
    "-fsanitize=cfi-unrelated-cast"
    "-fsanitize=cfi-nvcall"
    "-fsanitize=cfi-mfcall"
)

dataflow_sanitizer_flags=(
    "-fsanitize=dataflow"
    "-dfsan-combine-offset-labels-on-gep"
    "-dfsan-conditional-callbacks"
    "-dfsan-track-origins=2"
)

hwaddress_sanitizer_flags=("-fsanitize=hwaddress")
leak_sanitizer_flags=("-fsanitize=leak")
memory_sanitizer_flags=("-fsanitize=memory" "-fsanitize-memory-track-origins")
pointer_overflow_sanitizer_flags=("-fsanitize=pointer-overflow")
safe_stack_flags=("-fsanitize=safe-stack")
shadow_call_stack_flags=("-fsanitize=shadow-call-stack")
thread_sanitizer_flags=("-fsanitize=thread")

undefined_sanitizer_flags=(
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
    "-fsanitize=float-divide-by-zero"
    "-fsanitize=float-cast-overflow"
    "-fsanitize=nonnull-attribute"
    "-fsanitize=returns-nonnull-attribute"
    "-fsanitize=bool"
    "-fsanitize=enum"
    "-fsanitize=vptr"
    "-fsanitize=pointer-overflow"
    "-fsanitize=builtin"
    "-fsanitize=array-bounds"
    "-fsanitize=local-bounds"
    "-fsanitize=function"
    "-fsanitize=implicit-unsigned-integer-truncation"
    "-fsanitize=implicit-signed-integer-truncation"
    "-fsanitize=implicit-integer-sign-change"
    "-fsanitize=nullability-arg"
    "-fsanitize=nullability-assign"
    "-fsanitize=nullability-return"
    "-fsanitize=objc-cast"
    "-fsanitize=unsigned-shift-base"
    "-fsanitize=implicit-conversion"
    "-fsanitize=unsigned-integer-overflow"
)

cf_protection_flags=(
    "-fcf-protection=full"
    "-fcf-protection=branch"
    "-fcf-protection=return"
    "-fcf-protection=check"
    "-fcf-protection=none"
)

profile_flags=(
    "-fprofile-instr-generate"
    "-fprofile-generate"
)

# Process C compilers inline
for compiler in "${supported_c_compilers[@]}"; do
    echo "Checking: $compiler"
    flag_dir="../.flags/${compiler}"
    mkdir -p "$flag_dir"
    rm -f "$flag_dir"/*

    process_compiler_flags "$compiler" "$tmp_c_src" "analyzer"         "${analyzer_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_c_src" "code_generation"  "${code_generation_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_c_src" "debug"            "${debug_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_c_src" "instrumentation"  "${instrumentation_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_c_src" "optimization"     "${optimization_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_c_src" "warning"          "${warning_flags[@]}"

    process_sanitizer_category "$compiler" "$tmp_c_src" "address"          "${address_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "cfi"              "${cfi_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "dataflow"         "${dataflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "hwaddress"        "${hwaddress_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "leak"             "${leak_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "memory"           "${memory_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "pointer_overflow" "${pointer_overflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "safe_stack"       "${safe_stack_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "shadow_call_stack" "${shadow_call_stack_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "thread"           "${thread_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_c_src" "undefined"        "${undefined_sanitizer_flags[@]}"

    # Try exclusive picks in order, stop at first that works
    for flag in "${cf_protection_flags[@]}"; do
        set +e; is_flag_supported "$compiler" "$tmp_c_src" "$flag" "instrumentation"; rc=$?; set -e
        if [[ $rc -eq 1 ]]; then echo "Supported flag found: $flag"; break; fi
    done
    for flag in "${profile_flags[@]}"; do
        set +e; is_flag_supported "$compiler" "$tmp_c_src" "$flag" "instrumentation"; rc=$?; set -e
        if [[ $rc -eq 1 ]]; then echo "Supported flag found: $flag"; break; fi
    done
done

# Process C++ compilers inline
for compiler in "${supported_cxx_compilers[@]}"; do
    echo "Checking: $compiler"
    flag_dir="../.flags/${compiler}"
    mkdir -p "$flag_dir"
    rm -f "$flag_dir"/*

    process_compiler_flags "$compiler" "$tmp_cxx_src" "analyzer"         "${analyzer_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_cxx_src" "code_generation"  "${code_generation_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_cxx_src" "debug"            "${debug_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_cxx_src" "instrumentation"  "${instrumentation_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_cxx_src" "optimization"     "${optimization_flags[@]}"
    process_compiler_flags "$compiler" "$tmp_cxx_src" "warning"          "${warning_flags[@]}"

    process_sanitizer_category "$compiler" "$tmp_cxx_src" "address"          "${address_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "cfi"              "${cfi_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "dataflow"         "${dataflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "hwaddress"        "${hwaddress_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "leak"             "${leak_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "memory"           "${memory_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "pointer_overflow" "${pointer_overflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "safe_stack"       "${safe_stack_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "shadow_call_stack" "${shadow_call_stack_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "thread"           "${thread_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" "$tmp_cxx_src" "undefined"        "${undefined_sanitizer_flags[@]}"

    for flag in "${cf_protection_flags[@]}"; do
        set +e; is_flag_supported "$compiler" "$tmp_cxx_src" "$flag" "instrumentation"; rc=$?; set -e
        if [[ $rc -eq 1 ]]; then echo "Supported flag found: $flag"; break; fi
    done
    for flag in "${profile_flags[@]}"; do
        set +e; is_flag_supported "$compiler" "$tmp_cxx_src" "$flag" "instrumentation"; rc=$?; set -e
        if [[ $rc -eq 1 ]]; then echo "Supported flag found: $flag"; break; fi
    done
done
