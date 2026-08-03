# p101 tool responsibilities

This map records the consolidation boundary for the p101 tools. The goal is
one evidence acquisition path for each kind of input, followed by small policy
tools with explicit judgments.

## Source evidence

- `lib_c_facts` owns native Clang-derived C/C++ acquisition and the reusable
  fact snapshot format. C tools call its analysis API directly; `p101-c-facts`
  is only its command-line snapshot frontend.
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
- `lib_tool_event` owns event protocol v5, parsing, lifecycle state, and
  construction of the policy-free `p101-run-model-v1`. Its
  `p101-event-model` frontend is the only event-log parser launched by
  `p101 analyze`; the model's lifecycle entries and findings are produced in C.
- `p101_runtime.py` currently owns presentation policy over that C model:
  diagnostic-ID mapping, synchronization and trace judgments, text/JSON views,
  and the correlated report. It does not reconstruct resource lifecycle state.
  This Python layer remains orchestration/policy debt, not a competing parser
  or lifecycle implementation.
- `p101 report`, `p101 resource`, `p101 sync-check`, and `p101 trace` are views
  over an analysis directory. They do not parse event logs again.
- Resource, synchronization, trace, and correlated-report policy is owned by
  the canonical `p101 analyze`/`p101 view` runtime. The former standalone
  renderers are retired and are not admitted workspace dependencies.
- `p101-error-path-walk` owns repeated fault campaigns. Every baseline and
  injected case goes through the same `p101 run` capture/model/policy pipeline;
  the walker consumes the normalized resource-policy summary and never launches
  the four standalone analyzers.
- `p101 fault-campaign` expands the wrapper fault contract into platform- and
  mode-specific error-path-walk cases. It uses the authoritative
  wrapper-to-native mapping and resolves non-errno symbolic codes with the
  current platform compiler; a campaign cannot claim another host platform.
- `p101 interleaving-walk` owns bounded synchronization-event reorderings. It
  preserves recorded per-thread and explicit happens-before edges; it is not a
  general scheduler model checker.
- `p101 api-diff` owns governed public-API snapshot comparison.
- `p101 fuzz` delegates to a repository's declared `fuzz.sh`; absence is
  explicit tool trouble rather than a silently skipped check.

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

`p101_record` owns protocol-neutral TSV field handling and JSON string
encoding. Tools may decide their own JSON shape, but do not implement their own
escaping loops.

`lib_util` owns child launch, output redirection, `exec`, and `wait` through
`p101_tool_run_capture`. Individual tools retain only their child-environment
setup policy. This keeps process plumbing shared without putting tool-specific
environment variables or exit-status judgments in a library.

## Tradeoff

The consolidation shares facts, not policy. A single model builder removes
duplicate parsing and lifecycle state, while three small policy modules keep
resource, synchronization, and trace judgments independently testable. The
standalone analyzers are retired. Differential regression checks compare
canonical policy outputs and native-wrapper fixtures without retaining
competing runtime implementations.

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
