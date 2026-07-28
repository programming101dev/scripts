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

Ensure the scripts are executable:

```bash
chmod +x *.sh
```

## **Prerequisites**

To ensure you have all of the required tools installed, run:
```bash
./check-env.sh
```

If you are missing tools follow these [instructions](https://docs.google.com/document/d/1ZPqlPD1mie5iwJ2XAcNGz7WeA86dTLerFXs9sAuwCco/edit?usp=drive_link).

To determine which compilers you have installed on your system, run:
```bash
./check-compilers.sh
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
./update.sh -c <c compiler> -x <c++ compiler>
```

## **Running the update-all.sh Script**

If you want to verify that everything compiles with all of the supported compilers, run:

```bash
./update-all.sh
```

After `update-all.sh` succeeds, run the post-build acceptance checks:

```bash
./check-after-update-all.sh
```

This does not rebuild every repository again. It runs the shared CMake
regression harness, fresh-template standalone checks, and the
`p101-tool-playground` tour, then runs the small p101 behavior regression
corpus.

For a shorter behavior-only gate, run:

```bash
./check-p101-regression-corpus.sh
```

For the student-facing tool workflow, use the dispatcher:

```bash
./p101 check -s src -- ./build-clang/my-program input.txt
```

`p101 check` writes one report directory with the project quality gate
(`check.sh` when present), wrapper audit, module map, observed resource/call
logs, correlated findings, error-path walking, optional coverage, a
self-contained `index.html`, and a bug bundle.

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

For lower-level student/instructor tooling around observed runs:

```bash
./p101 html-report /path/to/p101-observe-output
./p101 bug-bundle /path/to/p101-observe-output
./p101 cohort submission-*/correlated-report.json
```

The p101 tools follow a lightweight design contract: bounded inputs, explicit
blind spots, deterministic receipts, shared mechanisms, and small public APIs.
See [docs/p101-tool-design-contract.md](docs/p101-tool-design-contract.md).
To check that each `p101-*` README exposes the minimum contract surface, run:

```bash
./check-p101-tool-contracts.sh
./p101 contracts
```

To replay the broader p101 tool audit — README contract checks, strict
wrapper-audit checks over the C tools, and module-map design reports — run:

```bash
./check-p101-tool-audit.sh
./p101 tool-audit
```

By default, module-map design notes are reported but do not fail the audit. Use
`--fail-module-notes` when intentionally ratcheting the p101 tools toward the
current module-splitting rules.

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
`.github/workflows/p101-stack.yml`; `./check-github-actions-template.sh` and
`./check-after-update-all.sh` fail if the starter copy drifts from the live CI
workflow. The workflow can be dispatched for all platforms or one target OS
(`linux`, `macos`, or `freebsd`) when you only need to rerun a single leg.

`scripts/CMakeLists.txt` is the source of truth for the shared C/C++ build
pipeline. After editing it, run `./copy-cmake.sh` and commit the copied files in
the affected repos. `./check-cmake-distribution.sh` and
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
./test-cmake.sh
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
./check-templates-standalone.sh
```

It copies `template-c`, `template-c-program`, and `template-cxx` to `/tmp`,
rejects hidden parent-workspace script dependencies, and configures, builds,
and tests each fresh project instance.

To run the broader p101 stack ratchet, use:

```bash
./check-p101-stack.sh -c clang -x clang++
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
./harvest-flags.py gcc clang
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

## **Discovering common non-POSIX Unix functions**

To seed future `lib_unix` wrappers, use:

```bash
./fetch-unix-doc-sources.sh -d /tmp/unix-doc-sources
./discover-common-unix-functions.py \
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

Only functions that compile and link on all target systems should be promoted
to `lib_unix` candidates. Documentation finds the goblins; compiler probes
decide which goblins are real functions.

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
