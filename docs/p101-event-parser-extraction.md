# p101 event parser extraction note

Role: `architect`

This note records the decision from the p101 tool audit: event-log parsing
should be shared in layers. The byte-safe reader and v2 schema parser now live
in `lib_env`; tool-specific policy remains in the individual tools.

## Current state

The p101 runtime tools intentionally share plain tab-separated event streams:

- `P101FD`
- `P101ALLOC`
- `P101FORK`
- `P101EXEC`
- `P101CALL`

The byte-level input mechanism is shared by `lib_env`:

- `p101_env_read_event_line()` reads one physical event-log line;
- it rejects embedded NUL bytes and overlong physical lines as malformed;
- it reports EOF and I/O failure distinctly.

The schema-level parser is also shared by `lib_env`:

- `p101_env_parse_event_line()` parses supported v2 runtime records;
- `p101_env_event_line_is_ours()` identifies p101 runtime event prefixes;
- `p101_env_event_parse_status_name()` gives stable status text.

That shared primitive is used by:

- `p101-resource-tracker`, which replays resource lifetimes;
- `p101-report`, which correlates resource and call logs;
- `p101-trace`, which renders call logs.

Each tool maps the shared parsed record into its own model. That keeps the
mechanism shared while leaving leak policy, trace rendering, and teaching text
local.

## Decision

Keep the byte-level reader and runtime event schema parser in `lib_env`, because
`lib_env` owns the event emission contract and already sits below the consuming
tools.

Do not extract policy with it. The shared library should parse records and
report parse status; it should not decide whether a leaked descriptor is a
finding, how a trace tree is rendered, or what text belongs in a teaching
report.

## Proposed boundary

The shared parser owns:

- event tags and the supported version;
- common tab splitting and physical field boundaries;
- numeric field parsing and range checks;
- parsed structs for resource and call records;
- parse statuses such as `ok`, `other`, `bad_version`, and `malformed`;
- unit tests and corpus coverage through the consuming tools.

The existing tools should continue to own:

- resource lifetime replay;
- leak/bad-release/exec-inheritance findings;
- trace tree rendering;
- correlated report wording and diagnostic IDs;
- command-line interfaces and exit-status policy.

## Extraction order

0. Keep byte-safe physical-line reading in `lib_env` and regression-test
   malformed lines through the resource tracker.
1. Add the shared v2 schema parser to `lib_env`.
2. Convert `p101-trace` first, because it only consumes `P101CALL`.
3. Convert `p101-report`, which consumes both resource and call records.
4. Convert `p101-resource-tracker`, keeping its lifetime model local.
5. Run the p101 behavior regression corpus and playground tour after each
   conversion.

## Non-goals

- Do not hide the TSV format from students.
- Do not merge the tools into one binary as part of this extraction.
- Do not move teaching policy or diagnostic wording into the parser library.
