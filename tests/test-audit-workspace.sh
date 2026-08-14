#!/usr/bin/env bash
set -euo pipefail

audit_workspace="${1:-${P101_AUDIT_WORKSPACE:-}}"
fact_bundle="${2:-${P101_SEMANTIC_FACT_BUNDLE:-}}"
control_group="${3:-all}"
scripts_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
workspace="$(CDPATH='' cd -- "$scripts_root/.." && pwd -P)"
# Keep the fixture at the same directory depth as scripts/. Repository paths
# in repos.txt are deliberately relative (../libraries, ../programs, ...), so a
# generic /tmp fixture would test a different path contract and fail before it
# reached the policy under test.
temporary_root="$(mktemp -d "$workspace/.p101-audit-workspace.XXXXXX")"
trap 'rm -rf "$temporary_root"' EXIT

if [ -z "$audit_workspace" ] || [ ! -x "$audit_workspace" ]; then
    printf 'audit-workspace negative controls: executable is absent: %s\n' "$audit_workspace" >&2
    exit 2
fi
case "$control_group" in
    all|architecture|fault) ;;
    *)
        printf 'audit-workspace negative controls: unknown group: %s\n' "$control_group" >&2
        exit 2
        ;;
esac

mkdir -p "$temporary_root/contracts"
cp "$scripts_root/repos.txt" "$temporary_root/repos.txt"
cp "$scripts_root/contracts/wrapper-fault-semantics.json" "$temporary_root/contracts/wrapper-fault-semantics.json"
cp "$scripts_root/contracts/wrapper-failure-contract.json" "$temporary_root/contracts/wrapper-failure-contract.json"
cp "$scripts_root/contracts/wrapper-lifecycle-contract.json" "$temporary_root/contracts/wrapper-lifecycle-contract.json"
cp "$scripts_root/contracts/wrapper-library-map.tsv" "$temporary_root/contracts/wrapper-library-map.tsv"
cp "$scripts_root/contracts/native-wrapper-parity.tsv" "$temporary_root/contracts/native-wrapper-parity.tsv"
cp "$scripts_root/contracts/p101-source-responsibilities.json" "$temporary_root/contracts/p101-source-responsibilities.json"
cp "$scripts_root/contracts/p101-boundaries.json" "$temporary_root/contracts/p101-boundaries.json"
cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"
cp "$scripts_root/contracts/p101-check-graph.json" "$temporary_root/contracts/p101-check-graph.json"
cp "$scripts_root/contracts/instrumentation-contract.json" "$temporary_root/contracts/instrumentation-contract.json"

inventory_root="$temporary_root/inventory"
mkdir -p "$inventory_root/contracts" \
    "$inventory_root/checks" \
    "$inventory_root/workspace" \
    "$inventory_root/tests" \
    "$inventory_root/repositories/sample"
printf '%s\n' \
    '{' \
    '  "schema": "p101-test-inventory-v1",' \
    '  "repository_manifest": "repos.txt",' \
    '  "entry_points": {' \
    '    "check.sh": {"owner":"repository","oracle":"build","runner":"update-all.sh"},' \
    '    "test.sh": {"owner":"repository","oracle":"test","runner":"checks/check-repository-tests.sh"},' \
    '    "fuzz.sh": {"owner":"repository","oracle":"fuzz","runner":"checks/check-repository-tests.sh"}' \
    '  },' \
    '  "standalone_verification_exclusions": [' \
    '    {"path":"check-after-update-all.sh","owner":"scripts","oracle":"graph","reason":"compatibility entry point"}' \
    '  ],' \
    '  "does_not_prove": "The fixture validates the inventory mechanism, not its completeness."' \
    '}' >"$temporary_root/inventory.valid.json"
cp "$temporary_root/inventory.valid.json" "$inventory_root/contracts/p101-test-inventory.json"
printf '%s\n' \
    '{"nodes":[' \
    '  {"command":["./checks/check-repository-tests.sh"]}' \
    ']}' >"$inventory_root/contracts/p101-check-graph.json"
printf 'sample|repositories/sample|c\n' >"$inventory_root/repos.txt"
printf '#!/usr/bin/env sh\nexit 0\n' >"$inventory_root/update-all.sh"
printf '#!/usr/bin/env sh\nexit 0\n' >"$inventory_root/check-after-update-all.sh"
printf '#!/usr/bin/env sh\nexit 0\n' >"$inventory_root/checks/check-repository-tests.sh"
printf '#!/usr/bin/env sh\nexit 0\n' >"$inventory_root/checks/p101-check-graph.py"
printf '#!/usr/bin/env sh\nexit 0\n' >"$inventory_root/repositories/sample/check.sh"
printf '#!/usr/bin/env sh\nexit 0\n' >"$inventory_root/repositories/sample/test.sh"
printf '#!/usr/bin/env sh\nexit 0\n' >"$inventory_root/repositories/sample/fuzz.sh"
chmod +x "$inventory_root/update-all.sh" \
    "$inventory_root/check-after-update-all.sh" \
    "$inventory_root/checks/check-repository-tests.sh" \
    "$inventory_root/checks/p101-check-graph.py" \
    "$inventory_root/repositories/sample/check.sh" \
    "$inventory_root/repositories/sample/test.sh" \
    "$inventory_root/repositories/sample/fuzz.sh"

check_repository_order()
{
    local cmake_probe="$temporary_root/read-config.cmake"
    local configs="$temporary_root/repository-configs.tsv"
    local positions="$temporary_root/repository-positions.tsv"
    local targets="$temporary_root/repository-targets.tsv"
    local dependencies="$temporary_root/repository-dependencies.tsv"
    local row
    local repository
    local config
    local index=0

    printf '%s\n' \
        'function(p101_inspect_config repository config)' \
        '  include("${config}")' \
        '  foreach(target IN LISTS LIBRARY_TARGETS)' \
        '    message("P101_TARGET=${target}|${repository}")' \
        '  endforeach()' \
        '  get_cmake_property(names VARIABLES)' \
        '  foreach(name IN LISTS names)' \
        '    if(name MATCHES "_LINK_LIBRARIES$")' \
        '      foreach(dependency IN LISTS ${name})' \
        '        message("P101_DEPENDENCY=${repository}|${dependency}")' \
        '      endforeach()' \
        '    endif()' \
        '  endforeach()' \
        'endfunction()' \
        'file(STRINGS "${CONFIGS}" rows)' \
        'foreach(row IN LISTS rows)' \
        '  string(REPLACE "|" ";" fields "${row}")' \
        '  list(GET fields 0 repository)' \
        '  list(GET fields 1 config)' \
        '  p101_inspect_config("${repository}" "${config}")' \
        'endforeach()' >"$cmake_probe"
    : >"$configs"
    : >"$positions"
    : >"$targets"
    : >"$dependencies"
    while IFS='|' read -r _ repository _ || [ -n "$repository" ]; do
        case "$repository" in
            ''|'#'*) continue ;;
        esac
        index=$((index + 1))
        repository="$(CDPATH='' cd -- "$scripts_root/$repository" && pwd -P)"
        printf '%s\t%s\n' "$repository" "$index" >>"$positions"
        config="$repository/config.cmake"
        [ -f "$config" ] || continue
        printf '%s|%s\n' "$repository" "$config" >>"$configs"
    done <"$scripts_root/repos.txt"

    while IFS= read -r row; do
        case "$row" in
            P101_TARGET=*)
                row="${row#P101_TARGET=}"
                printf '%s\t%s\n' "${row%%|*}" "${row#*|}" >>"$targets"
                ;;
            P101_DEPENDENCY=*)
                row="${row#P101_DEPENDENCY=}"
                printf '%s\t%s\n' "${row%%|*}" "${row#*|}" >>"$dependencies"
                ;;
        esac
    done < <(cmake -DCONFIGS="$configs" -P "$cmake_probe" 2>&1)

    awk -F '\t' '
        FILENAME == ARGV[1] { position[$1] = $2; next }
        FILENAME == ARGV[2] { owner[$1] = $2; next }
        {
            repository = $1
            dependency = $2
            if((dependency in owner) && position[owner[dependency]] > position[repository]) {
                printf "FAIL: %s precedes dependency owner %s (%s)\n", repository, owner[dependency], dependency > "/dev/stderr"
                failed = 1
            }
        }
        END { exit failed }
    ' "$positions" "$targets" "$dependencies"
}

run_policy()
{
    local policy="$1"
    if [[ "$policy" == native-wrapper-parity || "$policy" == boundaries || "$policy" == quality-contract || "$policy" == wrapper-unit-tests || "$policy" == instrumentation ]] && [ -n "$fact_bundle" ] && [ -f "$fact_bundle" ]; then
        "$audit_workspace" \
            --policy "$policy" \
            --workspace "$workspace" \
            --scripts-root "$temporary_root" \
            --facts "$fact_bundle" \
            -d:human
    else
        "$audit_workspace" \
            --policy "$policy" \
            --workspace "$workspace" \
            --scripts-root "$temporary_root" \
            -d:human
    fi
}

expect_failure()
{
    local label="$1"
    local policy="$2"
    if run_policy "$policy" >"$temporary_root/$label.out" 2>&1; then
        printf 'FAIL: %s drift was accepted\n' "$label" >&2
        exit 1
    fi
}

expect_json_failure()
{
    local label="$1"
    local policy="$2"
    if "$audit_workspace" \
        --policy "$policy" \
        --workspace "$workspace" \
        --scripts-root "$temporary_root" \
        -d:json >"$temporary_root/$label.json" 2>"$temporary_root/$label.err"; then
        printf 'FAIL: %s drift was accepted\n' "$label" >&2
        exit 1
    fi
    grep -Fq '"schema":"p101-tool-diagnostic-v1"' \
        "$temporary_root/$label.json"
    grep -Fq '"severity":"error"' "$temporary_root/$label.json"
    grep -Fq '"message":' "$temporary_root/$label.json"
}

run_inventory()
{
    "$audit_workspace" \
        --policy test-inventory \
        --workspace "$workspace" \
        --scripts-root "$inventory_root" \
        -d:human
}

run_source_responsibilities()
{
    if [ -z "$fact_bundle" ] || [ ! -f "$fact_bundle" ]; then
        printf 'audit-workspace negative controls: semantic fact bundle is absent: %s\n' "$fact_bundle" >&2
        return 2
    fi
    "$audit_workspace" \
        --policy source-responsibilities \
        --workspace .. \
        --scripts-root "$temporary_root" \
        --facts "$fact_bundle" \
        -d:human
}

run_architecture_controls()
{
    check_repository_order
    run_policy functional-library-split >/dev/null
    run_policy native-wrapper-parity >/dev/null
    run_policy boundaries >/dev/null
    run_policy quality-contract >/dev/null
    run_policy wrapper-unit-tests >/dev/null
    run_policy instrumentation >/dev/null
    run_inventory >/dev/null
    run_source_responsibilities >/dev/null

    sed -E 's/"maximum_lines": [0-9]+/"maximum_lines": 1/' \
        "$scripts_root/contracts/p101-source-responsibilities.json" \
        >"$temporary_root/contracts/p101-source-responsibilities.json"
    if run_source_responsibilities >"$temporary_root/facade-growth.out" 2>&1; then
        printf 'FAIL: facade growth was accepted\n' >&2
        exit 1
    fi
    cp "$scripts_root/contracts/p101-source-responsibilities.json" "$temporary_root/contracts/p101-source-responsibilities.json"

    sed 's/"c:@F@system", /"c:@F@p101_tool_run_capture", /' \
        "$scripts_root/contracts/p101-source-responsibilities.json" \
        >"$temporary_root/contracts/p101-source-responsibilities.json"
    if run_source_responsibilities >"$temporary_root/owner-bypass.out" 2>&1; then
        printf 'FAIL: owner bypass was accepted\n' >&2
        exit 1
    fi
    cp "$scripts_root/contracts/p101-source-responsibilities.json" "$temporary_root/contracts/p101-source-responsibilities.json"

    sed 's#"path":"check-after-update-all.sh"#"path":"missing-check.sh"#' \
        "$temporary_root/inventory.valid.json" \
        >"$inventory_root/contracts/p101-test-inventory.json"
    if run_inventory >"$temporary_root/stale-inventory.out" 2>&1; then
        printf 'FAIL: stale test-inventory exclusion was accepted\n' >&2
        exit 1
    fi
    cp "$temporary_root/inventory.valid.json" "$inventory_root/contracts/p101-test-inventory.json"

    sed '\#../libraries/lib_host|#d' "$scripts_root/repos.txt" >"$temporary_root/repos.txt"
    expect_failure functional-layout functional-library-split
    cp "$scripts_root/repos.txt" "$temporary_root/repos.txt"

    sed '2s#libraries/lib_cli/src/unistd.c#libraries/lib_cli/src/posix/unistd.c#' \
        "$scripts_root/contracts/wrapper-library-map.tsv" \
        >"$temporary_root/contracts/wrapper-library-map.tsv"
    expect_failure source-layout functional-library-split
    cp "$scripts_root/contracts/wrapper-library-map.tsv" "$temporary_root/contracts/wrapper-library-map.tsv"

    sed '2s#libraries/lib_cli/include/p101_cli/p101_unistd.h#libraries/lib_cli/include/p101_cli/wrong.h#' \
        "$scripts_root/contracts/wrapper-library-map.tsv" \
        >"$temporary_root/contracts/wrapper-library-map.tsv"
    expect_failure header-layout functional-library-split
    cp "$scripts_root/contracts/wrapper-library-map.tsv" "$temporary_root/contracts/wrapper-library-map.tsv"

    printf 'p101_missing\tc:@F@p101_missing\tcli\tPOSIX\tlibraries/lib_cli/src/unistd.c\tlibraries/lib_cli/include/p101_cli/p101_unistd.h\told.c\told.h\n' \
        >>"$temporary_root/contracts/wrapper-library-map.tsv"
    expect_failure central-only functional-library-split
    cp "$scripts_root/contracts/wrapper-library-map.tsv" "$temporary_root/contracts/wrapper-library-map.tsv"

    sed -n '2p' "$scripts_root/contracts/native-wrapper-parity.tsv" >>"$temporary_root/contracts/native-wrapper-parity.tsv"
    expect_failure native-parity native-wrapper-parity
    cp "$scripts_root/contracts/native-wrapper-parity.tsv" "$temporary_root/contracts/native-wrapper-parity.tsv"

    awk '!changed && /"owner_usr":/ { sub(/"owner_usr": "[^"]*"/, "\"owner_usr\": \"c:@F@p101_missing_boundary_owner\""); changed = 1 } { print }' \
        "$scripts_root/contracts/p101-boundaries.json" \
        >"$temporary_root/contracts/p101-boundaries.json"
    expect_failure boundary-owner boundaries
    cp "$scripts_root/contracts/p101-boundaries.json" "$temporary_root/contracts/p101-boundaries.json"

    awk '!changed && /"evidence_usr":/ { sub(/"evidence_usr": "[^"]*"/, "\"evidence_usr\": \"c:@F@p101_missing_boundary_evidence\""); changed = 1 } { print }' \
        "$scripts_root/contracts/p101-boundaries.json" \
        >"$temporary_root/contracts/p101-boundaries.json"
    expect_failure boundary-evidence-identity boundaries
    cp "$scripts_root/contracts/p101-boundaries.json" "$temporary_root/contracts/p101-boundaries.json"

    awk '
        /"owner_source":/ { source_count++ }
        /"owner_usr":/ { usr_count++ }
        source_count == 2 && !source_changed {
            sub(/"owner_source": "[^"]*"/, "\"owner_source\": \"libraries/lib_c_facts/include/p101_c_facts/compile_command.h\"")
            source_changed = 1
        }
        usr_count == 2 && !usr_changed {
            sub(/"owner_usr": "[^"]*"/, "\"owner_usr\": \"c:@F@p101_c_facts_with_compile_command\"")
            usr_changed = 1
        }
        { print }
    ' "$scripts_root/contracts/p101-boundaries.json" \
        >"$temporary_root/contracts/p101-boundaries.json"
    expect_failure boundary-duplicate-owner boundaries
    cp "$scripts_root/contracts/p101-boundaries.json" "$temporary_root/contracts/p101-boundaries.json"

    sed 's/p101:boundary-case:boundary:c-fact-analysis:identity_mismatch/p101:boundary-case:boundary:c-fact-analysis:binding_swap/' \
        "$scripts_root/contracts/p101-boundaries.json" \
        >"$temporary_root/contracts/p101-boundaries.json"
    expect_failure boundary-reused-evidence boundaries
    cp "$scripts_root/contracts/p101-boundaries.json" "$temporary_root/contracts/p101-boundaries.json"

    awk '!changed && /"resource_limit":/ { sub(/"resource_limit":/, "\"missing_resource_limit\":"); changed = 1 } { print }' \
        "$scripts_root/contracts/p101-boundaries.json" \
        >"$temporary_root/contracts/p101-boundaries.json"
    expect_failure boundary-missing-case boundaries
    cp "$scripts_root/contracts/p101-boundaries.json" "$temporary_root/contracts/p101-boundaries.json"

    awk '!changed && /"does_not_prove":/ { sub(/: "[^"]*"/, ": \"\""); changed = 1 } { print }' \
        "$scripts_root/contracts/p101-boundaries.json" \
        >"$temporary_root/contracts/p101-boundaries.json"
    expect_failure boundary-empty-limitation boundaries
    cp "$scripts_root/contracts/p101-boundaries.json" "$temporary_root/contracts/p101-boundaries.json"

    sed 's/p101-quality-contract-v3/p101-quality-contract-v2/' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-schema quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    awk '!changed && /"mode": "local"/ { sub(/"local"/, "\"ambient\""); changed = 1 } { print }' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-audit-mode quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    awk '!changed && /"oracle":/ { sub(/"oracle": "[^"]*"/, "\"oracle\": \"missing-quality-oracle\""); changed = 1 } { print }' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-oracle quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    sed 's/"P101_ERROR_NONE"/"P101_ERROR_NOT_A_VARIANT"/' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-enum-variant quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    sed 's/c:@E@p101_c_analysis_kind/c:@E@p101_missing_analysis_kind/' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-enum-classification quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    sed 's/boundary:c-fact-analysis/boundary:missing-c-fact-analysis/' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-boundary quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    sed 's/"allowed_caller_usr": "c:@F@main"/"allowed_caller_usr": "c:@F@worker"/' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-termination-owner quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    sed 's/p101:test:negative-control:process-termination//g' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-empty-termination-role quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    sed 's/"freebsd"/"unsupported-platform"/' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-platform quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    awk '!changed && /"does_not_prove":/ { sub(/: "[^"]*"/, ": \"\""); changed = 1 } { print }' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-empty-limitation quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    awk '
        !changed && /"patterns": \[/ { in_patterns = 1; print; next }
        in_patterns && /]/ { in_patterns = 0; changed = 1; print; next }
        in_patterns { sub(/"[^"]*"/, "\"P101_NOT_A_DOCUMENTED_CONCEPT\"") }
        { print }
    ' \
        "$scripts_root/contracts/p101-quality-contract.json" \
        >"$temporary_root/contracts/p101-quality-contract.json"
    expect_failure quality-documentation quality-contract
    cp "$scripts_root/contracts/p101-quality-contract.json" "$temporary_root/contracts/p101-quality-contract.json"

    awk '!changed && /"lib_cli": "native-wrapper"/ { sub(/"lib_cli": "native-wrapper"/, "\"lib_missing\": \"native-wrapper\""); changed = 1 } { print }' \
        "$scripts_root/contracts/instrumentation-contract.json" \
        >"$temporary_root/contracts/instrumentation-contract.json"
    expect_failure unit-library wrapper-unit-tests
    cp "$scripts_root/contracts/instrumentation-contract.json" "$temporary_root/contracts/instrumentation-contract.json"

    awk '!changed && /"fault"/ { sub(/"fault"/, "\"unknown-capability\""); changed = 1 } { print }' \
        "$scripts_root/contracts/instrumentation-contract.json" \
        >"$temporary_root/contracts/instrumentation-contract.json"
    expect_failure instrumentation-capability instrumentation
    cp "$scripts_root/contracts/instrumentation-contract.json" "$temporary_root/contracts/instrumentation-contract.json"

    awk -F '\t' 'BEGIN { OFS = FS } NR == 2 { $6 = $5 } { print }' \
        "$scripts_root/contracts/native-wrapper-parity.tsv" \
        >"$temporary_root/contracts/native-wrapper-parity.tsv"
    expect_failure native-identity native-wrapper-parity
    cp "$scripts_root/contracts/native-wrapper-parity.tsv" "$temporary_root/contracts/native-wrapper-parity.tsv"

    awk -F '\t' 'BEGIN { OFS = FS } NR == 2 { $6 = "" } { print }' \
        "$scripts_root/contracts/native-wrapper-parity.tsv" \
        >"$temporary_root/contracts/native-wrapper-parity.tsv"
    expect_failure native-incomplete native-wrapper-parity
}

run_fault_controls()
{
    run_policy wrapper-fault-semantics >/dev/null

    sed 's/"after-dispatch"/"before-call"/' \
        "$scripts_root/contracts/wrapper-fault-semantics.json" \
        >"$temporary_root/contracts/wrapper-fault-semantics.json"
    expect_json_failure phase wrapper-fault-semantics

    sed 's/"automatic-retry-forbidden"/"retry-always"/' \
        "$scripts_root/contracts/wrapper-fault-semantics.json" \
        >"$temporary_root/contracts/wrapper-fault-semantics.json"
    expect_failure retry wrapper-fault-semantics

    sed 's/p101-wrapper-fault-semantics-v3/p101-wrapper-fault-semantics-v2/' \
        "$scripts_root/contracts/wrapper-fault-semantics.json" \
        >"$temporary_root/contracts/wrapper-fault-semantics.json"
    expect_failure schema wrapper-fault-semantics
}

if [[ "$control_group" == all || "$control_group" == architecture ]]; then
    run_architecture_controls
fi
if [[ "$control_group" == all || "$control_group" == fault ]]; then
    run_fault_controls
fi

printf 'audit-workspace %s negative controls passed\n' "$control_group"
