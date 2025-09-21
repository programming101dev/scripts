#!/usr/bin/env bash
set -e

# ---------- Probe helpers ----------

# Probe one flag (language-aware)
# Usage: is_flag_supported <compiler> <lang:c|c++> <tmp_src> <flag> <array_name_by_ref>
is_flag_supported() {
    local compiler="$1"
    local lang="$2"          # c | c++
    local tmp_src="$3"
    local flag="$4"
    local supported_flags_ref="$5"

    local log="../.flags/${compiler}-${lang}.txt"
    mkdir -p "$(dirname "$log")"

    # Parse/type-check only; don't emit objects
    local probe_args=(-x "$lang" -fsyntax-only)

    # IMPORTANT:
    #  - Do NOT use -Werror globally (would turn benign warnings into errors)
    #  - For clang, force unknown-warning-option to be an error and silence unused-arg noise
    local extra_flags=()
    if [[ "$compiler" == clang* || "$compiler" == *clang++* ]]; then
        extra_flags+=(
            -Werror=unknown-warning-option
            -Wno-unknown-warning-option
            -Qunused-arguments
            -Wno-error=unused-command-line-argument
            -Wno-unused-command-line-argument
        )
    fi

    if "$compiler" "${probe_args[@]}" "${extra_flags[@]}" "$flag" "$tmp_src" >>"$log" 2>&1; then
        echo -e "\033[32m$flag supported by $compiler [$lang]\033[0m"
        eval "$supported_flags_ref+=('$flag')"
        return 1
    else
        echo -e "\033[31m$flag not supported by $compiler [$lang]\033[0m"
        echo "$flag is not supported" >>"$log"
        echo "------------------------------" >>"$log"
        return 0
    fi
}

# Collect supported flags for a category and write a file
# Usage: process_compiler_flags <compiler> <lang> <tmp_src> <category> <flags...>
process_compiler_flags() {
    local compiler="$1"
    local lang="$2"
    local tmp_src="$3"
    local category="$4"
    shift 4
    local flags=("$@")
    local supported_flags=()
    for flag in "${flags[@]}"; do
        set +e; is_flag_supported "$compiler" "$lang" "$tmp_src" "$flag" supported_flags; set -e
    done
    local out="../.flags/${compiler}"
    mkdir -p "$out"
    printf "%s" "$(IFS=" "; echo "${supported_flags[*]}")" > "${out}/${category}_flags.txt"
}

# Collect supported sanitizer flags for a category and write a file
# Usage: process_sanitizer_category <compiler> <lang> <tmp_src> <category> <flags...>
process_sanitizer_category() {
    local compiler="$1"
    local lang="$2"
    local tmp_src="$3"
    local category_name="$4"
    shift 4
    local flags=("$@")
    local supported_flags=()
    for flag in "${flags[@]}"; do
        set +e; is_flag_supported "$compiler" "$lang" "$tmp_src" "$flag" supported_flags; set -e
    done
    local out="../.flags/${compiler}"
    mkdir -p "$out"
    printf "%s" "$(IFS=" "; echo "${supported_flags[*]}")" > "${out}/${category_name}_sanitizer_flags.txt"
}

# ---------- Temporary sources (CLEAN) ----------

tmp_c_src=$(mktemp "/tmp/flagprobe_XXXXXX.c")
cat > "$tmp_c_src" <<'EOF'
int main(void){return 0;}
EOF

tmp_cxx_src=$(mktemp "/tmp/flagprobe_XXXXXX.cpp")
cat > "$tmp_cxx_src" <<'EOF'
int main(){return 0;}
EOF

trap "rm -f '$tmp_c_src' '$tmp_cxx_src' '*.gcno'" EXIT

# ---------- Compiler lists ----------

supported_c_compilers=()
while IFS= read -r line; do
    [[ -n "$line" ]] && supported_c_compilers+=("$line")
done < supported_c_compilers.txt

supported_cxx_compilers=()
while IFS= read -r line; do
    [[ -n "$line" ]] && supported_cxx_compilers+=("$line")
done < supported_cxx_compilers.txt

# ---------- Flag sets ----------

# GCC Static Analyzer warnings (GCC-only)
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
    "-fvar-tracking" "-fvar-tracking-assignments"
    "-gdescribe-dies" "-gpubnames" "-ggnu-pubname"
    "-grecord-gcc-switches"
    "-gno-strict-dwarf" "-gcolumn-info" "-gstatement-frontiers"
    "-gvariable-location-views" "-gno-internal-reset-location-views"
    "-ginline-points"
    "-fno-eliminate-unused-debug-types"
)

# Split instrumentations by language (e.g., -fvtable-verify is C++-only)
instrumentation_c_flags=(
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
    "-finstrument-functions"
    "-finstrument-functions-once"
)

instrumentation_cxx_flags=(
    "${instrumentation_c_flags[@]}"
    "-fvtable-verify=std"
)

optimization_flags=("-O0")

# Trimmed warning set (typo fixed; kept representative)
warning_flags=(
    "-Werror"
    "-Wpedantic"
    "-pedantic-errors"
    "-Wall" "-Wextra"
    "-Wformat" "-Wformat-security"
    "-Wnonnull" "-Wnull-dereference"
    "-Wimplicit" "-Wimplicit-fallthrough"
    "-Wignored-qualifiers" "-Wreturn-type"
    "-Wswitch" "-Wswitch-enum"
    "-Wunused" "-Wunused-parameter" "-Wunused-variable"
    "-Wuninitialized"
    "-Wstrict-aliasing"
    "-Wshadow"
    "-Wconversion"
    "-Wsign-compare" "-Wsign-conversion"
    "-Wcast-align" "-Wcast-function-type"
    "-Wold-style-declaration" "-Wmissing-prototypes"
    "-Wmissing-declarations"
    "-Wpacked" "-Wredundant-decls"
    "-Wrestrict"
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
    "-fsanitize=shift" "-fsanitize=shift-exponent" "-fsanitize=shift-base"
    "-fsanitize=integer-divide-by-zero" "-fsanitize=unreachable"
    "-fsanitize=vla-bound" "-fsanitize=null" "-fsanitize=return"
    "-fsanitize=signed-integer-overflow" "-fsanitize=bounds" "-fsanitize=bounds-strict"
    "-fsanitize=alignment" "-fsanitize=float-divide-by-zero" "-fsanitize=float-cast-overflow"
    "-fsanitize=nonnull-attribute" "-fsanitize=returns-nonnull-attribute"
    "-fsanitize=bool" "-fsanitize=enum" "-fsanitize=vptr"
    "-fsanitize=pointer-overflow" "-fsanitize=builtin"
    "-fsanitize=array-bounds" "-fsanitize=local-bounds" "-fsanitize=function"
    "-fsanitize=implicit-unsigned-integer-truncation"
    "-fsanitize=implicit-signed-integer-truncation"
    "-fsanitize=implicit-integer-sign-change"
    "-fsanitize=nullability-arg" "-fsanitize=nullability-assign" "-fsanitize=nullability-return"
    "-fsanitize=objc-cast" "-fsanitize=unsigned-shift-base"
    "-fsanitize=implicit-conversion" "-fsanitize=unsigned-integer-overflow"
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

# ---------- Run probes ----------

# C compilers
for compiler in "${supported_c_compilers[@]}"; do
    echo "Checking: $compiler [C]"
    out="../.flags/${compiler}"
    mkdir -p "$out"; rm -f "$out"/*

    # GCC analyzer flags are GCC-only
    if [[ "$compiler" == gcc* || "$compiler" == *gcc ]]; then
        process_compiler_flags "$compiler" c "$tmp_c_src" "analyzer" "${analyzer_flags[@]}"
    fi

    process_compiler_flags "$compiler" c "$tmp_c_src" "code_generation"  "${code_generation_flags[@]}"
    process_compiler_flags "$compiler" c "$tmp_c_src" "debug"            "${debug_flags[@]}"
    process_compiler_flags "$compiler" c "$tmp_c_src" "instrumentation"  "${instrumentation_c_flags[@]}"
    process_compiler_flags "$compiler" c "$tmp_c_src" "optimization"     "${optimization_flags[@]}"
    process_compiler_flags "$compiler" c "$tmp_c_src" "warning"          "${warning_flags[@]}"

    process_sanitizer_category "$compiler" c "$tmp_c_src" "address"          "${address_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "cfi"              "${cfi_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "dataflow"         "${dataflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "hwaddress"        "${hwaddress_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "leak"             "${leak_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "memory"           "${memory_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "pointer_overflow" "${pointer_overflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "safe_stack"       "${safe_stack_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "shadow_call_stack" "${shadow_call_stack_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "thread"           "${thread_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c "$tmp_c_src" "undefined"        "${undefined_sanitizer_flags[@]}"

    for flag in "${cf_protection_flags[@]}"; do
        set +e; is_flag_supported "$compiler" c "$tmp_c_src" "$flag" "instrumentation"; rc=$?; set -e
        if [[ $rc -eq 1 ]]; then echo "Supported flag found: $flag"; break; fi
    done
    for flag in "${profile_flags[@]}"; do
        set +e; is_flag_supported "$compiler" c "$tmp_c_src" "$flag" "instrumentation"; rc=$?; set -e
        if [[ $rc -eq 1 ]]; then echo "Supported flag found: $flag"; break; fi
    done
done

# C++ compilers
for compiler in "${supported_cxx_compilers[@]}"; do
    echo "Checking: $compiler [C++]"
    out="../.flags/${compiler}"
    mkdir -p "$out"; rm -f "$out"/*

    if [[ "$compiler" == g++* || "$compiler" == *g++ ]]; then
        process_compiler_flags "$compiler" c++ "$tmp_cxx_src" "analyzer" "${analyzer_flags[@]}"
    fi

    process_compiler_flags "$compiler" c++ "$tmp_cxx_src" "code_generation"  "${code_generation_flags[@]}"
    process_compiler_flags "$compiler" c++ "$tmp_cxx_src" "debug"            "${debug_flags[@]}"
    process_compiler_flags "$compiler" c++ "$tmp_cxx_src" "instrumentation"  "${instrumentation_cxx_flags[@]}"
    process_compiler_flags "$compiler" c++ "$tmp_cxx_src" "optimization"     "${optimization_flags[@]}"
    process_compiler_flags "$compiler" c++ "$tmp_cxx_src" "warning"          "${warning_flags[@]}"

    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "address"          "${address_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "cfi"              "${cfi_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "dataflow"         "${dataflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "hwaddress"        "${hwaddress_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "leak"             "${leak_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "memory"           "${memory_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "pointer_overflow" "${pointer_overflow_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "safe_stack"       "${safe_stack_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "shadow_call_stack" "${shadow_call_stack_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "thread"           "${thread_sanitizer_flags[@]}"
    process_sanitizer_category "$compiler" c++ "$tmp_cxx_src" "undefined"        "${undefined_sanitizer_flags[@]}"

    for flag in "${cf_protection_flags[@]}"; do
        set +e; is_flag_supported "$compiler" c++ "$tmp_cxx_src" "$flag" "instrumentation"; rc=$?; set -e
        if ([[ $rc -eq 1 ]]); then echo "Supported flag found: $flag"; break; fi
    done
    for flag in "${profile_flags[@]}"; do
        set +e; is_flag_supported "$compiler" c++ "$tmp_cxx_src" "$flag" "instrumentation"; rc=$?; set -e
        if ([[ $rc -eq 1 ]]); then echo "Supported flag found: $flag"; break; fi
    done
done
