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
mixed with unrelated program output. Version 3 is emitted. The parser also
accepts version 2 so old teaching receipts remain readable; version 1 is
rejected.

Version 3 adds an execution-context ID between PID and sequence number. A
context normally corresponds to one `p101_env`, which is the unit used by the
one-environment-per-thread convention.

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
cmake -S libraries/lib_tool_event/test -B libraries/lib_tool_event/test/build
cmake --build libraries/lib_tool_event/test/build
ctest --test-dir libraries/lib_tool_event/test/build --output-on-failure
```

The test covers version-2 compatibility, version-3 context metadata, escaped
serialization round-trips, and generic lifecycle replay.
