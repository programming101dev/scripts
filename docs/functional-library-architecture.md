# Functional wrapper library architecture

The portable wrapper surface is owned by functional repositories, not by the
standard that first documented an interface.

## Contract

- A public wrapper has exactly one functional owner.
- The installed API is available with compatible semantics on Linux, macOS,
  and FreeBSD.
- POSIX, XSI, optional-POSIX, and common-Unix origins are provenance metadata
  in each `api-manifest.tsv`; they do not select the repository.
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

Functional ownership creates more repositories and makes some programs link
several small libraries. In return, students can discover an API by purpose,
dependencies describe actual capabilities, platform availability is explicit,
and standards provenance can change without moving ownership.

The former `lib_posix`, `lib_posix_optional`, `lib_posix_xsi`, and `lib_unix`
repositories are retained only as migration history. They and their wrapper
example repositories are absent from `repos.txt`.

## Evidence

`contracts/wrapper-library-map.tsv` is the central ownership receipt. Every functional
library carries its own `api-manifest.tsv`. Run:

```sh
./checks/check-functional-library-split.py
./checks/check-wrapper-unit-tests.py
./checks/check-p101-library-audit.sh
./tests/test-cmake.sh -c clang -x clang++
```

The first command rejects duplicate or missing ownership, stale source/header
paths, unsupported platform rows, active references to retired headers or link
targets, and drift between the central and per-library manifests. The second
rejects wrappers without a unique compiled and invoked test case.

Wrapper unit tests establish the wrapper boundary and representative success or
injected-failure behavior. They do not prove every kernel state, timing
interleaving, locale database, or external service behavior; the platform CI
matrix and playgrounds exercise those broader integration boundaries.
