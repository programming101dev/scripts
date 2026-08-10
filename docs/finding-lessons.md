# Finding-to-lesson contract

The finding-to-lesson catalog turns a stable diagnostic ID into a teaching
route without moving repair policy into the diagnostic producer.

## Boundary

Tools continue to own:

- whether a finding exists;
- its stable ID, severity, source location, message, and evidence;
- the admitted source facts or runtime events;
- the exit status.

Source-oriented tools serialize those fields through
`lib_tool_event`'s `p101_tool_diagnostic_write()`. Default text therefore uses
the editor-friendly `path:line:column: severity: message [ID]` grammar used by
compilers, clang-tidy, and cppcheck. JSON carries the same message in a
`p101-tool-diagnostic-v1` object. When a catalog route is known, both formats
also carry the lesson identity and a direct playground URL.
The common command-line selection is `-d:human`, `-d:json`, or
`-d:human,json`; dual mode reserves stdout for JSON and stderr for human
diagnostics.

Complete source-tool runs use the shared `p101-tool-report-v1` envelope. It
adds the producer's admitted inputs, required `does_not_prove` boundary,
summary counters, typed outcome, and Unix exit status around those same
diagnostic objects. There is no `-j` or `--json` alias; one exact parser keeps
the command line consistent across tools.

The playground curriculum owns:

- the primary lesson and related practice labs;
- prerequisite lessons and track placement;
- conceptual explanation and repair walkthrough;
- the command that verifies the repair.

`playgrounds/lessons/manifest.json` is also the single source of truth for the
finding ID, lesson ID, lesson location, and public lesson URL. Native tools do
not maintain parallel mapping tables. Run
`./generators/generate-tool-lesson-catalog.py` after editing the manifest; it
generates the typed `lib_tool_event` catalog and the shared
`p101_tool_rule_definition_lookup()` implementation. The governed
`tool-lesson-catalog` check rejects generated drift.

This separation matters. Changing lesson prose must never change whether a
program passes, and a diagnostic should not contain an answer key.

## Admitted inputs and outputs

`p101_lessons.py` admits:

- `playgrounds/lessons/manifest.json`;
- the `expected.json` and `lesson.md` files selected by its `case_glob`;
- tool source files used by the workspace completeness check;
- finding JSON or text reports supplied to `guide`.

The native catalog generator admits only the same manifest and its declared
lesson files. It produces a C enum and immutable lookup table; it does not
infer severity, messages, or whether a finding exists.

It produces:

- `python3 runtime/p101_lessons.py show <ID>` terminal guidance;
- `python3 runtime/p101_lessons.py run <ID>` isolated broken-state evidence and a receipt;
- `python3 runtime/p101_lessons.py verify-one <ID> [report ...]` student repair verification;
- `python3 runtime/p101_lessons.py list` mappings;
- `python3 runtime/p101_lessons.py verify [--quick|--full]` executable acceptance receipts;
- `python3 runtime/p101_lessons.py coverage` as Markdown or JSON;
- `python3 runtime/p101_lessons.py progress <receipt-path ...>` prerequisite-aware progress;
- a linked Markdown guide for a check directory;
- lesson annotations in runtime JSON and HTML reports;
- a cohort ranking of the lessons implicated most often;
- exit `1` when an emitted diagnostic lacks a lesson;
- exit `2` for malformed catalogs, missing lesson files, or unreadable inputs.

`runtime/p101-analyze.py` records the path and SHA-256 digest of the effective catalog and
lesson files in its receipt. It checks that digest again after analysis.

## Completeness rule

The workspace gate scans diagnostic IDs that the active tools and shared runtime
policies can emit. Every non-fallback ID must map to at least one substantive
lesson file and executable acceptance evidence. Fallback IDs ending in `000`
are explicitly listed rather than silently ignored. Stale ignored IDs also
fail the gate.

There are two deliberately separate receipts:

- native evidence runs either the real playground scenario or the owning
  tool/policy test suite;
- a canonical broken/repaired report pair proves that the finding routes to the
  intended lesson and that repaired evidence is accepted.

The coverage matrix labels these columns independently. Passing the protocol
pair cannot be reported as proof that the analyzer detected the original bug.
Playground cases also carry a deterministic repair oracle: the detecting
finding must disappear, or the documented fixed-output contract must hold.
Declared macOS/Linux/FreeBSD support is a contract, not evidence: a platform is
listed as verified only when its successful `--full` receipt is supplied with
`python3 runtime/p101_lessons.py coverage --receipts <path>`.

The first lesson by curriculum order is the primary lesson. Additional labs
using the same diagnostic become related practice. This lets `P101-FD-001`, for
example, teach basic descriptor ownership first and then point to early-return
and partial-cleanup variants.

## Blind spots

The source inventory is deterministic but deliberately scoped to active p101
tool implementations and shared runtime rule files. Diagnostics synthesized by
an external plugin or third-party tool are not part of the workspace
completeness claim. A report containing such an ID records it as unmapped and
`python3 runtime/p101_lessons.py guide` exits `1`.

An owning-tool suite proves the checked fixture and analyzer behavior, not every
possible C program. Static heuristics remain bounded by their admitted facts,
and runtime lessons remain bounded by emitted wrapper events. A student repair
is accepted only by `python3 runtime/p101_lessons.py verify-one` using the original detecting evidence
or the playground case's fixed-state oracle.

## Replayable evidence

```sh
./tests/test-p101-lessons.py
./tests/test-tool-lesson-catalog.py
./generators/generate-tool-lesson-catalog.py --check
./runtime/p101_lessons.py check
./runtime/p101_lessons.py show P101-FD-001
./runtime/p101_lessons.py run P101-FD-001
./runtime/p101_lessons.py verify-one P101-FD-001 /path/to/correlated-report.json
./runtime/p101_lessons.py verify --quick
./runtime/p101_lessons.py coverage
./runtime/p101_lessons.py progress /path/to/student-receipts
./runtime/p101_lessons.py guide --markdown /path/to/check-output
```

The governed `templates-standalone`, `playground-tour`, and `playground-lab`
nodes run the representative native acceptance set.
`check-after-update-all.sh` runs every owning-tool profile and every native
playground issue case on the current platform.
