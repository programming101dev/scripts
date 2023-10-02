#!/usr/bin/env bash

# Exit the script if any command fails
set -e

# Parse command-line options
while getopts "c:" opt; do
    case "$opt" in
        c) compiler="$OPTARG";;
        \?) echo "Usage: $0 [-c compiler (e.g. gcc/clang)]"; exit 1;;
    esac
done

# Check if the -c option has been provided
if [ -z "$compiler" ]; then
    echo "Error: -c option is required."
    exit 1
fi

# Function to create or overwrite the test.c file
create_or_overwrite_test_c_file() {
    echo -e "int main(void) { return 0; }" > test.c
}

# Function to compile the test.c file with the specified compiler and -Werror
compile_test_c_file() {
    "$compiler" -Werror test.c -o test
    rm -f test
}

# Function to check if a flag is supported by the compiler
is_flag_supported() {
    local flag="$1"
    if ("$compiler" $flag -E - < /dev/null &> /dev/null); then
        echo "Flag '$flag' is supported by $compiler."
        supported_flags+=" $flag"
    else
        echo "Flag '$flag' is not supported by $compiler."
    fi
}

WARNING_FLAGS=(
  "-Wno-invalid-command-line-argument"
  "-Wno-unused-command-line-argument"
  "-pedantic-errors"
  "-Waddress"
  "-Waggregate-return"
  "-Wall"
  "-Walloc-zero"
  "-Walloca"
  "-Warith-conversion"
  "-Warray-bounds"
  "-Wattribute-alias=2"
  "-Wbad-function-cast"
  "-Wbool-compare"
  "-Wbool-operation"
  "-Wcast-align"
  "-Wcast-align=strict"
  "-Wcast-function-type"
  "-Wcast-qual"
  "-Wchar-subscripts"
  "-Wclobbered"
  "-Wcomment"
  "-Wconversion"
  "-Wdangling-else"
  "-Wdangling-pointer"
  "-Wdangling-pointer=2"
  "-Wdate-time"
  "-Wdeclaration-after-statement"
  "-Wdisabled-optimization"
  "-Wdouble-promotion"
  "-Wduplicated-branches"
  "-Wduplicated-cond"
  "-Wempty-body"
  "-Wenum-compare"
  "-Wenum-conversion"
  "-Wenum-int-mismatch"
  "-Wexpansion-to-defined"
  "-Wextra"
  "-Wfatal-errors"
  "-Wflex-array-member-not-at-end"
  "-Wfloat-conversion"
  "-Wfloat-equal"
  "-Wformat"
  "-Wformat-overflow=2"
  "-Wformat-security"
  "-Wformat-signedness"
  "-Wformat-truncation=2"
  "-Wformat-y2k"
  "-Wformat=2"
  "-Wframe-address"
  "-Wignored-qualifiers"
  "-Wimplicit"
  "-Wimplicit-fallthrough"
  "-Wimplicit-fallthrough=3"
  "-Winfinite-recursion"
  "-Winit-self"
  "-Winline"
  "-Wint-in-bool-context"
  "-Winvalid-pch"
  "-Winvalid-utf8"
  "-Wjump-misses-init"
  "-Wlogical-not-parentheses"
  "-Wlogical-op"
  "-Wmain"
  "-Wmaybe-uninitialized"
  "-Wmemset-elt-size"
  "-Wmemset-transposed-args"
  "-Wmisleading-indentation"
  "-Wmissing-attributes"
  "-Wmissing-braces"
  "-Wmissing-declarations"
  "-Wmissing-field-initializers"
  "-Wmissing-format-attribute"
  "-Wmissing-include-dirs"
  "-Wmissing-noreturn"
  "-Wmissing-parameter-type"
  "-Wmissing-prototypes"
  "-Wmissing-variable-declarations"
  "-Wmultistatement-macros"
  "-Wnested-externs"
  "-Wnull-dereference"
  "-Wold-style-declaration"
  "-Wold-style-definition"
  "-Wopenacc-parallelism"
  "-Wopenmp-simd"
  "-Woverlength-strings"
  "-Wpacked"
  "-Wpacked-not-aligned"
  "-Wparentheses"
  "-Wpedantic"
  "-Wpointer-arith"
  "-Wpointer-sign"
  "-Wredundant-decls"
  "-Wrestrict"
  "-Wreturn-type"
  "-Wsequence-point"
  "-Wshadow"
  "-Wshadow=compatible-local"
  "-Wshadow=global"
  "-Wshadow=local"
  "-Wshift-negative-value"
  "-Wshift-overflow=2"
  "-Wsign-compare"
  "-Wsign-conversion"
  "-Wsizeof-array-div"
  "-Wsizeof-pointer-div"
  "-Wsizeof-pointer-memaccess"
  "-Wstack-protector"
  "-Wstrict-aliasing"
  "-Wstrict-aliasing=3"
  "-Wstrict-flex-arrays"
  "-Wstrict-prototypes"
  "-Wstring-compare"
  "-Wswitch"
  "-Wswitch-default"
  "-Wswitch-enum"
  "-Wsync-nand"
  "-Wtautological-compare"
  "-Wtrampolines"
  "-Wtrigraphs"
  "-Wtrivial-auto-var-init"
  "-Wtsan"
  "-Wtype-limits"
  "-Wundef"
  "-Wuninitialized"
  "-Wunknown-pragmas"
  "-Wunused"
  "-Wunused-but-set-parameter"
  "-Wunused-but-set-variable"
  "-Wunused-const-variable"
  "-Wunused-const-variable=2"
  "-Wunused-function"
  "-Wunused-label"
  "-Wunused-local-typedefs"
  "-Wunused-macros"
  "-Wunused-parameter"
  "-Wunused-value"
  "-Wunused-variable"
  "-Wvariadic-macros"
  "-Wvector-operation-performance"
  "-Wvla"
  "-Wvolatile-register-var"
  "-Wwrite-strings"
  "-Wxor-used-as-pow"
  "-Wzero-length-bounds"
  "-Wbidi-chars=unpaired,ucn"
  "-Wc++-compat"
  "-W"
  "-Wabsolute-value"
  "-Waddress-of-packed-member"
  "-Waddress-of-temporary"
  "-Waix-compat"
  "-Walign-mismatch"
  "-Walloca-with-align-alignof"
  "-Walways-inline-coroutine"
  "-Wambiguous-ellipsis"
  "-Wambiguous-macro"
  "-Wambiguous-member-template"
  "-Wambiguous-reversed-operator"
  "-Wanalyzer-incompatible-plugin"
  "-Wanon-enum-enum-conversion"
  "-Wanonymous-pack-parens"
  "-Warc"
  "-Warc-bridge-casts-disallowed-in-nonarc"
  "-Warc-maybe-repeated-use-of-weak"
  "-Warc-non-pod-memaccess"
  "-Warc-performSelector-leaks"
  "-Warc-repeated-use-of-weak"
  "-Warc-retain-cycles"
  "-Warc-unsafe-retained-assign"
  "-Wargument-outside-range"
  "-Wargument-undefined-behaviour"
  "-Warray-bounds-pointer-arithmetic"
  "-Warray-parameter"
  "-Wasm"
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
  "-Watomic-property-with-user-defined-accessor"
  "-Wattribute-packed-for-bitfield"
  "-Wattribute-warning"
  "-Wattributes"
  "-Wauto-disable-vptr-sanitizer"
  "-Wauto-import"
  "-Wauto-storage-class"
  "-Wauto-var-id"
  "-Wavailability"
  "-Wavr-rtlib-linking-quirks"
  "-Wbackend-plugin"
  "-Wbackslash-newline-escape"
  "-Wbinary-literal"
  "-Wbind-to-temporary-copy"
  "-Wbinding-in-condition"
  "-Wbit-int-extension"
  "-Wbitfield-constant-conversion"
  "-Wbitfield-enum-conversion"
  "-Wbitfield-width"
  "-Wbitwise-conditional-parentheses"
  "-Wbitwise-instead-of-logical"
  "-Wbitwise-op-parentheses"
  "-Wblock-capture-autoreleasing"
  "-Wbool-conversion"
  "-Wbool-conversions"
  "-Wbool-operation"
  "-Wbraced-scalar-init"
  "-Wbranch-protection"
  "-Wbridge-cast"
  "-Wbuiltin-assume-aligned-alignment"
  "-Wbuiltin-macro-redefined"
  "-Wbuiltin-memcpy-chk-size"
  "-Wbuiltin-requires-header"
  "-Wc11-extensions"
  "-Wc23-compat"
  "-Wc23-extensions"
  "-Wc2x-compat"
  "-Wc2x-extensions"
  "-Wc99-compat"
  "-Wc99-designator"
  "-Wc99-extensions"
  "-Wcalled-once-parameter"
  "-Wcast-calling-convention"
  "-Wcast-function-type-strict"
  "-Wcast-of-sel-type"
  "-Wcast-qual-unrelated"
  "-WCFString-literal"
  "-Wchar-align"
  "-WCL4"
  "-Wclang-cl-pch"
  "-Wclass-varargs"
  "-Wcmse-union-leak"
  "-Wcomma"
  "-Wcomments"
  "-Wcompletion-handler"
  "-Wcomplex-component-init"
  "-Wcompound-token-split"
  "-Wcompound-token-split-by-macro"
  "-Wcompound-token-split-by-space"
  "-Wconditional-type-mismatch"
  "-Wconditional-uninitialized"
  "-Wconfig-macros"
  "-Wconstant-conversion"
  "-Wconstant-evaluated"
  "-Wconstant-logical-operand"
  "-Wconstexpr-not-const"
  "-Wconsumed"
  "-Wcoro-non-aligned-allocation-function"
  "-Wcoroutine"
  "-Wcoroutine-missing-unhandled-exception"
  "-Wcovered-switch-default"
  "-Wcpp"
  "-Wcstring-format-directive"
  "-Wctu"
  "-Wcuda-compat"
  "-Wcustom-atomic-properties"
  "-Wcxx-attribute-extension"
  "-Wdangling"
  "-Wdangling-field"
  "-Wdangling-gsl"
  "-Wdangling-initializer-list"
  "-Wdealloc-in-category"
  "-Wdebug-compression-unavailable"
  "-Wdefaulted-function-deleted"
  "-Wdelegating-ctor-cycles"
  "-Wdelimited-escape-sequence-extension"
  "-Wdeprecate-lax-vec-conv-all"
  "-Wdeprecated"
  "-Wdeprecated-altivec-src-compat"
  "-Wdeprecated-anon-enum-enum-conversion"
  "-Wdeprecated-array-compare"
  "-Wdeprecated-attributes"
  "-Wdeprecated-builtins"
  "-Wdeprecated-comma-subscript"
  "-Wdeprecated-coroutine"
  "-Wdeprecated-declarations"
  "-Wdeprecated-dynamic-exception-spec"
  "-Wdeprecated-enum-compare"
  "-Wdeprecated-enum-compare-conditional"
  "-Wdeprecated-implementations"
  "-Wdeprecated-increment-bool"
  "-Wdeprecated-literal-operator"
  "-Wdeprecated-non-prototype"
  "-Wdeprecated-pragma"
  "-Wdeprecated-redundant-constexpr-static-def"
  "-Wdeprecated-register"
  "-Wdeprecated-static-analyzer-flag"
  "-Wdeprecated-this-capture"
  "-Wdeprecated-type"
  "-Wdeprecated-volatile"
  "-Wdeprecated-writable-strings"
  "-Wdirect-ivar-access"
  "-Wdisabled-macro-expansion"
  "-Wdiscard-qual"
  "-Wdistributed-object-modifiers"
  "-Wdiv-by-zero"
  "-Wdivision-by-zero"
  "-Wdll-attribute-on-redeclaration"
  "-Wdllexport-explicit-instantiation-decl"
  "-Wdllimport-static-field-def"
#  "-Wdocumentation"
#  "-Wdocumentation-deprecated-sync"
#  "-Wdocumentation-html"
#  "-Wdocumentation-pedantic"
#  "-Wdocumentation-unknown-command"
  "-Wdollars-in-identifier-extension"
  "-Wdollar-in-identifier-extension"
  "-Wdtor-name"
  "-Wdtor-typedef"
  "-Wduplicate-decl-specifier"
  "-Wduplicate-enum"
  "-Wduplicate-method-arg"
  "-Wduplicate-method-match"
  "-Wduplicate-protocol"
  "-Wdxil-validation"
  "-Wdynamic-class-memaccess"
  "-Wdynamic-exception-spec"
  "-Weager-load-cxx-named-modules"
  "-Welaborated-enum-base"
  "-Welaborated-enum-class"
  "-Wembedded-directive"
  "-Wempty-decomposition"
  "-Wempty-init-stmt"
  "-Wempty-translation-unit"
  "-Wencode-type"
  "-Wendif-labels"
  "-Wenum-compare-conditional"
  "-Wenum-compare-switch"
  "-Wenum-constexpr-conversion"
  "-Wenum-enum-conversion"
  "-Wenum-float-conversion"
  "-Wenum-too-large"
  "-Wexcess-initializers"
  "-Wexcessive-regsave"
  "-Wexit-time-destructors"
  "-Wexperimental-header-units"
  "-Wexplicit-initialize-call"
  "-Wexplicit-ownership-type"
  "-Wexport-unnamed"
  "-Wextern-c-compat"
  "-Wextern-initializer"
  "-Wextra-qualification"
  "-Wextra-semi-stmt"
  "-Wextra-tokens"
  "-Wfinal-dtor-non-final-class"
  "-Wfinal-macro"
  "-Wfixed-enum-extension"
  "-Wfixed-point-overflow"
  "-Wflag-enum"
  "-Wflexible-array-extensions"
  "-Wfloat-overflow-conversion"
  "-Wfloat-zero-conversion"
  "-Wfor-loop-analysis"
  "-Wformat-extra-args"
  "-Wformat-insufficient-args"
  "-Wformat-invalid-specifier"
  "-Wformat-non-iso"
  "-Wformat-nonliteral"
  "-Wformat-pedantic"
  "-Wformat-type-confusion"
  "-Wformat-zero-length"
  "-Wfortify-source"
  "-Wfour-char-constants"
  "-Wframework-include-private-from-public"
  "-Wfree-nonheap-object"
  "-Wfunction-multiversion"
  "-Wfuse-ld-path"
  "-Wfuture-attribute-extensions"
  "-Wfuture-compat"
  "-Wgcc-compat"
  "-Wgeneric-type-extension"
  "-Wglobal-constructors"
  "-Wglobal-isel"
  "-Wgnu"
  "-Wgnu-alignof-expression"
  "-Wgnu-anonymous-struct"
  "-Wgnu-array-member-paren-init"
  "-Wgnu-auto-type"
  "-Wgnu-binary-literal"
  "-Wgnu-case-range"
  "-Wgnu-complex-integer"
  "-Wgnu-compound-literal-initializer"
  "-Wgnu-conditional-omitted-operand"
  "-Wgnu-designator"
  "-Wgnu-empty-initializer"
  "-Wgnu-empty-struct"
  "-Wgnu-flexible-array-initializer"
  "-Wgnu-flexible-array-union-member"
  "-Wgnu-folding-constant"
  "-Wgnu-imaginary-constant"
  "-Wgnu-include-next"
  "-Wgnu-inline-cpp-without-extern"
  "-Wgnu-label-as-value"
  "-Wgnu-line-marker"
  "-Wgnu-null-pointer-arithmetic"
  "-Wgnu-offsetof-extensions"
  "-Wgnu-pointer-arith"
  "-Wgnu-redeclared-enum"
  "-Wgnu-statement-expression"
  "-Wgnu-statement-expression-from-macro-expansion"
  "-Wgnu-static-float-init"
  "-Wgnu-string-literal-operator-template"
  "-Wgnu-union-cast"
  "-Wgnu-variable-sized-type-not-at-end"
  "-Wgnu-zero-line-directive"
  "-Wgnu-zero-variadic-macro-arguments"
  "-Wgpu-maybe-wrong-side"
  "-Wheader-guard"
  "-Wheader-hygiene"
  "-Whip-omp-target-directives"
  "-Whip-only"
  "-Whlsl-extensions"
  "-Widiomatic-parentheses"
  "-Wignored-attributes"
  "-Wignored-availability-without-sdk-settings"
  "-Wignored-optimization-argument"
  "-Wignored-pragma-intrinsic"
  "-Wignored-pragma-optimize"
  "-Wignored-pragmas"
  "-Wignored-reference-qualifiers"
  "-Wimplicit-atomic-properties"
  "-Wimplicit-const-int-float-conversion"
  "-Wimplicit-conversion-floating-point-to-bool"
  "-Wimplicit-exception-spec-mismatch"
  "-Wimplicit-fallthrough-per-function"
  "-Wimplicit-fixed-point-conversion"
  "-Wimplicit-float-conversion"
  "-Wimplicit-function-declaration"
  "-Wimplicit-int"
  "-Wimplicit-int-conversion"
  "-Wimplicit-int-float-conversion"
  "-Wimplicit-retain-self"
  "-Wimplicitly-unsigned-literal"
  "-Wimport"
  "-Wimport-preprocessor-directive-pedantic"
  "-Winclude-next-absolute-path"
  "-Winclude-next-outside-header"
  "-Wincompatible-exception-spec"
  "-Wincompatible-function-pointer-types"
  "-Wincompatible-function-pointer-types-strict"
  "-Wincompatible-library-redeclaration"
  "-Wincompatible-ms-pragma-section"
  "-Wincompatible-ms-struct"
  "-Wincompatible-pointer-types"
  "-Wincompatible-pointer-types-discards-qualifiers"
  "-Wincompatible-property-type"
  "-Wincompatible-sysroot"
  "-Wincomplete-framework-module-declaration"
  "-Wincomplete-implementation"
  "-Wincomplete-module"
  "-Wincomplete-setjmp-declaration"
  "-Wincomplete-umbrella"
  "-Winconsistent-dllimport"
  "-Winconsistent-missing-destructor-override"
  "-Winconsistent-missing-override"
  "-Wincrement-bool"
  "-WIndependentClass-attribute"
  "-Winitializer-overrides"
  "-Winjected-class-name"
  "-Winline-asm"
  "-Winline-namespace-reopened-noninline"
  "-Winline-new-delete"
  "-Winstantiation-after-specialization"
  "-Wint-conversion"
  "-Wint-conversions"
  "-Wint-to-pointer-cast"
  "-Wint-to-void-pointer-cast"
  "-Winteger-overflow"
  "-Winvalid-constexpr"
  "-Winvalid-iboutlet"
  "-Winvalid-initializer-from-system-header"
  "-Winvalid-ios-deployment-target"
  "-Winvalid-no-builtin-names"
  "-Winvalid-noreturn"
  "-Winvalid-or-nonexistent-directory"
  "-Winvalid-partial-specialization"
  "-Winvalid-pp-token"
  "-Winvalid-source-encoding"
  "-Winvalid-static-assert-message"
  "-Winvalid-token-paste"
  "-Winvalid-unevaluated-string"
  "-Wjump-seh-finally"
  "-Wkeyword-compat"
  "-Wkeyword-macro"
  "-Wknr-promoted-parameter"
  "-Wlanguage-extension-token"
  "-Wlarge-by-value-copy"
  "-Wliblto"
  "-Wlinker-warnings"
  "-Wliteral-conversion"
  "-Wliteral-range"
  "-Wlocal-type-template-args"
  "-Wlogical-op-parentheses"
  "-Wloop-analysis"
  "-Wmacro-redefined"
  "-Wmain-return-type"
  "-Wmalformed-warning-check"
  "-Wmany-braces-around-scalar-init"
  "-Wmathematical-notation-identifier-extension"
  "-Wmax-tokens"
  "-Wmax-unsigned-zero"
  "-Wmemsize-comparison"
  "-Wmethod-signatures"
  "-Wmisexpect"
  "-Wmismatched-parameter-types"
  "-Wmismatched-return-types"
  "-Wmissing-constinit"
  "-Wmissing-exception-spec"
  "-Wmissing-method-return-type"
  "-Wmissing-multilib"
  "-Wmissing-noescape"
  "-Wmissing-noreturn"
  "-Wmissing-prototype-for-cc"
  "-Wmissing-selector-name"
  "-Wmissing-sysroot"
  "-Wmisspelled-assumption"
  "-Wmodule-conflict"
  "-Wmodule-file-config-mismatch"
  "-Wmodule-file-extension"
  "-Wmodule-import-in-extern-c"
  "-Wmodules-ambiguous-internal-linkage"
  "-Wmodules-import-nested-redundant"
  "-Wmost"
  "-Wmove"
  "-Wmsvc-include"
  "-Wmsvc-not-found"
  "-Wmulti-gpu"
  "-Wmultichar"
  "-Wmultiple-move-vbase"
  "-Wnarrowing"
  "-Wnested-anon-types"
  "-Wnew-returns-null"
  "-Wnewline-eof"
  "-Wnsconsumed-mismatch"
  "-WNSObject-attribute"
  "-Wnsreturns-mismatch"
  "-Wnull-arithmetic"
  "-Wnull-character"
  "-Wnull-conversion"
  "-Wnull-pointer-arithmetic"
  "-Wnull-pointer-subtraction"
  "-Wnullability"
  "-Wnullability-completeness"
  "-Wnullability-completeness-on-arrays"
  "-Wnullability-declspec"
  "-Wnullability-extension"
  "-Wnullability-inferred-on-nested-type"
  "-Wnullable-to-nonnull-conversion"
  "-Wodr"
  "-Wopencl-unsupported-rgba"
  "-Wopenmp"
  "-Wopenmp-51-extensions"
  "-Wopenmp-clauses"
  "-Wopenmp-extensions"
  "-Wopenmp-loop-form"
  "-Wopenmp-mapping"
  "-Wopenmp-target"
  "-Wopenmp-target-exception"
  "-Woption-ignored"
  "-Wordered-compare-function-pointers"
  "-Wout-of-line-declaration"
  "-Wout-of-scope-function"
  "-Wover-aligned"
  "-Woverflow"
  "-Woverloaded-shift-op-parentheses"
  "-Woverride-init"
  "-Woverride-module"
  "-Woverriding-method-mismatch"
  "-Woverriding-option"
  "-Wpacked-non-pod"
  "-Wparentheses-equality"
  "-Wpartial-availability"
  "-Wpass-failed"
  "-Wpch-date-time"
  "-Wpedantic-core-features"
  "-Wpedantic-macros"
  "-Wpointer-bool-conversion"
  "-Wpointer-compare"
  "-Wpointer-integer-compare"
  "-Wpointer-to-enum-cast"
  "-Wpointer-to-int-cast"
  "-Wpointer-type-mismatch"
  "-Wpoison-system-directories"
  "-Wpotentially-direct-selector"
  "-Wpotentially-evaluated-expression"
  "-Wpragma-clang-attribute"
  "-Wpragma-once-outside-header"
  "-Wpragma-pack"
  "-Wpragma-pack-suspicious-include"
  "-Wpragma-system-header-outside-header"
  "-Wpragmas"
  "-Wpre-c23-compat"
  "-Wpre-c23-compat-pedantic"
  "-Wpre-c2x-compat"
  "-Wpre-c2x-compat-pedantic"
  "-Wpre-openmp-51-compat"
  "-Wpredefined-identifier-outside-function"
  "-Wprivate-extern"
  "-Wprivate-header"
  "-Wprivate-module"
  "-Wprofile-instr-missing"
  "-Wprofile-instr-out-of-date"
  "-Wprofile-instr-unprofiled"
  "-Wproperty-access-dot-syntax"
  "-Wproperty-attribute-mismatch"
  "-Wprotocol-property-synthesis-ambiguity"
  "-Wpsabi"
  "-Wqualified-void-return-type"
  "-Wquoted-include-in-framework-header"
  "-Wrange-loop-analysis"
  "-Wrange-loop-bind-reference"
  "-Wread-modules-implicitly"
  "-Wread-only-types"
  "-Wreadonly-iboutlet-property"
  "-Wreceiver-expr"
  "-Wreceiver-forward-class"
  "-Wredeclared-class-member"
  "-Wredundant-consteval-if"
  "-Wredundant-parens"
  "-Wreinterpret-base-class"
  "-Wreorder-ctor"
  "-Wreorder-init-list"
  "-Wrequires-super-attribute"
  "-Wreserved-id-macro"
  "-Wreserved-identifier"
  "-Wreserved-macro-identifier"
  "-Wreserved-module-identifier"
  "-Wreserved-user-defined-literal"
  "-Wrestrict-expansion"
  "-Wretained-language-linkage"
  "-Wreturn-local-addr"
  "-Wreturn-stack-address"
  "-Wreturn-std-move"
  "-Wreturn-type-c-linkage"
  "-Wrewrite-not-bool"
  "-Wrtti"
  "-Wsarif-format-unstable"
  "-Wsection"
  "-Wselector-type-mismatch"
  "-Wself-assign"
  "-Wself-assign-field"
  "-Wself-assign-overloaded"
  "-Wself-move"
  "-Wsemicolon-before-method-body"
  "-Wsentinel"
  "-Wserialized-diagnostics"
  "-Wshadow-all"
  "-Wshadow-field"
  "-Wshadow-field-in-constructor"
  "-Wshadow-field-in-constructor-modified"
  "-Wshadow-uncaptured-local"
  "-Wshift-count-negative"
  "-Wshift-count-overflow"
  "-Wshift-op-parentheses"
  "-Wshift-overflow"
  "-Wshift-sign-overflow"
  "-Wshorten-64-to-32"
  "-Wsigned-enum-bitfield"
  "-Wsigned-unsigned-wchar"
  "-Wsingle-bit-bitfield-constant-conversion"
  "-Wsizeof-array-argument"
  "-Wsizeof-array-decay"
  "-Wslash-u-filename"
  "-Wslh-asm-goto"
  "-Wsometimes-uninitialized"
  "-Wsource-mgr"
  "-Wsource-uses-openmp"
  "-Wspir-compat"
  "-Wspirv-compat"
  "-Wstack-exhausted"
  "-Wstatic-float-init"
  "-Wstatic-in-inline"
  "-Wstatic-inline-explicit-instantiation"
  "-Wstatic-local-in-inline"
  "-Wstatic-self-init"
  "-Wstdlibcxx-not-found"
  "-Wstrict-potentially-direct-selector"
  "-Wstring-concatenation"
  "-Wstring-conversion"
  "-Wstring-plus-char"
  "-Wstring-plus-int"
  "-Wstrlcpy-strlcat-size"
  "-Wstrncat-size"
  "-Wsuggest-attribute=const"
  "-Wsuggest-attribute=malloc"
  "-Wsuggest-attribute=noreturn"
  "-Wsuggest-attribute=pure"
  "-Wsuggest-destructor-override"
  "-Wsuper-class-method-mismatch"
  "-Wsuspicious-bzero"
  "-Wsuspicious-memaccess"
  "-Wswift-name-attribute"
  "-Wswitch-bool"
  "-Wsync-alignment"
  "-Wsync-fetch-and-nand-semantics-changed"
  "-Wtarget-clones-mixed-specifiers"
  "-Wtautological-bitwise-compare"
  "-Wtautological-constant-compare"
  "-Wtautological-constant-in-range-compare"
  "-Wtautological-constant-out-of-range-compare"
  "-Wtautological-negation-compare"
  "-Wtautological-overlap-compare"
  "-Wtautological-pointer-compare"
  "-Wtautological-type-limit-compare"
  "-Wtautological-undefined-compare"
  "-Wtautological-unsigned-char-zero-compare"
  "-Wtautological-unsigned-enum-zero-compare"
  "-Wtautological-unsigned-zero-compare"
  "-Wtautological-value-range-compare"
  "-Wtcb-enforcement"
  "-Wtentative-definition-incomplete-type"
  "-Wthread-safety"
  "-Wthread-safety-analysis"
  "-Wthread-safety-attributes"
  "-Wthread-safety-beta"
  "-Wthread-safety-negative"
  "-Wthread-safety-precise"
  "-Wthread-safety-reference"
  "-Wthread-safety-verbose"
  "-Wtype-safety"
  "-Wtypedef-redefinition"
  "-Wtypename-missing"
  "-Wunable-to-open-stats-file"
  "-Wunaligned-access"
  "-Wunaligned-qualifier-implicit-cast"
  "-Wunavailable-declarations"
  "-Wundef-prefix"
  "-Wundefined-bool-conversion"
  "-Wundefined-func-template"
  "-Wundefined-inline"
  "-Wundefined-internal"
  "-Wundefined-internal-type"
  "-Wundefined-reinterpret-cast"
  "-Wundefined-var-template"
  "-Wunderaligned-exception-object"
  "-Wunevaluated-expression"
  "-Wunguarded-availability"
  "-Wunguarded-availability-new"
  "-Wunicode"
  "-Wunicode-homoglyph"
  "-Wunicode-whitespace"
  "-Wunicode-zero-width"
  "-Wuninitialized-const-reference"
  "-Wunknown-argument"
  "-Wunknown-assumption"
  "-Wunknown-attributes"
  "-Wunknown-cuda-version"
  "-Wunknown-directives"
  "-Wunknown-escape-sequence"
  "-Wunknown-sanitizers"
  "-Wno-unknown-warning-option"
  "-Wunnamed-type-template-args"
  "-Wunneeded-internal-declaration"
  "-Wunneeded-member-function"
  "-Wunqualified-std-cast-call"
  "-Wunreachable-code"
  "-Wunreachable-code-aggressive"
  "-Wunreachable-code-break"
  "-Wunreachable-code-fallthrough"
  "-Wunreachable-code-generic-assoc"
  "-Wunreachable-code-loop-increment"
  "-Wunreachable-code-return"
  "-Wunsequenced"
  "-Wunsupported-abi"
  "-Wunsupported-abs"
  "-Wunsupported-availability-guard"
  "-Wunsupported-cb"
  "-Wunsupported-dll-base-class-template"
  "-Wunsupported-floating-point-opt"
  "-Wunsupported-friend"
  "-Wunsupported-gpopt"
  "-Wunsupported-nan"
  "-Wunsupported-target-opt"
  "-Wunsupported-visibility"
  "-Wunusable-partial-specialization"
  "-Wunused-argument"
  "-Wunused-comparison"
  "-Wunused-exception-parameter"
  "-Wunused-getter-return-value"
  "-Wunused-lambda-capture"
  "-Wunused-local-typedef"
  "-Wunused-member-function"
  "-Wunused-private-field"
  "-Wunused-property-ivar"
  "-Wunused-result"
  "-Wunused-template"
  "-Wunused-volatile-lvalue"
  "-Wused-but-marked-unused"
  "-Wuser-defined-literals"
  "-Wuser-defined-warnings"
  "-Wvarargs"
  "-Wvec-elem-size"
  "-Wvector-conversion"
  "-Wvector-conversions"
  "-Wvisibility"
  "-Wvla-extension"
  "-Wvoid-pointer-to-enum-cast"
  "-Wvoid-pointer-to-int-cast"
  "-Wvoid-ptr-dereference"
  "-Wvolatile-register-var"
  "-Wwasm-exception-spec"
  "-Wweak-template-vtables"
  "-Wweak-vtables"
  "-Wwritable-strings"
  "-Wzero-length-array"
  "-Wc++-compat"
  "-Wabi"
# these need special support for the makefile      "-W#pragma-messages"
# these need special support for the makefile      "-W#warnings"
)

SANITIZER_FLAGS=(
    "-fsanitize=address"
    "-fsanitize=pointer-compare"
    "-fsanitize=pointer-subtract"
    "-fsanitize=leak"
    "-fsanitize=undefined"
    "-fsanitize=shift"
    "-fsanitize=shift-exponent"
    "-fsanitize=shift-base"
    "-fsanitize=integer-divide-by-zero"
    "-fsanitize=unreachable"
    "-fsanitize=vla-bound"
    "-fsanitize=null"
    "-fsanitize=signed-integer-overflow"
    "-fsanitize=bounds"
    "-fsanitize=bounds-strict"
    "-fsanitize=alignment"
    "-fsanitize=object-size"
    "-fsanitize=float-divide-by-zero"
    "-fsanitize=float-cast-overflow"
    "-fsanitize=nonnull-attribute"
    "-fsanitize=returns-nonnull-attribute"
    "-fsanitize=bool"
    "-fsanitize=enum"
    "-fsanitize=pointer-overflow"
    "-fsanitize=builtin"
    "-fsanitize-address-use-after-scope"
#        "-fcf-protection=full" # M1 (and I assume M2) Mac does not support this
    "-fharden-compares"
    "-fharden-conditional-branches"
    "-fstack-protector-all"
    "-fstack-clash-protection"
    "-fharden-control-flow-redundancy"
    "-fno-delete-null-pointer-checks"
    "-fno-omit-frame-pointer"
#        "-fsanitize-coverage=trace-pc"
#        "-fsanitize-coverage=trace-cmp"
#        "-finstrument-functions"
)

ANALYZER_FLAGS=(
# this needs to be handled better        "--analyze"
    "--analyzer"
    "-Xanalyzer"
    "-fanalyzer"
    "-fanalyzer-transitivity"
    "-fanalyzer-verbosity=3"
    "-Wno-analyzer-too-complex"
    "-Wno-analyzer-fd-leak"
)

DEBUG_FLAGS=(
    "-g3"
    "-ggdb"
    "-fvar-tracking"
    "-fvar-tracking-assignments"
    "-gcolumn-info"
)

# Remove existing debug_flags.txt, analyzer_flags.txt, warning_flags.txt, and sanitizer_flags.txt files
rm -f ../debug_flags.txt ../analyzer_flags.txt ../warning_flags.txt ../sanitizer_flags.txt

# Call the function to create or overwrite the file
create_or_overwrite_test_c_file

# Loop over DEBUG_FLAGS and compile with each set of flags
for flags in "${DEBUG_FLAGS[@]}"; do
    compile_test_c_file "$flags"
    echo "File 'test.c' compiled with flags: $flags"
done

# Call the function to check if each flag is supported
for flag in "${DEBUG_FLAGS[@]}"; do
    is_flag_supported "$flag"
done

# Write the supported debug flags to debug_flags.txt as one line with spaces
echo "$supported_flags" > ../debug_flags.txt

# Reset the supported_flags variable for analyzer flags
supported_flags=""

# Loop over ANALYZER_FLAGS and compile with each set of flags
for flags in "${ANALYZER_FLAGS[@]}"; do
    compile_test_c_file "$flags"
    echo "File 'test.c' compiled with flags: $flags"
done

# Call the function to check if each analyzer flag is supported
for flag in "${ANALYZER_FLAGS[@]}"; do
    is_flag_supported "$flag"
done

# Write the supported analyzer flags to analyzer_flags.txt as one line with spaces
echo "$supported_flags" > ../analyzer_flags.txt

# Reset the supported_flags variable for warning flags
supported_flags=""

# Loop over WARNING_FLAGS and compile with each set of flags
for flags in "${WARNING_FLAGS[@]}"; do
    compile_test_c_file "$flags"
    echo "File 'test.c' compiled with flags: $flags"
done

# Call the function to check if each warning flag is supported
for flag in "${WARNING_FLAGS[@]}"; do
    is_flag_supported "$flag"
done

# Write the supported warning flags to warning_flags.txt as one line with spaces
echo "$supported_flags" > ../warning_flags.txt

# Reset the supported_flags variable for sanitizer flags
supported_flags=""

# Loop over SANITIZER_FLAGS and compile with each set of flags
for flags in "${SANITIZER_FLAGS[@]}"; do
    compile_test_c_file "$flags"
    echo "File 'test.c' compiled with flags: $flags"
done

# Call the function to check if each sanitizer flag is supported
for flag in "${SANITIZER_FLAGS[@]}"; do
    is_flag_supported "$flag"
done

# Write the supported sanitizer flags to sanitizer_flags.txt as one line with spaces
echo "$supported_flags" > ../sanitizer_flags.txt

# Call the function to compile the file
compile_test_c_file

# Remove the 'test.c' file
rm -f test.c

# List of directories
directories=(
    "lib_error"
    "lib_env"
    "lib_c"
    "lib_posix"
    "lib_posix_xsi"
    "lib_posix_optional"
    "lib_unix"
)

# Loop through the directories
for dir in "${directories[@]}"; do
    # Change to the directory
    pushd "../$dir" || exit

    # Construct the full path to the 'build' directory
    build_directory="build"

    # Check if the 'build' directory exists
    if [ -d "$build_directory" ]; then
        # If it exists, delete it
        rm -r "$build_directory"
        echo "Deleted 'build' directory in $dir."
    fi

    # Create the 'build' directory
    mkdir -p "$build_directory"

    # Run cmake configure with the specified compiler
    cmake -S . -B "$build_directory" -DCMAKE_C_COMPILER="$compiler"

    # Return to the original directory
    popd || exit
done

echo "CMake configuration completed with compiler: $compiler"
