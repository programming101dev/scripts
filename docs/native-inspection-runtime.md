# Native inspection runtime boundary

The capture-analysis runtime is native C. `lib_tool_event` owns reusable event
parsing, causal-model construction, policy mechanics, fingerprints, receipts,
and bounded schedule exploration. `p101-inspect` owns commands, exit-status
policy, report selection, rule-pack judgment, and student-facing diagnostics.
`inspect-capture` remains the deliberately small subprocess boundary.

This replaces the former Python runtime modules for capture orchestration,
analysis, model verification, report viewing, receipts, and interleaving walks.
Low-level native helpers such as `p101-event-model` remain available because
they expose a useful library boundary; they are not separate policy engines.

## Tradeoff

Keeping one native process avoids repeated JSON/TSV parsing and removes Python
startup and import cost from every analysis phase. It also makes the same
model instance available to all policy views. The cost is a larger
`p101-inspect` command and stricter memory/error-path ownership in C. Shared
mechanics therefore stay in the tested library, while the command remains a
thin policy coordinator. JSON rule packs remain the source of truth and
generate the checked-in native rule catalog; builds do not need Python at run
time.

## Contract

Admitted inputs are protocol-v5 call/resource streams, capture receipts,
sanitizer text, expectation files, and the generated built-in rule catalog.
Outputs are deterministic text and JSON reports, a causal model, finding
index, immutable input snapshots, fingerprints, receipts, and exit status 0,
1, or 2. Human and JSON views use the shared `lib_tool_event` output contract.

The runtime cannot observe direct libc calls, third-party internals, missing
wrapper events, unexecuted paths, kernel-only state, or schedules outside the
configured exploration bound. A clean result is evidence over admitted
observations, not a proof of total correctness.

## Replayable evidence

`programs/p101-inspect/test/test_native_cli.sh` exercises clean analysis,
tamper rejection, rule checks, policy-specific view status, human/JSON output,
resource leaks, and a synchronization counterexample. Both Clang and GCC build
the aggregate workspace targets, and `audit-errors`, clang-tidy, and cppcheck
check the new C implementation.
