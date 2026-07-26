# p101 event parser extraction note

Role: `architect`

This note records the decision from the p101 tool audit: event-log parsing is a
good future shared-library extraction, but it should not be bundled with
unrelated tool fixes.

## Current state

The p101 runtime tools intentionally share plain tab-separated event streams:

- `P101FD`
- `P101ALLOC`
- `P101FORK`
- `P101EXEC`
- `P101CALL`

The parsing mechanism is currently duplicated across:

- `p101-resource-tracker`, which replays resource lifetimes;
- `p101-report`, which correlates resource and call logs;
- `p101-trace`, which renders call logs.

That duplication is acceptable while the v1 format is stable, but it becomes a
maintenance hazard when the schema changes. A v2 timestamp/sequence field would
force coordinated edits in several parsers.

## Decision

Extract the shared event parsing mechanism when the next event-schema change or
new event consumer is started.

Do not extract policy with it. The shared library should parse records and
report parse status; it should not decide whether a leaked descriptor is a
finding, how a trace tree is rendered, or what text belongs in a teaching
report.

## Proposed boundary

A future event parser library should own:

- event tags and version constants;
- common tab splitting and field unescaping;
- numeric field parsing and range checks;
- parsed structs for resource and call records;
- parse statuses such as `ok`, `other`, `bad_version`, and `malformed`;
- unit tests and a small golden corpus for each record kind.

The existing tools should continue to own:

- resource lifetime replay;
- leak/bad-release/exec-inheritance findings;
- trace tree rendering;
- correlated report wording and diagnostic IDs;
- command-line interfaces and exit-status policy.

## Candidate name

`lib_p101_events` is the clearest name if the library is created. It is more
specific than `lib_event` and avoids confusing the C fact stream in
`lib_c_facts` with runtime event logs.

## Extraction order

1. Add the library with the current v1 parser and tests.
2. Convert `p101-trace` first, because it only consumes `P101CALL`.
3. Convert `p101-report`, which consumes both resource and call records.
4. Convert `p101-resource-tracker`, keeping its lifetime model local.
5. Run the p101 behavior regression corpus and playground tour after each
   conversion.

## Non-goals

- Do not hide the TSV format from students.
- Do not merge the tools into one binary as part of this extraction.
- Do not move teaching policy or diagnostic wording into the parser library.

