# Functional wrapper library architecture

The portable wrapper surface is owned by functional repositories, not by the
standard that first documented an interface.

## Contract

- A public wrapper has exactly one functional owner.
- The installed API is available with compatible semantics on Linux, macOS,
  and FreeBSD.
- POSIX, XSI, optional-POSIX, and common-Unix origins are provenance metadata
  in each `api-manifest.tsv`; they do not select the repository.
- Each functional library has one installed public header,
  `include/p101_<domain>/<domain>.h`, and one implementation translation unit,
  `src/<domain>.c`. The source tree does not repeat standards provenance.
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

Functional ownership creates more repositories, makes some programs link
several small libraries, and creates larger translation units inside the
domain repositories. In return, students can discover an API by purpose,
dependencies describe actual capabilities, platform availability is explicit,
private implementation pieces can cooperate without artificial standards
boundaries, and standards provenance can change without moving code.

The single-source rule deliberately trades fine-grained incremental
compilation for a smaller and more truthful teaching surface. These libraries
are small enough that the compilation cost is acceptable. If a domain grows
large enough to invalidate that tradeoff, it should split by a real functional
boundary rather than by standards origin.

The former `lib_posix`, `lib_posix_optional`, `lib_posix_xsi`, and `lib_unix`
repositories are retained only as migration history. They and their wrapper
example repositories are absent from `repos.txt`.

## Evidence

`contracts/wrapper-library-map.tsv` is the central ownership receipt. Every functional
library carries its own `api-manifest.tsv`. Run:

```sh
"${P101_AUDIT_WORKSPACE:?set this to the qualified audit-workspace}" \
    --policy functional-library-split --workspace .. --scripts-root .
./checks/check-wrapper-unit-tests.py
./checks/check-p101-library-audit.sh
./tests/test-cmake.sh -c clang -x clang++
```

The native workspace policy rejects duplicate or missing ownership, anything other than
one domain header and one domain source, obsolete standards-origin source
directories, stale source/header paths, unsupported platform rows, active
references to retired headers or link targets, and drift between the central
and per-library manifests. The second rejects wrappers without a unique
compiled and invoked test case.

Wrapper unit tests establish the wrapper boundary and representative success or
injected-failure behavior. They do not prove every kernel state, timing
interleaving, locale database, or external service behavior; the platform CI
matrix and playgrounds exercise those broader integration boundaries.
