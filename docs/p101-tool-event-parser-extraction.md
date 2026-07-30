# Event protocol extraction

Role: `architect`

## Decision

The p101 runtime event protocol belongs to `libraries/lib_tool_event`.

`lib_env` owns runtime observation: it assigns execution-context IDs and
per-context sequence numbers, samples monotonic and wall clocks, and invokes
the configured observers. It does not own the byte representation.

`lib_tool_event` owns:

- schema versions and record kinds;
- escaped, tab-separated serialization;
- bounded physical-line input;
- parsing and validation;
- the generic resource-lifecycle replay model.

Tools include `<p101_tool_event/event.h>` directly. They must not reach through
`lib_env` for parsing helpers or keep private copies of the wire grammar.

## Contract

Admitted input is a byte stream containing p101 event records, optionally
mixed with unrelated program output. Version 4 is the sole emitted and
accepted version. All other formats are rejected.

Version 4 includes an execution-context ID, sequence number, monotonic and wall
timestamps, and a completion count. `lib_env` serializes sequence assignment
with record publication for one environment.

Output consists of parsed `struct p101_tool_event_record` values, serialized records,
or policy-free lifecycle entries and findings. Tool-specific severity,
diagnostic IDs, wording, and exit policy remain in each program.

## Blind spots

The protocol sees only events emitted by p101 wrappers or user code that calls
the observation API. Direct libc calls, third-party internals, and resources
without an instrumented acquire/release boundary remain invisible.

Timestamps describe when the wrapper emitted the record, not when an
unobserved kernel-side effect occurred.

## Evidence

```sh
cd libraries/lib_tool_event
./build.sh -q
./test.sh
```

The test covers non-current-version rejection, version-4 context metadata and
completion receipts, escaped serialization round-trips, and generic lifecycle
replay.
