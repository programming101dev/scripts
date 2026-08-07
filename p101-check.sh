#!/usr/bin/env bash
# Run p101 checks one at a time. Generated from scripts/contracts/p101-check-graph.json
#
#   ./p101-check list            # numbered node list
#   ./p101-check 52              # run node 52
#   ./p101-check 13 14           # run several nodes in the order given
#   ./p101-check library-audit   # run by id
#   ./p101-check show 52         # print the command without running it
#   ./p101-check all             # run every node in dependency order
#   STOP=1 ./p101-check all      # ...halting at the first failure
#
# Env (all optional): OUT CC CXX FMT FUZZ FAULTS
set -uo pipefail

SCRIPTS="${P101_SCRIPTS:-$HOME/work/programming101dev/scripts}"
if [ ! -d "$SCRIPTS" ]; then
    echo "p101-check: scripts dir not found: $SCRIPTS (set P101_SCRIPTS)" >&2
    exit 2
fi
GRAPH="$SCRIPTS/contracts/p101-check-graph.json"

: "${OUT:=/tmp/p101-one}"
: "${CC:=$(command -v clang || echo /usr/bin/cc)}"
: "${CXX:=$(command -v clang++ || echo /usr/bin/c++)}"
: "${FMT:=clang-format}"
: "${FUZZ:=5}"
: "${FAULTS:=1}"
mkdir -p "$OUT"

emit() { # emit <selector|""> <mode: list|show|run>
python3 - "$GRAPH" "$1" "$2" "$OUT" "$CC" "$CXX" "$FMT" "$FUZZ" "$FAULTS" <<'PY'
import json, sys, shlex
graph, sel, mode, out, cc, cxx, fmt, fuzz, faults = sys.argv[1:10]
nodes = json.load(open(graph))["nodes"]
subs = {"{out}": out, "{cc}": cc, "{cxx}": cxx, "{formatter}": fmt,
        "{fuzz_secs}": fuzz, "{fault_count}": faults,
        "{template_no_tests}": "", "{playground_quality}": "",
        "{playground_coverage}": "", "{playground_fuzz}": ""}

def command(n):
    parts = []
    for a in n["command"]:
        for k, v in subs.items():
            a = a.replace(k, v)
        if a:
            parts.append(a)
    return parts

if mode == "list":
    try:
        for i, n in enumerate(nodes, 1):
            tag = "" if n.get("required") else "  (optional)"
            print(f"{i:2d}  {n['id']:<34} {n.get('group','')}{tag}")
    except BrokenPipeError:
        pass
    sys.exit(0)

chosen = None
for i, n in enumerate(nodes, 1):
    if sel == str(i) or sel == n["id"]:
        chosen = n
        break
if chosen is None:
    print(f"p101-check: no node matching {sel!r}; try: p101-check list", file=sys.stderr)
    sys.exit(2)

g = " ".join(chosen.get("guarantee", "").split())
print(f"# {chosen['id']}  [{chosen.get('group','')}]", file=sys.stderr)
if g:
    print(f"# {g}", file=sys.stderr)
print(" ".join(shlex.quote(a) for a in command(chosen)))
PY
}

ids() { emit "" list | awk '{print $2}'; }

case "${1:-list}" in
    list) emit "" list ;;
    all)
        : "${STOP:=0}"
        passed=0; failed=0; failed_ids=""
        timings="$OUT/timings.tsv"
        printf 'seconds\tstatus\tnode\n' > "$timings"
        start_all=$(date +%s)
        for id in $(ids); do
            printf '\n=== %s\n' "$id"
            t0=$(date +%s)
            if "$0" "$id"; then
                secs=$(( $(date +%s) - t0 ))
                passed=$((passed + 1)); printf 'PASS %s (%ss)\n' "$id" "$secs"
                printf '%s\tPASS\t%s\n' "$secs" "$id" >> "$timings"
            else
                secs=$(( $(date +%s) - t0 ))
                failed=$((failed + 1)); failed_ids="$failed_ids $id"
                printf 'FAIL %s (%ss)\n' "$id" "$secs"
                printf '%s\tFAIL\t%s\n' "$secs" "$id" >> "$timings"
                if [ "$STOP" != "0" ]; then
                    printf '\nhalted at %s\n' "$id"
                    break
                fi
            fi
        done
        total=$(( $(date +%s) - start_all ))
        printf '\n%d passed, %d failed in %ss\n' "$passed" "$failed" "$total"
        printf '\nslowest nodes (of %ss total):\n' "$total"
        tail -n +2 "$timings" | sort -rn | head -15 | awk -v t="$total" \
            '{pct = t > 0 ? ($1 * 100 / t) : 0; printf "  %6ss  %5.1f%%  %s  %s\n", $1, pct, $2, $3}'
        printf 'full table: %s\n' "$timings"
        if [ -n "$failed_ids" ]; then
            printf 'failed:%s\n' "$failed_ids"
            exit 1
        fi
        ;;
    show) emit "${2:?usage: p101-check show <n|id>}" show ;;
    *)
        if [ "$#" -gt 1 ]; then
            failed_ids=""
            for sel in "$@"; do
                printf '\n=== %s\n' "$sel"
                t0=$(date +%s)
                if "$0" "$sel"; then
                    printf 'PASS %s (%ss)\n' "$sel" "$(( $(date +%s) - t0 ))"
                else
                    failed_ids="$failed_ids $sel"
                    printf 'FAIL %s (%ss)\n' "$sel" "$(( $(date +%s) - t0 ))"
                fi
            done
            if [ -n "$failed_ids" ]; then
                printf '\nfailed:%s\n' "$failed_ids"
                exit 1
            fi
            exit 0
        fi
        cmd="$(emit "$1" run)" || exit 2
        [ -n "$cmd" ] || exit 2
        echo "\$ $cmd" >&2
        cd "$SCRIPTS" || exit 2
        eval "$cmd"
        ;;
esac

