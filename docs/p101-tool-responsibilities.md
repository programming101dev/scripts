# p101 tool responsibilities

This map records the consolidation boundary for the p101 tools. The goal is
one evidence acquisition path for each kind of input, followed by small policy
tools with explicit judgments.

## Source evidence

- `p101-c-facts` acquires Clang-derived C/C++ facts. It is the fact-producing
  interface; it does not decide whether a project is well structured.
- `lib_c_facts` parses and exposes the fact snapshot.
- `p101-wrapper-audit` owns the portable wrapper boundary: missed wrappers,
  unresolved external calls, indirect-call boundaries, wrapper form, and the
  opt-in platform-header rule pack.
- `p101-error-contract` owns error-flow and `p101_error`/`p101_env` lifecycle
  judgments.
- `p101-module-map` owns module structure: file scope, public API surface,
  include direction, coupling, and dependency cycles.
- `p101-mutation-check` owns mutation execution and surviving-mutant policy.

`p101 doctor` is the source/module preflight. It orchestrates these tools and
reuses one fact snapshot; it does not reimplement their policies. `p101 check`
owns the larger quality, runtime, fault-walk, HTML, and bundle workflow.

## Runtime evidence

- `p101 observe` captures stdout, stderr, resource events, call events, the
  manifest, and the run receipt. Capture does not make policy judgments.
- `lib_tool_event` owns event protocol v4, parsing, and construction of the
  policy-free `p101-run-model-v1`. Its `p101-event-model` frontend is the only
  event-log parser launched by `p101 analyze`.
- `p101_runtime.py` owns the resource, synchronization, and trace policy
  modules over that model. It also renders their text/JSON views and the
  correlated report. Policies share facts but retain separate diagnostic IDs,
  summaries, and exit statuses.
- `p101 report`, `p101 resource`, `p101 sync-check`, and `p101 trace` are views
  over an analysis directory. They do not parse event logs again.
- The standalone `p101-resource-tracker`, `p101-sync-check`, `p101-trace`, and
  `p101-report` binaries remain differential/reference implementations while
  migration receipts are accumulated. They are available through the explicit
  `p101 report-events` escape hatch, not the ordinary workflow.
- `p101-error-path-walk` owns repeated fault campaigns. Every baseline and
  injected case goes through the same `p101 run` capture/model/policy pipeline;
  the walker consumes the normalized resource-policy summary and never launches
  the four standalone analyzers.

## Teaching policy

- Diagnostic producers own stable IDs and evidence, not repair walkthroughs.
- `playgrounds/lessons/manifest.json` maps those IDs to primary and related
  lessons, prerequisites, tracks, and verification commands.
- `p101_lessons.py` validates that curriculum contract and annotates reports.
- `p101 check` writes one `lesson-guide.md` spanning static, runtime, and
  fault-campaign findings.

This keeps lesson wording and sequencing out of the analyzers while making a
new unmapped diagnostic fail the workspace acceptance gate.

## Shared process mechanism

`lib_util` owns child launch, output redirection, `exec`, and `wait` through
`p101_tool_run_capture`. Individual tools retain only their child-environment
setup policy. This keeps process plumbing shared without putting tool-specific
environment variables or exit-status judgments in a library.

## Tradeoff

The consolidation shares facts, not policy. A single model builder removes
duplicate parsing and lifecycle state, while three small policy modules keep
resource, synchronization, and trace judgments independently testable. The
standalone analyzers are retained temporarily for differential debugging;
ordinary commands no longer depend on them. This preserves the useful teaching
boundaries without paying for seven processes and several competing models per
run.

`p101 observe` and `p101 analyze` are deliberately separate. `p101 run` is only
their convenience composition, so one immutable capture can be replayed against
new policy without rerunning student code.

## Receipts

Replay the relevant checks with:

```sh
libraries/lib_util/build.sh -q
libraries/lib_util/test.sh
programs/p101-wrapper-audit/test.sh
programs/p101-error-contract/test.sh
programs/p101-module-map/test.sh
programs/p101-report/test.sh
programs/p101-observe/test.sh
programs/p101-doctor/test.sh
scripts/tests/test-p101-runtime.py
scripts/tests/test-p101-lessons.py
scripts/p101 lessons check
scripts/checks/check-p101-regression-corpus.sh
```

Blind spots remain the same as the underlying evidence: unparsed source,
direct calls that bypass wrappers, third-party internals, missing event
emission, and runtime paths that were not exercised.
