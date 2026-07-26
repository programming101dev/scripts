cmake_minimum_required(VERSION 3.20)

# Tool policy defaults. Project config.cmake may define any of these before the
# shared CMakeLists includes this file.

if (NOT DEFINED P101_TIDY_DEFAULT_CHECKS OR "${P101_TIDY_DEFAULT_CHECKS}" STREQUAL "")
    set(P101_TIDY_DEFAULT_CHECKS
            "*"
            "-llvmlibc-*"
            "-clang-diagnostic-unused-macros"
            "-llvmlibc-restrict-system-libc-headers"
            "-altera-struct-pack-align"
            "-readability-identifier-length"
            "-altera-unroll-loops"
            "-cppcoreguidelines-init-variables"
            "-cert-err33-c"
            "-bugprone-easily-swappable-parameters"
            "-clang-analyzer-security.insecureAPI.DeprecatedOrUnsafeBufferHandling"
            "-altera-id-dependent-backward-branch"
            "-concurrency-mt-unsafe"
            "-misc-unused-parameters"
            "-hicpp-signed-bitwise"
            "-google-readability-todo"
            "-cert-msc30-c"
            "-readability-function-cognitive-complexity"
            "-clang-analyzer-security.insecureAPI.strcpy"
            "-cert-env33-c"
            "-android-cloexec-accept"
            "-misc-include-cleaner"
            "-llvm-header-guard"
            "-google-readability-casting"
            "-readability-redundant-casting"
            "-fuchsia-trailing-return"
    )
endif ()

if (NOT DEFINED P101_TIDY_EXTRA_CHECKS OR "${P101_TIDY_EXTRA_CHECKS}" STREQUAL "")
    set(P101_TIDY_EXTRA_CHECKS)
endif ()

if (NOT DEFINED P101_TIDY_WARNINGS_AS_ERRORS OR "${P101_TIDY_WARNINGS_AS_ERRORS}" STREQUAL "")
    set(P101_TIDY_WARNINGS_AS_ERRORS "*")
endif ()

if (NOT DEFINED P101_CPPCHECK_EXHAUSTIVE_ARG OR "${P101_CPPCHECK_EXHAUSTIVE_ARG}" STREQUAL "")
    set(P101_CPPCHECK_EXHAUSTIVE_ARG --check-level=exhaustive)
endif ()

if (NOT DEFINED P101_CPPCHECK_BASE_ARGS OR "${P101_CPPCHECK_BASE_ARGS}" STREQUAL "")
    set(P101_CPPCHECK_BASE_ARGS
            --enable=all
            --inconclusive
            ${P101_CPPCHECK_EXHAUSTIVE_ARG}
            --library=posix
            --force
            --inline-suppr
            --quiet
    )
endif ()

if (NOT DEFINED P101_CPPCHECK_SUPPRESSIONS OR "${P101_CPPCHECK_SUPPRESSIONS}" STREQUAL "")
    set(P101_CPPCHECK_SUPPRESSIONS
            missingIncludeSystem
            unusedFunction
            staticFunction
            constParameterPointer
            unmatchedSuppression
            checkersReport
    )
endif ()

if (NOT DEFINED P101_CLANG_SA_PROFILE OR "${P101_CLANG_SA_PROFILE}" STREQUAL "")
    set(P101_CLANG_SA_PROFILE "deep")
endif ()

if (NOT DEFINED P101_CLANG_SA_DISABLE_CHECKERS OR "${P101_CLANG_SA_DISABLE_CHECKERS}" STREQUAL "")
    set(P101_CLANG_SA_DISABLE_CHECKERS
            alpha.core.Conversion
            security.insecureAPI.DeprecatedOrUnsafeBufferHandling
            security.insecureAPI.strcpy
    )
endif ()

if (NOT DEFINED P101_CLANG_SA_COMMON_ARGS OR "${P101_CLANG_SA_COMMON_ARGS}" STREQUAL "")
    set(P101_CLANG_SA_COMMON_ARGS
            --analyze
            -Xanalyzer -analyzer-output=text
    )
endif ()

if (NOT DEFINED P101_CLANG_SA_CTU_COMMON_ARGS OR "${P101_CLANG_SA_CTU_COMMON_ARGS}" STREQUAL "")
    set(P101_CLANG_SA_CTU_COMMON_ARGS
            -Xanalyzer -analyzer-output=text
    )
endif ()

if (NOT DEFINED P101_CLANG_SA_PROFILE_ARGS OR "${P101_CLANG_SA_PROFILE_ARGS}" STREQUAL "")
    set(P101_CLANG_SA_PROFILE_ARGS
            -Xanalyzer -analyzer-config -Xanalyzer analyze-headers=false
            -Xanalyzer -analyzer-config -Xanalyzer report-in-main-source-file=true
    )
endif ()

if (NOT DEFINED P101_CLANG_SA_BASE_CHECKERS OR "${P101_CLANG_SA_BASE_CHECKERS}" STREQUAL "")
    set(P101_CLANG_SA_BASE_CHECKERS core unix security nullability)
endif ()

if (NOT DEFINED P101_CLANG_SA_APPLE_CHECKERS OR "${P101_CLANG_SA_APPLE_CHECKERS}" STREQUAL "")
    set(P101_CLANG_SA_APPLE_CHECKERS osx)
endif ()

if (NOT DEFINED P101_CLANG_SA_NON_APPLE_CHECKERS OR "${P101_CLANG_SA_NON_APPLE_CHECKERS}" STREQUAL "")
    set(P101_CLANG_SA_NON_APPLE_CHECKERS deadcode optin apiModeling)
endif ()

if (NOT DEFINED P101_CLANG_SA_CXX_CHECKERS OR "${P101_CLANG_SA_CXX_CHECKERS}" STREQUAL "")
    set(P101_CLANG_SA_CXX_CHECKERS cplusplus)
endif ()

if (NOT DEFINED P101_CLANG_SA_DEEP_CHECKERS OR "${P101_CLANG_SA_DEEP_CHECKERS}" STREQUAL "")
    set(P101_CLANG_SA_DEEP_CHECKERS alpha.core alpha.security alpha.unix)
endif ()

if (NOT DEFINED P101_CLANG_SA_DEEP_ARGS OR "${P101_CLANG_SA_DEEP_ARGS}" STREQUAL "")
    set(P101_CLANG_SA_DEEP_ARGS
            -Xanalyzer -analyzer-config -Xanalyzer aggressive-binary-operation-simplification=true
            -Xanalyzer -analyzer-config -Xanalyzer unroll-loops=true
    )
endif ()
