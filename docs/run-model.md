# p101 causal run model

`p101-run-model-v1` is the normalized, policy-free execution model built once
by `lib_tool_event` through `p101-event-model`. It is the stable boundary
between captured evidence and tool-specific judgments.

## Admitted inputs

The model admits only validated event protocol v4 records from `resources.log`
and `calls.log`. `p101-event-model` uses the authoritative `lib_tool_event`
parser; no runtime policy module parses TSV.

Each node has a deterministic identity derived from process, observation
context, event sequence, and event kind. Current node domains are calls and
resources. Current causal edges are:

- `call-parent`: lexical/dynamic call nesting within one context;
- `call-return`: a matched enter/exit pair;
- `call-caused-event`: the active call when a resource event was emitted;
- `resource-lifetime`: acquisition/replacement to release/replacement;
- `process-child-event`: a successful fork/spawn to observations made by the
  created process.

Timestamps remain observations, not a claimed total order across independently
buffered processes. Per-context sequence and explicit edges carry the stronger
ordering claims.

## Outputs and policy

`p101 analyze` launches the model builder once, runs resource,
synchronization, and trace policies in-process, and fingerprints
`run-model.json` plus every rendered view in its separate analysis receipt.
`p101 verify` checks graph integrity and optional expectations. `p101 compare`
compares stable finding identities and causal edge counts without treating
volatile PIDs, pointer values, or descriptor numbers as semantic identity.
`p101 check --rules PACK` evaluates a bounded declarative policy pack over the
validated model and findings. `p101 explain FINDING-ID` shows the source-matched
causal neighborhood behind one finding.

An expectations file is intentionally plain text:

```text
p101-expectations-v1
result=clean
finding_count=0
forbid=P101-*
require_edge=call-return
require_edge=resource-lifetime
require_call=p101_open
forbid_call=malloc
require_resource=fd
min_edges=resource-lifetime:1
min_nodes=1
```

Supported rules are `result`, `finding_count`, `forbid`, `require`,
`require_edge`, `require_call`, `forbid_call`, `require_resource`, `min_edges`,
and `min_nodes`. Finding, call, and resource patterns use shell-style
wildcards. `min_edges` uses `KIND:COUNT`.

Verification first validates the analysis receipt, every output fingerprint,
the analyzer status/result relationship, stable node identities, summary
counts, edge endpoints, domain compatibility, and forward per-context causal
ordering. Expectation mismatches exit 1; malformed, modified, or incomplete
evidence exits 2.

## Blind spots

The graph cannot contain direct libc calls, third-party internals, kernel-only
activity, or wrapper events that were not emitted. It is deterministic evidence
about the admitted event streams, not proof of complete program behavior.

The graph deliberately contains facts rather than severity or teaching prose.
The three policy modules continue to own their diagnostic IDs and judgments.
`p101 report`, `p101 resource`, `p101 sync-check`, and `p101 trace` are
read-only views of the resulting analysis directory.

Built-in rule packs and their limits are documented in
[`rule-packs.md`](rule-packs.md).
