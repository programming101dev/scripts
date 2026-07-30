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

`p101-doctor` orchestrates these tools and reuses one fact snapshot. It does
not reimplement their policies.

## Runtime evidence

- `p101-observe -C` captures stdout, stderr, resource events, call events, the
  manifest, and the run receipt without analysis.
- `lib_tool_event` owns the event schema, parser, lifecycle model, and receipt
  mechanics.
- `p101-report -b DIR RUN_DIR` parses the captured logs once and writes the
  correlated text, JSON, and Mermaid artifacts from the same model.
- `p101-resource-tracker`, `p101-sync-check`, and `p101-trace` remain focused
  analyzers because they enforce domain-specific strictness that the
  correlated report does not yet preserve: resource high-water/strict release
  policy, synchronization misuse, and call-stack integrity.
- `p101-error-path-walk` owns repeated fault campaigns. It consumes observe
  receipts; it does not duplicate event parsing.

## Shared process mechanism

`lib_util` owns child launch, output redirection, `exec`, and `wait` through
`p101_tool_run_capture`. Individual tools retain only their child-environment
setup policy. This keeps process plumbing shared without putting tool-specific
environment variables or exit-status judgments in a library.

## Tradeoff

The specialized runtime analyzers have not been collapsed into one large
binary. Their distinct exit semantics are useful teaching contracts, and
merging them before the correlated model represents those contracts would
discard behavior rather than remove duplication. The shared event parser and
one-pass report bundle remove mechanism duplication now; later consolidation
should happen only when differential corpus tests prove semantic parity.

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
scripts/check-p101-regression-corpus.sh
```

Blind spots remain the same as the underlying evidence: unparsed source,
direct calls that bypass wrappers, third-party internals, missing event
emission, and runtime paths that were not exercised.
