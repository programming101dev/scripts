# scripts Repository Guide

Welcome to the `scripts` repository. This guide will help you set up and run the provided scripts.

## **Table of Contents**

1. [Cloning the Repository](#cloning-the-repository)
2. [Prerequisites](#Prerequisites)
3. [Running the `setup.sh` Script](#running-the-setupsh-script)
4. [Running the `update.sh` Script](#running-the-updatesh-script)
5. [Running the `update-all.sh` Script](#running-the-update-allsh-script)

## **Cloning the Repository**

Clone the repository using the following command:

```bash
git clone https://github.com/programming101dev/scripts.git
```

Navigate to the cloned directory:

```bash
cd scripts
```

Ensure the public entry points are executable:

```bash
chmod +x p101 p101-workspace setup.sh update-all.sh check-after-update-all.sh
```

## Command layout

The repository keeps five public commands at its root:

- `p101` is the student/tool dispatcher.
- `p101-workspace` is the maintainer dispatcher.
- `setup.sh` performs first-time workspace setup.
- `update-all.sh` refreshes and builds the compiler matrix.
- `check-after-update-all.sh` runs the governed acceptance graph.

The implementation is grouped by responsibility:

- `checks/` contains acceptance and policy gates.
- `tests/` contains regression tests for shared scripts and contracts.
- `generators/` contains flag, wrapper-test, and source-data generators.
- `workspace/` contains configure/build and local toolchain mechanics.
- `distribution/` contains repository refresh and shared-file distribution.
- `runtime/` contains the implementation behind the `p101` dispatcher.
- `contracts/` contains machine-readable manifests and policies.
- `shared/library/` contains the canonical library-only install helpers.

This boundary is intentional: callers should prefer `p101` or
`p101-workspace`; direct paths into these directories are for maintainers,
tests, and CI. The C/C++ repositories still carry their own root build scripts
because templates must remain usable after being copied outside this workspace.

## **Prerequisites**

To ensure you have all of the required tools installed, run:
```bash
./p101-workspace environment
```

If you are missing tools follow these [instructions](https://docs.google.com/document/d/1ZPqlPD1mie5iwJ2XAcNGz7WeA86dTLerFXs9sAuwCco/edit?usp=drive_link).

To determine which compilers you have installed on your system, run:
```bash
./p101-workspace compilers
```

## **Running the setup.sh Script**

To setup the system the first time, run:

```bash
./setup.sh -c <c compiler> -x <c++ compiler>
```

To the see the list of possible compilers:
```bash
cat supported_c_compilers.txt
cat supported_cxx_compilers.txt
```

## **Running the update.sh Script**

After the system has been setup you will want to periodically update from github and rebuild:

```bash
./p101-workspace update -c <c compiler> -x <c++ compiler>
```

## **Running the update-all.sh Script**

If you want to verify that everything compiles with all of the supported compilers, run:

```bash
./update-all.sh
```

For an iterative portability pass, add `--interactive`:

```bash
./update-all.sh --interactive
```

When a repository's configure, build, or install phase fails, the script leaves
you at that repository/compiler boundary and waits. Push the fix from another
terminal, then press Enter; the script fast-forwards the repository with
`git pull --ff-only` before retrying only the failed phase. A failed pull returns
to the prompt without retrying stale code. Enter `q` to abort. Repositories that
already passed are not rebuilt, and after the repaired repository succeeds the
compiler matrix continues normally. The same option is available on `setup.sh`,
`update.sh`, and `build-repo.sh`. This is an intentional development relaxation:
after coordinated fixes move repository revisions, refresh and commit
`repos.lock` before expecting strict workspace acceptance to pass.

`repos.txt` uses `c`, `cxx`, and `python` for active projects. A newly created,
not-yet-populated C repository uses `c-bootstrap`: `clone-repos.sh` keeps it
present and updated, while build, distribution, test, and audit gates skip it.
Change the type to `c` in the same change that adds its project contract; an
active C repository is never allowed to pass without `config.cmake`, build
scripts, tests, and the shared workspace links.

## Reproducible workspace revisions

`repos.txt` declares repository ownership and location. `repos.lock` binds
every declaration to one exact 40-character Git commit. Setup, update,
`p101-workspace clone`, and GitHub Actions use the lock by default, so Linux,
macOS, and FreeBSD evaluate the same source graph even if an upstream `main`
branch moves while a matrix run is in progress.

Inspect or verify the current workspace with:

```bash
./p101-workspace lock verify
```

Following moving upstream branches is an explicit development operation:

```bash
./p101-workspace clone --latest
# review, build, test, commit, and push the coordinated changes
./p101-workspace lock refresh
git add repos.lock
git commit -m "Refresh workspace repository lock"
```

`lock refresh` refuses a repository whose `HEAD` is not exactly its configured
upstream, which prevents an unpushed local commit from entering a shared lock.
Use `--require-clean` when generated or other uncommitted worktree content must
also be absent. Ordinary lock verification requires exact origins and commits
and reports dirty worktrees without pretending their uncommitted content is
reproducible.

`setup.sh`, `update.sh`, and `update-all.sh` accept `--latest` as the same
explicit escape hatch. Strict post-update acceptance verifies the lock and
writes `workspace-lock-receipt.json`; the governed graph receipt records its
lock and manifest digests.

After `update-all.sh` succeeds, run the post-build acceptance checks:

```bash
./check-after-update-all.sh
```

This delegates to the governed graph in `contracts/p101-check-graph.json`. Every node
declares its argv, dependencies, resource effects, guarantee, and limitation;
the runner writes a log per node plus a `p101-tool-run-receipt-v1` receipt.
Use `./checks/p101-check-graph.py list` to inspect the graph,
`./check-after-update-all.sh --only boundaries` for one node and its
dependencies, or `--from <node>` to resume from a known boundary. With
`--interactive`, a failure pauses and retries exactly that node after the fix.

The graph includes the shared CMake regression harness, tool and wrapper
audits, fresh-template standalone checks, every repository-owned unit suite,
bounded fuzz smoke tests where supported, the playground tour, and the p101
behavior regression corpus. `contracts/p101-boundaries.json` separately binds shared
mechanisms to one owner and to clean, typed-refusal, and binding-swap tests.
`contracts/p101-test-inventory.json` prevents a repository or scripts verification entry
point from silently falling outside the runners.

Three narrower checks enforce contracts that used to be implicit:

```bash
./checks/check-p101-instrumentation.py
./checks/check-repository-tests.sh
./checks/check-workspace-public-api.sh
```

The workspace API audit requires a compile database from every C/C++ repository
in `repos.txt`; run `update-all.sh` first. This prevents a missing consumer build
from being mistaken for an unused public API. `--allow-incomplete` is available
for an explicitly provisional local report, and labels the result as
incomplete. Intentional non-consumers must be named with a reason in
`contracts/workspace-public-api-excludes.txt`; they are reported separately rather than
silently dropped.

The instrumentation check compares Clang-derived wrapper facts with
`contracts/instrumentation-contract.json`: error-aware wrappers must expose a fault
point, tracing must be balanced, and resource-owning wrappers listed in the
manifest must emit the corresponding lifecycle event. The repository test
check reports `NO TEST` and `NO FUZZ TARGET` explicitly instead of treating
absence as success. The workspace API check combines facts from all built
libraries, programs, templates, playgrounds, and examples so public functions,
types, and macros unused by every checked-in consumer become review candidates.
Those candidates are deterministic evidence, not proof that a general-purpose
wrapper API should be removed; use `--fail-findings` only after reviewing or
allowlisting intentional API.

For a shorter behavior-only gate, run:

```bash
./checks/check-p101-regression-corpus.sh
```

Every runtime fixture in that gate is captured once and analyzed from one
`p101-run-model-v1`. The gate checks executable expectations and the shared
resource, synchronization, trace, and correlated views.

To stress `lib_c_facts` against pinned external code rather than only p101
sources, run:

```bash
./checks/check-c-facts-external-corpus.sh -o /tmp/p101-c-facts-external
```

This opt-in suite samples 10 mature C and 10 mature C++ projects, then exercises
10 intentionally defective C cases, 10 intentionally defective C++ cases, and
10 IOCCC entries. It stores upstream trees in a disposable cache and writes the
exact source selection, raw facts, diagnostics, and result ledger to the output
directory. See [the external corpus contract](docs/c-facts-external-corpus.md).

To replay the source-contract audit over every active wrapper library:

```bash
./checks/check-p101-library-audit.sh
```

This uses each library's compile database, its optional checked-in
`.p101-wrapper-audit-allow` boundary ledger, `p101-error-contract`, and
`p101-module-map -L`. The boundary pass records a P101FACT v2 snapshot and
admitted-input manifest; both downstream C policy tools reuse that snapshot.
Runtime wrapper feature coverage is enforced by each split library's generated
wrapper tests and `unit-test-manifest.tsv`. Reports are written under one
artifact directory.

For the student-facing tool workflow, use the dispatcher:

```bash
./p101 check -s src -- ./build-clang/my-program input.txt
```

`p101 check` writes one report directory with the project quality gate
(`check.sh` when present), wrapper audit, module map, observed resource/call
logs, correlated findings, error-path walking, optional coverage, a
self-contained `index.html`, and a bug bundle.

Capture and analysis are separate so the same evidence can be replayed with a
newer build of the tools:

```bash
./p101 observe -o /tmp/student-run -- ./student-program
./p101 analyze /tmp/student-run
./p101 analyze -o /tmp/student-run.analysis-2 /tmp/student-run
```

For the common one-shot workflow, compose the two explicit stages:

```bash
./p101 run -o /tmp/student-run-with-analysis -- ./student-program
```

`p101 analyze` admits a `p101-observe` capture whose
`p101-run-receipt-v1` receipt names event protocol v4 and whose bounded
artifact fingerprints still match. It verifies the capture before and after
analysis, builds one policy-free model from one private fingerprint-checked
snapshot of the event logs, never writes inside the capture, runs the resource,
synchronization, and trace policy modules, renders the correlated report, and writes a separate
`p101-analysis-receipt-v1` receipt containing input fingerprints, content-based
tool versions, exit statuses, and output fingerprints. An existing output
directory is never reused.

Incomplete or modified captures are refused. `--force` is the explicit
instructor/debugging override; the resulting analysis receipt is permanently
labelled `capture_verification=overridden`. FNV-1a fingerprints detect ordinary
changes but are not signatures, and replay can only analyze wrapper events that
were actually captured. It cannot reconstruct direct libc calls, third-party
internals, or omitted events.

Every new analysis bundle also contains `run-model.json`, the canonical causal
graph of call and resource facts. Verify it directly or make a lesson/CI
expectation executable:

```bash
./p101 verify /tmp/student-run.analysis
./p101 verify -e p101-expectations.txt /tmp/student-run.analysis
./p101 compare previous.analysis current.analysis
./p101 check /tmp/student-run.analysis --rules resource-clean
./p101 explain /tmp/student-run.analysis P101-FD-001
./p101 resource /tmp/student-run.analysis
./p101 sync-check /tmp/student-run.analysis
./p101 trace /tmp/student-run.analysis
./p101 report /tmp/student-run.analysis
```

The model contract and expectation language are documented in
[`docs/run-model.md`](docs/run-model.md). Declarative course-policy packs are
documented in [`docs/rule-packs.md`](docs/rule-packs.md).

To run the checked playground lesson corpus:

```bash
./p101 corpus --quick
./p101 corpus
```

To turn that corpus into a student-facing lab series:

```bash
./p101 lab --quick
./p101 lab
```

`p101 lab` writes a self-contained `index.html`, a Markdown lab outline, the
checked corpus reports, and the command logs. Each lab has an issue ID, lesson,
fix checklist, and progress state. Students can fix one issue at a time and
re-run the command to watch labs move from `OPEN` to `FIXED`. Use
`--strict-corpus` for instructor/CI checks that should fail if the committed
broken fixtures stop producing their expected diagnostics.

Every stable finding ID is resolved through the checked playground lesson
catalog. Runtime JSON and HTML reports carry a primary lesson plus any related
labs; static findings can be resolved with the same dispatcher:

```bash
./p101 lesson P101-FD-001
./p101 lesson run P101-FD-001
./p101 lesson verify P101-FD-001 /path/to/report.json
./p101 lessons guide /path/to/check-output
./p101 lessons check
./p101 lessons verify --quick
./p101 lessons coverage
./p101 lessons progress /path/to/student-receipts
```

`p101 lessons check` scans the diagnostic IDs emitted by the tools and fails if
any non-fallback ID lacks a real lesson file, prerequisites, native acceptance
evidence, and a replayable repair oracle. `p101 lessons verify` materializes a
broken/repaired protocol pair for every ID; `--quick` runs representative
native evidence and `--full` runs every owning suite and playground issue case.
Native checks use up to four isolated workers by default; pass `--jobs 1` for
serial execution or a different bounded worker count for the host.
`p101 lessons coverage` exposes evidence level and platform support. The
mapping is curriculum policy in
`playgrounds/lessons/manifest.json`; the tools continue to own the evidence and
diagnostic IDs. The boundary and completeness claim are documented in
[`docs/finding-lessons.md`](docs/finding-lessons.md).

For lower-level student/instructor tooling around observed runs:

```bash
./p101 html-report /path/to/p101-observe-output
./p101 bug-bundle /path/to/p101-observe-output
./p101 cohort submission-*/correlated-report.json
```

The p101 tools follow a lightweight design contract: bounded inputs, explicit
blind spots, deterministic receipts, shared mechanisms, and small public APIs.
See [docs/p101-tool-design-contract.md](docs/p101-tool-design-contract.md).
The source/runtime ownership map and the consolidation tradeoff are recorded in
[docs/p101-tool-responsibilities.md](docs/p101-tool-responsibilities.md).
To check that each `p101-*` README exposes the minimum contract surface, run:

```bash
./checks/check-p101-tool-contracts.sh
./p101 contracts
```

To replay the broader p101 tool audit — README contract checks, strict
wrapper-audit checks over the C tools, and module-map design reports — run:

```bash
./checks/check-p101-tool-audit.sh
./p101 tool-audit
```

By default, module-map design notes fail the audit. Use
`--allow-module-notes` only for an exploratory report that should not enforce
the current module-splitting rules. Each C tool is parsed once; module-map
reuses the recorded P101FACT v2 snapshot, and a checked-in
`.p101-wrapper-audit-allow` file is treated as a scoped, stale-checked boundary
ledger.

## Wrapper caveat: `setjmp`

Most p101 wrappers can behave like the underlying C or POSIX function with
better error reporting and observability. `setjmp` and `sigsetjmp` are the
important exception: the macro must be invoked directly in the stack frame that
will receive the matching `longjmp`. Do not put a normal function wrapper
between `setjmp` and the caller that expects to resume. Fuzz harnesses and
teaching examples should call `setjmp`/`sigsetjmp` directly and reserve p101
wrappers for the surrounding cleanup, logging, and resource-management calls.

`github-actions/p101-stack.yml` is a starter CI workflow for macOS, Linux, and
FreeBSD. Copy it to `.github/workflows/` in the repo that should own the
multi-platform gate. In this repo it is kept byte-for-byte identical to
`.github/workflows/p101-stack.yml`; `./checks/check-github-actions-template.sh` and
`./check-after-update-all.sh` fail if the starter copy drifts from the live CI
workflow. The workflow can be dispatched for all platforms or one target OS
(`linux`, `macos`, or `freebsd`) when you only need to rerun a single leg.

`scripts/CMakeLists.txt` is the source of truth for the shared C/C++ build
pipeline. After editing it, run `./distribution/copy-cmake.sh` and commit the copied files in
the affected repos. `./checks/check-cmake-distribution.sh` and
`./check-after-update-all.sh` fail if any distributed copy has drifted.

When a repo is checked out inside the broader `programming101dev` workspace,
the shared CMake and standalone `test.sh`/`fuzz.sh` scripts prefer sibling
`libraries/*/include` and compiler-matching `libraries/*/build-<compiler>`
directories before installed `/usr/local` headers and libraries. That keeps
development builds honest after wrapper/library API changes. If a template or
repo is instantiated outside the workspace, those sibling paths are absent and the scripts
fall back to the installed p101 stack.

## **Testing the shared CMakeLists.txt**

Before committing changes to `CMakeLists.txt`, run:

```bash
./tests/test-cmake.sh
```

It configures and builds a matrix of tiny sample projects (library+executable
with a shared source, relative headers, whitespace/zero target lists, missing
config, code that must be rejected by clang-tidy, and a C++ variant) against
the `CMakeLists.txt` in this directory, so regressions are caught here instead
of in a student's build. Use `-k` to keep the sandbox with all logs.

## **Running the acceptance checks**

To verify that fresh template instances are self-contained aside from intentional
shared-artifact symlinks such as `.flags`, run:

```bash
./checks/check-templates-standalone.sh
```

It copies `template-c`, `template-c-program`, and `template-cxx` to `/tmp`,
rejects hidden parent-workspace script dependencies, and configures, builds,
and tests each fresh project instance.

To run the broader p101 stack ratchet, use:

```bash
./checks/check-p101-stack.sh -c clang -x clang++
./p101 stack-check -c clang -x clang++
```

That script builds repos from `repos.txt`, runs the standalone template check,
then runs the `p101-tool-playground` tour, a `p101 check` golden-path smoke, and
the quick playground corpus and lab-book smoke. During development, you can use
`--skip-repo-build` for a quicker smoke of the template and playground pieces.
Use `--skip-install` when you want a non-interactive build-only stack check
that does not run each repo's `install.sh`.

## **Discovering new flags**

To see every flag your installed compilers support that `flags/*.txt` has no
decision on yet, run:

```bash
./generators/harvest-flags.py gcc clang
```

It queries the installed binaries themselves (`gcc --help=<section>`,
`clang --autocomplete=...`) — no network, no compiler sources needed, works
with Apple clang — and writes two files per compiler under `flag_report/`:
`<cc>-canonical.txt`, the full flag universe marked `[+]` included / `[-]`
excluded / `[?]` undecided, and `<cc>-new-flags.txt`, just the undecided
flags bucketed by which `flags/` file they belong in. An active line in
`flags/*.txt` means *include*; a commented line means *considered and
rejected* — to make a decision, move the flag into the right file in one of
those two forms, bump `version.txt`, and `generate-flags.sh` will probe
whether each choice actually works on each machine. Run it again after a
compiler upgrade to see what new checks became available.

## **Discovering common portable Unix functions**

To seed future functional wrapper libraries, use:

```bash
./generators/fetch-unix-doc-sources.sh -d /tmp/unix-doc-sources
./generators/discover-common-unix-functions.py \
  --linux /tmp/unix-doc-sources/linux-man-pages \
  --freebsd /tmp/unix-doc-sources/freebsd-src \
  --macos /tmp/unix-doc-sources/apple-Libc \
  --posix-symbols /path/to/posix-symbols.txt \
  --posix-html /path/to/posix/functions/toc.html \
  --output /tmp/unix-doc-sources/common-unix-functions.csv \
  --emit-probes /tmp/unix-doc-sources/probe-work
```

The analyzer computes:

```text
documented(Linux) ∩ documented(FreeBSD) ∩ documented(macOS)
  - POSIX interfaces
  - existing p101 wrappers
  - built-in legacy/unsafe exclusions
```

The CSV is only a wrapper backlog. Treat the generated compile probes as the
real gate: copy `/tmp/unix-doc-sources/probe-work` to macOS, FreeBSD, and
Linux machines and run:

```bash
cd /tmp/unix-doc-sources/probe-work
CC=cc ./run-probes.sh
```

Only functions that compile and link on all target systems should be promoted.
Assign each accepted wrapper to its functional owner (`lib_io`, `lib_network`,
`lib_process`, and so on); standards provenance belongs in its API manifest.
Documentation finds the goblins; compiler probes decide which goblins are real
functions.

Every accepted wrapper must also acquire a unit-test row. This is a
workspace-wide contract, including `lib_c`, `lib_c_facts`, `lib_convert`,
`lib_fsm`, and `lib_util` as well as the functional wrapper libraries.
`wrapper-errno-contract.json` is the single source of truth for failure codes.
It retains the POSIX.1-2024 `shall fail` and `may fail` sets separately, plus
documented Linux, macOS, and FreeBSD overrides. A platform manual replaces the
POSIX set when that manual exists; otherwise the portable POSIX set is used.
References such as “the errors specified for socket() and malloc()” are
resolved transitively and retained in the JSON. Every platform record names its
`effective_source_kind` (`platform-manual` or `posix-fallback`) and exact
`effective_source`. Archive- and SDK-backed manuals also retain a separate
`effective_source_path`; this avoids manufacturing invalid URLs by appending an
internal manual path to an archive URL. A test receipt therefore never leaves
the selected authority or page implicit. The top-level `platform_coverage`
summary reports manual overrides and POSIX fallbacks for both the complete
function catalogue and the active wrapper inventory.

The checked-in catalogue is refreshed from the Open Group function index and
`<errno.h>` page, kernel.org Linux man-pages, the active Xcode SDK manuals, and
the official FreeBSD manual archive. The refresh command deliberately consumes
local source snapshots so ordinary builds and CI never depend on the network:

```bash
./generators/refresh-wrapper-errno-contract.py \
  --index /path/to/posix/idx/functions.html \
  --errno-page /path/to/posix/basedefs/errno.h.html \
  --page-dir /path/to/posix/functions \
  --platform macos \
  --man-root "$(xcrun --show-sdk-path)/usr/share/man" \
  --platform-source "xcode-sdk://macosx-<version>/usr/share/man"
```

Run the same command with `--platform linux` or `--platform freebsd` and the
corresponding manual root to update those layers without discarding the other
two. Regenerate the deterministic injected-failure cases and validate the
complete contract:

```bash
./generators/generate-wrapper-unit-tests.py --clang clang
./tests/test-wrapper-errno-contract.py
./checks/check-wrapper-unit-tests.py
```

For each fault-capable wrapper, the generator injects every error in the active
platform's effective set and verifies that the exact code reaches
`p101_error`. An interface with no documented errno still receives one `EIO`
injection smoke case; that case is labeled by the empty manual set rather than
misrepresented as a documented failure. Wrappers without an injectable failure
boundary are assigned to an existing behavior test or the owning library's
handwritten `test/test_behavior.c`.

There is no behavior-test exemption for a native interface with a documented
failure on any supported platform. Such a wrapper must expose the injectable
error boundary and must have a generated fault test. The static check validates
the Linux, macOS, and FreeBSD arrays on every host, so a missing FreeBSD case is
caught on macOS rather than deferred to FreeBSD CI. The runtime suite then
executes the selected host array. The check requires every public manifest API
to be invoked by exactly one compiled test source, validates all 1,099 JSON
bindings, requires all three platform records, and compares every generated
error array with the catalogue. `test.sh` and the three-platform CI matrix are
the executable receipts.

The executable 10x contract replays every library test suite with call and
resource logging enabled:

```bash
./checks/check-wrapper-conformance.py -o /tmp/p101-wrapper-conformance
```

It combines `api-manifest.tsv`, `unit-test-manifest.tsv`, and the
Clang-derived capability receipt, then requires every env-aware public API to
appear with balanced runtime ENTER/EXIT records. Fault-capable APIs must be
wired to their deterministic failure tests. Optional argument/result logging
is admitted by `wrapper-conformance-contract.json`.

The 11x lifecycle layer exercises interactions rather than isolated calls:

```bash
./checks/check-wrapper-lifecycles.py -o /tmp/p101-wrapper-lifecycles
```

`wrapper-lifecycle-contract.json` defines deterministic state machines for
allocation, descriptors, streams, mappings, mutexes, processes, threads, and
short I/O. The runner generates replay sequences, walks error/EINTR/timeout
and short-I/O disruptions to exhaustion, validates the v4 event stream through
`lib_tool_event`, rejects resource leaks with `p101-resource-tracker`, and
shrinks any failure to a replayable minimal sequence. Its JSON receipt records
the seed, compiler, platform, replay, and fault index.

`--posix-symbols` accepts a simple newline list. `--posix-html` accepts a local
POSIX function TOC or individual function page; if sibling function pages are
present locally, the analyzer reads their `NAME` blocks too. That matters
because POSIX groups related interfaces on one page, so an index-only list can
miss names such as formatted I/O variants.

By default, the analyzer also excludes old or unsafe interfaces such as
`mktemp`, old BSD signal-mask APIs, obsolete byte/string aliases, `getw`/`putw`,
`getpass`, and historical globals. Use `--include-all` to see them in the CSV as
`excluded-legacy`, or `--no-default-excludes` if you intentionally want the raw
set.
