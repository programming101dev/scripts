# Functional wrapper library architecture

The portable wrapper surface is owned by functional repositories, not by the
standard that first documented an interface.

## Contract

- A public wrapper has exactly one functional owner.
- The installed API is available with compatible semantics on Linux, macOS,
  and FreeBSD.
- POSIX, XSI, optional-POSIX, and common-Unix origins are provenance metadata
  in each `api-manifest.tsv`; they do not select the repository.
- Installed headers mirror the native C/POSIX/Unix API families, under the
  owning namespace (for example `include/p101_text/p101_regex.h`).
- C translation units mirror those public headers. A repository that owns more
  than one namespace keeps each target under `src/p101_<domain>/`; provenance
  such as POSIX, XSI, optional-POSIX, or Unix does not create source layers.
- Unsupported designs may be retained under `design/unsupported`, but they are
  neither compiled nor installed.
- Consumers include and link only functional libraries.
- Every public wrapper has exactly one entry in
  `test/unit-test-manifest.tsv` and is invoked by a compiled unit-test case.
  Fault-capable wrappers receive deterministic injected-failure tests;
  wrappers without that path receive handwritten behavior tests.
- The unit-test contract is workspace-wide. Libraries outside the functional
  wrapper split use the same API and test manifests, and the instrumentation
  audit rejects public definitions missing from those manifests.

The active domains are I/O, filesystem, memory, process, thread,
synchronization, IPC, network, terminal, time, identity, text, locale, math,
search, dynamic linking, diagnostics, database, CLI, random, and host
information.

## Tradeoff

Functional ownership makes some programs link several small targets. The
native-shaped files add a little structure, but students can find a wrapper by
the header where the native function is declared and can inspect its matching
implementation file. Repository consolidation is used only where the runtime
concepts share a lifecycle and dependency boundary: thread/synchronization,
text/locale, and math/random. Each public namespace and CMake target remains
separate inside its consolidated repository.

The former `lib_posix`, `lib_posix_optional`, `lib_posix_xsi`, and `lib_unix`
repositories are retained only as migration history. They and their wrapper
example repositories are absent from `repos.txt`.

## Evidence

`contracts/wrapper-library-map.tsv` is the central ownership receipt. Every functional
library carries its own `api-manifest.tsv`. Run:

```sh
"${P101_AUDIT_WORKSPACE:?set this to the qualified audit-workspace}" \
    --policy functional-library-split --workspace .. --scripts-root .
"${P101_AUDIT_WORKSPACE:?set this to the qualified audit-workspace}" \
    --policy wrapper-unit-tests --workspace .. --scripts-root .
./checks/check-p101-library-audit.sh
./tests/test-cmake.sh -c clang -x clang++
```

The native workspace policy rejects duplicate or missing ownership,
non-native-shaped headers or source files, obsolete standards-origin source
directories, stale source/header paths, unsupported platform rows, active
references to retired headers or link targets, and drift between the central
and per-library manifests. The second rejects wrappers without a unique
compiled and invoked test case.

Wrapper unit tests establish the wrapper boundary and representative success or
injected-failure behavior. They do not prove every kernel state, timing
interleaving, locale database, or external service behavior; the platform CI
matrix and playgrounds exercise those broader integration boundaries.
