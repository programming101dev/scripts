# Tool consolidation and responsibility map

The workspace has no central `p101` dispatcher. CMake and repository scripts
already own compilation and native analysis, while
`check-after-update-all.sh` already owns dependency-aware workspace
orchestration. A second command router duplicated those responsibilities and
made every tool rename a workspace-wide compatibility problem.

## The three policy categories

### `programs/p101-audit`

This repository owns semantic source policy. Its internal engines are
`audit-facts`, `audit-wrappers`, `audit-errors`, `audit-modules`, and
`audit-doctor`. They share one repository, build, dependency declaration, and
test conductor while retaining separate C modules and narrow command-line
contracts.

- Inputs: source paths, compile databases, P101FACT snapshots, and scoped
  wrapper-boundary ledgers.
- Outputs: facts, findings, Markdown/JSON reports, and explicit exit status.
- Blind spots: source omitted from the admitted compile database, unsupported
  language constructs, and runtime behavior.

`lib_c_facts` owns Clang acquisition and the typed fact format. The audit
engines own policy only; they must not grow private parsers.

### `programs/p101-test`

This repository owns executable fault and mutation campaigns. Its internal
engines are `test-faults` and `test-mutation`.

- Inputs: a command under test, the wrapper fault contract, mutation
  candidates, and bounded campaign options.
- Outputs: per-case evidence, surviving/killed mutation results, and status.
- Blind spots: paths not executed, direct calls that bypass instrumented
  wrappers, third-party internals, and schedules outside declared bounds.

Repository `test.sh` and `fuzz.sh` remain the ordinary unit/fuzz mechanism.
The category program adds cross-run policy; it does not replace CTest or
libFuzzer.

### `programs/p101-inspect`

This repository owns policy-free capture. `inspect-capture` records one command
into immutable streams and a receipt. Shared runtime scripts under
`scripts/runtime/` verify and replay that evidence into the resource,
synchronization, trace, and correlated views.

- Inputs: one command, capture flags, and wrapper events.
- Outputs: immutable capture artifacts and a fingerprinted receipt.
- Blind spots: direct libc calls, omitted events, third-party internals, and
  behavior outside the captured execution.

`lib_tool_event` owns event parsing, lifecycle state, and the policy-free run
model. Inspect and report code must consume that mechanism rather than create a
private protocol implementation.

## What was removed

The separate `p101-error-contract`, `p101-module-map`, `p101-doctor`, and
`p101-mutation-check` repositories were merged into their category owners. The
`p101` dispatcher, the duplicate check-graph selector, and the fuzz router were
removed. A single `runtime/student-workflow.sh` remains as the student-facing
composition of audit, capture, and bounded fault testing; it is not a second
workspace acceptance graph. Fuzzing is invoked through the repository that
declares the target; lessons, models, reports, and playgrounds are invoked
through their owning scripts.

## Tradeoff

The consolidation is at the repository and orchestration boundary, not a
forced monolith. Keeping the internal engines separate preserves deterministic
inputs, focused tests, distinct finding policy, and useful debugger entry
points. Sharing three category repositories removes four independently refreshed
program worktrees and the central routing layer without mixing source audit,
runtime capture, and executable testing.

The category commands use category names in their current evidence schemas.
Shared runtime and library schemas retain the `p101-*` project namespace;
those names identify record formats, not live command aliases.

## Replayable evidence

```sh
programs/p101-audit/test.sh
programs/p101-test/test.sh
programs/p101-inspect/test.sh
scripts/tests/test-p101-analyze.py
scripts/tests/test-p101-lessons.py
scripts/runtime/p101_lessons.py check
scripts/checks/check-p101-regression-corpus.sh
scripts/check-after-update-all.sh
```
