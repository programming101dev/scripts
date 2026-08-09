#!/bin/sh
set -eu

output=
script_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
# shellcheck source=shared/artifacts.sh
. "$script_root/shared/artifacts.sh"

usage()
{
    printf 'Usage: %s -o <output-dir>\n' "$0" >&2
}

find_tool()
{
    environment_name=$1
    shift
    configured=$(printenv "$environment_name" 2>/dev/null || true)

    if [ -n "$configured" ] &&
        { [ -x "$configured" ] || command -v "$configured" >/dev/null 2>&1; }
    then
        printf '%s\n' "$configured"
        return 0
    fi

    for candidate
    do
        if [ -n "$candidate" ] && [ -x "$candidate" ]
        then
            printf '%s\n' "$candidate"
            return 0
        fi
        if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1
        then
            command -v "$candidate"
            return 0
        fi
    done

    return 1
}

last_build_tool()
{
    p101_find_built_tool "$1" "$2"
}

while getopts "o:" option
do
    case "$option" in
        o) output=$OPTARG ;;
        *) usage; exit 2 ;;
    esac
done
shift $((OPTIND - 1))

if [ -z "$output" ] || [ "$#" -ne 0 ]
then
    usage
    exit 2
fi

observe_repository=$script_root/../programs/p101-inspect
event_repository=$script_root/../libraries/lib_tool_event
observe=$(find_tool P101_INSPECT_CAPTURE \
    "$(last_build_tool "$observe_repository" inspect-capture)" \
    "$observe_repository/build-clang-22/inspect-capture" \
    "$observe_repository/build-clang/inspect-capture" \
    inspect-capture || true)
verifier=$(find_tool P101_TOOL_RECEIPT \
    "$(last_build_tool "$event_repository" p101-tool-receipt)" \
    "$event_repository/build-clang-22/p101-tool-receipt" \
    "$event_repository/build-clang/p101-tool-receipt" \
    p101-tool-receipt || true)
if [ -z "$observe" ] || [ -z "$verifier" ]
then
    printf 'Required tools are unavailable: inspect-capture=%s p101-tool-receipt=%s\n' \
        "${observe:-missing}" "${verifier:-missing}" >&2
    exit 2
fi

mkdir -p "$output"
"$observe" -o "$output/capture" -- /usr/bin/true
"$verifier" verify "$output/capture/tool-receipt.json"

cp "$output/capture/tool-receipt.json" "$output/tampered-receipt.json"
sed 's/"inspect-capture"/"inspect-Capture"/' \
    "$output/tampered-receipt.json" >"$output/tampered-receipt.next"
mv "$output/tampered-receipt.next" "$output/tampered-receipt.json"
if "$verifier" verify "$output/tampered-receipt.json" \
    >"$output/tampered-verification.txt" 2>&1
then
    printf 'Tampered tool receipt unexpectedly passed verification.\n' >&2
    exit 1
fi
grep -q 'bad-digest' "$output/tampered-verification.txt"

printf 'p101 tool receipt contract passed: %s\n' \
    "$output/capture/tool-receipt.json"
