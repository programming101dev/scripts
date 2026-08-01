# p101 tool design contract

The p101 tools are teaching infrastructure. They should feel simple enough for
students to inspect, but disciplined enough that instructors can trust the
reports. This contract adapts the reusable lessons from the Brain-first design
system without the strict security or workstation-specific rules.

## 1. Bound the tool

Every tool should make its boundary clear:

- what it reads;
- what it ignores;
- which schemas or wrapper events it understands;
- which files or platforms are outside its model.

This matters most for wrapper-based tools. If a program calls `malloc` directly,
`p101-resource-tracker` cannot see the allocation. If a dependency opens a file
without a p101 wrapper, the descriptor is outside the observed event stream.
That is not a bug in the report; it is a boundary that must stay visible.

## 2. Separate mechanism from policy

Reusable mechanics belong in libraries or shared scripts:

- event/fact parsers;
- record schemas;
- wrapper mechanics;
- resource lifetime models;
- shared build and check infrastructure.

Teaching policy belongs in the tool that teaches it:

- warning thresholds;
- wording;
- scoring;
- module-design heuristics;
- what counts as a finding for a particular assignment.

When a second tool needs the same parser or protocol, promote the mechanism.
`lib_c_facts` exists for this reason: the `P101FACT` format is shared
infrastructure, while `p101-module-map` owns the module-design advice.
Runtime event logs are the next likely candidate; see
[`p101-tool-event-parser-extraction.md`](p101-tool-event-parser-extraction.md).

## 3. Prefer deterministic receipts over claims

For every nontrivial feature, keep a replayable receipt:

- a unit test;
- a regression corpus fixture;
- a playground tour case;
- a `check-after-update-all.sh` run;
- a short smoke command in the README.

Tool output is evidence, not proof. It is useful because someone can rerun the
same command over the same admitted inputs and inspect the result.

Machine-readable receipts use the shared outcome vocabulary where practical:
`clean`, `findings`, `refused`, `incomplete`, `unsupported`, and `tool-error`.
The C representation and `p101-tool-run-receipt-v1` writer live in
`lib_tool_event`; the post-update graph uses the same vocabulary. Ordinary
command-line exit statuses remain `0` for clean, `1` for findings, and `2` for
refusal, incomplete evidence, unsupported execution, or tool failure.

## 4. Govern boundaries and checks

`p101-boundaries.json` names the owner, admitted input, output, refusal,
evidence, and clean/refusal/binding-swap tests for each load-bearing shared
boundary. `p101-check-graph.json` names the post-update checks, dependencies,
resource effects, guarantees, and limitations. Their validators are release
gates; adding an ungoverned verification entry point or silently moving an
owner is a failure.

## 5. Make blind spots explicit

Every tool README should be honest about important blind spots. Common examples:

- direct non-p101 calls are invisible to wrapper-based tools;
- third-party library behavior may be outside the event stream;
- heuristic module advice is not a compiler error;
- event ordering may be limited by the log schema;
- platform-specific APIs may be intentionally absent.

This keeps the tools trustworthy. A bounded report with a visible limitation is
better than a confident report that silently overclaims.

## 6. Keep APIs small and local

For C code:

- use `static` for file-local helpers;
- expose only functions, types, and macros needed outside the file;
- prefer opaque or narrow types when callers do not need representation details;
- put shared formats in one parser rather than copying field indexes.

The module-map and wrapper-audit work should continue to use real parsers for C
facts. Hand-rolled C parsing is acceptable only for deliberately narrow
line-oriented formats, not for C syntax.

## 7. Use the role lens lightly

Before substantial work, classify it as one or more of:

- `architect`: boundaries, protocols, repos, or shared mechanisms;
- `implementor`: new behavior;
- `fixer`: repair with before/after evidence;
- `judge`: audit or review with explicit limits.

The role only decides what kind of evidence is appropriate. It should not become
ceremony.
