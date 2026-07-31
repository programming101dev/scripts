# Finding-to-lesson contract

The finding-to-lesson catalog turns a stable diagnostic ID into a teaching
route without moving repair policy into the diagnostic producer.

## Boundary

Tools continue to own:

- whether a finding exists;
- its stable ID, severity, source location, message, and evidence;
- the admitted source facts or runtime events;
- the exit status.

The playground curriculum owns:

- the primary lesson and related practice labs;
- prerequisite lessons and track placement;
- conceptual explanation and repair walkthrough;
- the command that verifies the repair.

This separation matters. Changing lesson prose must never change whether a
program passes, and a diagnostic should not contain an answer key.

## Admitted inputs and outputs

`p101_lessons.py` admits:

- `playgrounds/lessons/manifest.json`;
- the `expected.json` and `lesson.md` files selected by its `case_glob`;
- tool source files used by the workspace completeness check;
- finding JSON or text reports supplied to `guide`.

It produces:

- `p101 lesson <ID>` terminal guidance;
- `p101 lessons list` mappings;
- a linked Markdown guide for a check directory;
- lesson annotations in runtime JSON and HTML reports;
- a cohort ranking of the lessons implicated most often;
- exit `1` when an emitted diagnostic lacks a lesson;
- exit `2` for malformed catalogs, missing lesson files, or unreadable inputs.

`p101 analyze` records the path and SHA-256 digest of the effective catalog and
lesson files in its receipt. It checks that digest again after analysis.

## Completeness rule

The workspace gate scans diagnostic IDs that the active tools and shared runtime
policies can emit. Every non-fallback ID must map to at least one substantive
lesson file. Fallback IDs ending in `000` are explicitly listed rather than
silently ignored. Stale ignored IDs also fail the gate.

The first lesson by curriculum order is the primary lesson. Additional labs
using the same diagnostic become related practice. This lets `P101-FD-001`, for
example, teach basic descriptor ownership first and then point to early-return
and partial-cleanup variants.

## Blind spots

The source inventory is deterministic but deliberately scoped to active p101
tool implementations and shared runtime rule files. Diagnostics synthesized by
an external plugin or third-party tool are not part of the workspace
completeness claim. A report containing such an ID records it as unmapped and
`p101 lessons guide` exits `1`.

A mapped lesson does not prove that its repair is correct. The lesson's
verification command and the original detecting tool provide that evidence.

## Replayable evidence

```sh
./test-p101-lessons.py
./p101 lessons check
./p101 lesson P101-FD-001
./p101 lessons guide --markdown /path/to/p101-check-output
```
