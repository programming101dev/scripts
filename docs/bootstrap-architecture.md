# Workspace bootstrap architecture

The workspace has one deliberate trust boundary: a host C compiler, CMake, and
libclang build the minimum runtime and tool executables before any p101 tool is
allowed to judge source. The tools never participate in compiling their own
dependencies.

| Stage | CMake target | Admitted input | Output |
| --- | --- | --- | --- |
| 0 | configure | compiler, CMake, libclang, checked-out source | one exact CMake graph |
| 1 | `p101_host_runtime` | 15 declared library repositories | 16 in-tree runtime targets |
| 2 | `p101_host_tools` | runtime targets and three program repositories | 10 in-tree tool executables |
| 3 | `p101_tool_qualification` | those exact executables | smoke/semantic tests and `host-tool-qualification.json` |
| 4 | `p101_acceptance` | qualified tool paths and the governed policy graph | strict workspace receipts and summary |

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
pair, prepares shared repositories and flag caches once, then launches every
compiler pair concurrently. Pair workers use explicit configuration lanes
whose identity covers both compiler fingerprints, probed flags, instrumentation
modes, and caller flags; they never consume the shared `.last-build-dir` marker
for dependency resolution. Link and runtime search are closed over that exact
lane, so incompatible profiling and compiler runtimes can coexist. Their
output is isolated under `target/update-all/<run-id>/`, with a deterministic
TSV/Markdown summary and complete ordered failure logs. Only after every pair
passes does a serialized finalization phase publish host markers and install
host runtime artifacts. The host pair then configures `workspace/CMakeLists.txt`
and builds `p101_acceptance`. `--skip-acceptance` is the explicit bring-up
escape hatch; strict acceptance is the default.

GitHub Actions uses this target once. It no longer runs a second
`check-after-update-all.sh` pass after `update-all.sh`; the compatibility script
is the policy runner invoked from `p101_workspace_checks`. Release preflight
also invokes the same target with a candidate repository lock and cache reuse
disabled.

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
- Stage 4 still contains mature shell/Python policy nodes. They are invoked
  behind the CMake target while they are incrementally replaced; CMake owns
  their ordering and exact host-tool identities, not their internal policy.
- External compiler, CMake, libclang, system headers, and operating-system
  behavior remain trusted inputs and are covered by the platform CI matrix.
- A warm incremental replay proves exact reuse only for the current workspace,
  toolchain, policy, and workload identity. It says nothing about a different
  checkout or machine.

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
