# Workspace bootstrap architecture

The workspace has one deliberate trust boundary: a host C compiler, CMake, and
libclang build the minimum runtime and tool executables before any p101 tool is
allowed to judge source. The tools never participate in compiling their own
dependencies.

| Stage | CMake target | Admitted input | Output |
| --- | --- | --- | --- |
| 0 | configure | compiler, CMake, libclang, checked-out source | one exact CMake graph |
| 1 | `p101_host_runtime` | 15 declared library repositories | 16 in-tree runtime targets |
| 2 | `p101_host_tools` | runtime targets and three program repositories | 12 in-tree tool executables |
| 3 | `p101_tool_qualification` | those exact executables | smoke/semantic tests and `host-tool-qualification.json` |
| 4 | `p101_acceptance` | selected workspace level and qualified tool paths | build-only, qualification, or strict workspace receipts |

The same numeric level controls every repository and the aggregate graph:

| Level | Repository contract | Aggregate contract |
| ---: | --- | --- |
| `1` | validate formatting and compile/install primary targets | compiler matrix and installation only |
| `2` | level 1 plus standalone unit tests | build and qualify the exact in-tree host tools |
| `3` | level 2 plus the complete analyzer pipeline | invoke the governed `check-after-update-all.sh` graph and incremental replay |

An independently configured repository, `workspace/CMakeLists.txt`, and
`update-all.sh` all default to level 1. Maintainers and CI select level 2 or 3
explicitly. The selected level passes into both the repository lanes and the
workspace graph and is part of the lane identity, so evidence from a shallower
contract cannot satisfy a deeper one.

This breaks the apparent cycle. The compiler builds the mechanism first;
qualified mechanism then evaluates the libraries and tools, including its own
source. No host library is installed, no target is resolved from an old
repository build directory, and no acceptance check is permitted to silently
substitute a different audit, test, inspection, event-model, or receipt binary.

The graph intentionally uses a small bootstrap policy: the selected language
standard, platform feature macros, and `-Werror`, without sanitizer or probed
warning bundles. The compiler matrix has already exercised the full repository
build policies. Repeating those policies while merely constructing the judges
would add time and a second source of bootstrap failures without strengthening
the final judgment.

`update-all.sh` selects the first declared usable compiler pair as the host
pair, prepares shared repositories and flag caches once per operating system,
then launches every compiler pair concurrently. Repository refresh, version
comparison, compiler discovery, flag generation, distribution, and formatting
belong only to that serialized preparation phase. Pair workers repeat only
compiler-specific smoke tests, target-specific compound sanitizer filtering,
and builds. GitHub Actions does not pre-clone or pre-discover; each platform
job enters this same boundary exactly once. Flag preparation is intentionally
not shared between Linux, macOS, and FreeBSD because their SDK, linker, target,
and sanitizer capabilities differ. Pair workers use explicit configuration lanes
whose identity covers both compiler fingerprints, probed flags, instrumentation
modes, and caller flags; they never consume the shared `.last-build-dir` marker
for dependency resolution. Link and runtime search are closed over that exact
lane, so incompatible profiling and compiler runtimes can coexist. Their
output is isolated under `target/update-all/<run-id>/`, with a deterministic
TSV/Markdown summary and complete ordered failure logs. Only after every pair
passes does a serialized finalization phase publish host markers and install
host runtime artifacts. At explicitly selected levels 2 and 3, the host pair
then configures `workspace/CMakeLists.txt` and builds `p101_acceptance`.
`--skip-acceptance` is the explicit bring-up escape hatch for those levels;
level 1 is the default and stops after formatting, building, and installation.

Formatting follows repository refresh and formatter validation but precedes
flag generation, shared distribution, source identity, cache-key computation,
configuration, and compilation. Local preparation applies it and continues.
CI and release preflight select `--format-check`, which reports drift with
clang-format's dry-run mode without changing tracked bytes. `--no-format` is
an explicit local escape hatch rather than the default.

The format pass and the level-3 compile-database mechanics do not depend on
Python or on a p101 library. `cmake/p101_compile_db.c` is a C17 bootstrap
program using only libc/POSIX. The workspace format entry point compiles it
into `target/bootstrap` before any p101 tool is available; repository CMake
trees compile the same source as an isolated custom artifact for compile-DB
sanitizing, per-translation-unit analysis, and optional CTU analysis. Keeping
it out of the normal target list prevents the bootstrap translation unit from
contaminating project facts, coverage, cppcheck, or profiling evidence.

Repository build reuse is deliberately below the judgment boundary.
`update-all.sh` defaults `P101_REPOSITORY_BUILD_CACHE` to
`target/repository-build-cache`; an absolute override moves it and `off`
disables it. Each physical CMake tree is addressed by the compiler lane plus a
full digest of the repository worktree and narrow shared build policy. The
repository path is only a compatibility symlink. A changed identity therefore
creates a new immutable lane instead of destructively repurposing a prior one.
Restoring such a tree never constitutes a pass: the repository is configured
again, CMake validates its dependency graph, and dependency-tracked compile,
analyze, clang-tidy, and cppcheck outputs rerun when their admitted
source/header, compile database, policy, or tool identity has changed.

Marker publication is transactional. Pair workers defer marker writes; after
all workers pass, one serialized host finalizer validates receipts, installs,
and atomically publishes the selected quality and runtime markers. A failed
configure, build, install, or partial publication restores the last marker only
when its target still exists; a stale incoming marker becomes absent. Cache
collection runs only after workers stop and finalization succeeds. It removes
unselected content-addressed convenience aliases immediately and unreferenced physical lanes after
`P101_BUILD_CACHE_MAX_AGE_DAYS` (30 by default). The tradeoff is bounded extra
storage and reliance on CMake's declared dependency graph in exchange for
avoiding unconditional clean builds. Direct repository builds retain
clean-first behavior; only the workspace orchestrator opts into this reuse.
Stable exact-lane aliases for all compiler pairs remain protected: downstream
checks resolve those names, whereas the installation markers identify only the
host pair. This prevents collection from making a valid non-host compiler lane
disappear between the matrix build and its acceptance pass.

GitHub Actions uses this target once. It no longer runs a second
`check-after-update-all.sh` pass after `update-all.sh`; the compatibility script
is the policy runner invoked from `p101_workspace_checks`. Release preflight
also invokes the same target with a candidate repository lock and cache reuse
disabled. After that target produces a clean, digest-valid receipt, preflight
writes `workspace-candidate.json`. The candidate binds the scripts revision,
every managed revision, each upstream base and target ref, the candidate lock,
candidate stack contract, compiler-matrix evidence, and the governed acceptance
receipt.

Publication consumes that candidate rather than rediscovering repository state.
It computes the affected set from the candidate's ahead-only rows, fetches and
validates every remote, and first stages exact commits under the deterministic
candidate ref. A synthetic scripts commit changes only `repos.lock` and its
derived stack contract, so the workflow at that ref clones the candidate's
exact managed commits. Linux,
macOS, and FreeBSD each emit a receipt bound to the same candidate ID, lock,
scripts commit, ref, and workflow run. Only the digest-valid aggregate unlocks
default-branch promotion. A changed local `HEAD`, evidence file, candidate lock,
temporary ref, acceptance digest, or remote base refuses the transaction.
Already-staged and already-promoted exact commits are recognized on replay, so
a network or platform failure is resumable with
`publish-workspace.sh --resume <candidate>`.

After all managed revisions land, scripts deterministically refreshes
`repos.lock` and the stack contract, commits only those two completion
artifacts, and publishes scripts last. `completion.json` binds that final
scripts revision, the published contract hashes, and the three-platform
qualification digest to the original candidate. Temporary refs are deleted
only when they still identify the expected candidate commits.
Generation and formatting still happen before candidate creation; if they
change tracked bytes, preflight stops for review instead of committing unseen
content.

The tool-qualification receipt is an actual CMake output. If neither a host
tool nor one of its runtime dependencies changed, CMake retains the receipt and
does not rerun the 23 qualification tests. Workspace policy retains its
content-addressed evidence cache because source identity, policy identity,
tool identity, outputs, and result are all verified before reuse.

Every acceptance invocation performs one second, isolated incremental replay
from the full receipt. `contracts/p101-performance-budget.json` bounds both the
full graph and incremental replay and requires the latter to reuse at least 50
of the governed nodes. The resulting `performance-receipt.json` records the
two elapsed times and reuse count. These are deliberately generous portability
ceilings, not benchmark claims.

## Blind spots

- Stage 3 proves only CLI availability, option rejection, and the declared
  native semantic regressions. It does not prove workspace policy.
- Stage 4 still contains mature shell/Python orchestration, generation,
  repository-transaction, and dynamic-report nodes. Five source-policy
  processes have moved behind the native `audit-workspace` engine. The
  remaining Python boundary is deliberate: dynamic graph scheduling, source
  generation, external-manual harvesting, HTML/report generation, and
  repository transactions are not made safer or faster by a mechanical C
  rewrite. CMake owns their ordering and exact host-tool identities, not their
  internal policy.
- External compiler, CMake, libclang, system headers, and operating-system
  behavior remain trusted inputs and are covered by the platform CI matrix.
- A warm incremental replay proves exact reuse only for the current workspace,
  toolchain, policy, and workload identity. It says nothing about a different
  checkout or machine.
- Repository build caches cannot detect an undeclared generated input. They
  accelerate the same CMake graph but do not strengthen its completeness; the
  clean compiler/platform matrix remains the portability evidence.
- Independent Git repositories cannot be updated or rolled back as one remote
  transaction. The candidate gives exact, fail-closed, resumable publication;
  it does not claim cross-repository rollback.

## Replayable evidence

Configure and qualify the host tools without running the full workspace gate:

```sh
cmake -S workspace -B target/workspace/smoke \
  -DCMAKE_C_COMPILER=clang
cmake --build target/workspace/smoke --target p101_tool_qualification --parallel
```

Run the full stable target after a completed compiler matrix:

```sh
cmake -S workspace -B target/workspace/clang \
  -DCMAKE_C_COMPILER=clang \
  -DP101_ACCEPTANCE_CXX_COMPILER=clang++
cmake --build target/workspace/clang --target p101_acceptance --parallel
```

The focused negative and configuration controls are in
`tests/test-workspace-cmake.sh`.
