# Unpublished workspace change audit — 2026-08-08

## Scope and evidence

This is a disposition record for the 186 commits that were ahead of upstream
when stabilization began. The commits were spread across 43 repositories; 41
repositories also had uncommitted generated `test.sh` drift, and 42 managed
HEADs differed from `repos.lock`.

Every ahead repository was preserved at the local branch
`audit/pre-stabilization-2026-08-08` before stabilization edits began. This
receipt classifies commit cohorts; it does not claim that the code in a `keep`
cohort has passed the final cross-platform gate.

## Disposition

| Commits | Disposition | Reason |
| ---: | --- | --- |
| 38 | keep | The original Bash 3.2 guarded-array change is a focused portability fix. |
| 38 | drop | The first fleet-wide module-map rollout introduced lexical purpose inference and reverted the Bash fix. Its useful loader changes are retained by later focused work. |
| 42 | rework | The second fleet-wide rollout mixed rule withdrawal, loader fixes, and generated script changes under one message. Preserve useful bytes, but replace these commits with focused repository commits. |
| 3 | keep | The `p101_fsm_state_id` rename follows the POSIX `_t` namespace rule and was propagated to consumers. |
| 8 | keep | FSM environment/tracing signature propagation is internally consistent; retain subject to canary tests. |
| 8 | keep | P101FACT v7 constant propagation removes duplicated protocol literals. |
| 1 | keep | Standalone public-header compilation is a deterministic boundary check. |
| 1 | rework | `release.sh` is useful as orchestration, but automatic source commits and lock deletion are removed by stabilization. |
| 1 | drop | The prior release dry-run accommodation accepted dirty repositories and did not validate the release candidate. |
| 15 | rework | Shape/naming idiom work is split: AST/USR-based structure checks stay; lexical ownership and predicate-purpose claims are withdrawn; unavoidable vocabulary rules are labeled as naming conventions. |
| 31 | keep | Focused type, bool, fact-loader, test-shard, include-cycle, and conformance repairs remain, subject to repository tests. |

Total: **186 commits**.

## Stabilization boundary

Publication must now satisfy these properties before any push:

1. every managed repository and `scripts` is clean;
2. release code never runs `git add -A` or creates source commits;
3. release code never deletes Git lock files;
4. dry-run and real publication admit the same committed revisions;
5. generated shared files are distributed once, after their owning contract
   and canary tests pass;
6. `repos.lock` and the stack contract are refreshed only after reviewed
   repository commits exist.

## Blind spots

This classification is based on commit messages, diffs, rule contracts, and
current workspace topology. Final `keep` status still depends on the macOS,
Linux, and FreeBSD gates. The local archive branches are not remote backups.

## Stabilization result — 2026-08-09

The unpublished history was replaced by focused local commits after every
original branch was preserved. During verification, the audit found and fixed
five defects that were independent of formatting or warning churn:

1. malformed `lib_tool_event` records could reach payload fields before their
   arity was validated, producing a crash or use-after-free;
2. the shared JSON decoder rejected valid uppercase hexadecimal escapes;
3. `lib_c_facts` lacked executable conformance evidence for one public outcome;
4. the `p101-error-path-walk` test target omitted its `p101_record` dependency;
5. the `p101-doctor` CLI test used a whitespace expression with the wrong
   shell semantics.

The shared repository test runner was also corrected to use workspace-built
dependencies before installed copies. This removed a stale-install boundary
that could make template tests pass or fail against unrelated library bytes.

The semantic no-embedded-call contract initially reported 197 playground
violations. The playground now stores each call result before using it in a
condition, argument, cast, or return; loop reevaluation and short-circuit order
were preserved explicitly. Its unused public scenario query was removed, and
argument defaulting no longer uses the resource-owning `_init` convention.
The clean corpus case has a narrow, declared `module-map` exit amnesty for the
remaining warning that its scenario catalogue is intentionally a large module.
Runtime findings and error-contract findings are not covered by that amnesty.

### Final evidence

- 25/25 generator regression tests passed.
- 43 focused repository test executables passed.
- `lib_tool_event` strict build, unit tests, and fuzz targets passed.
- `lib_env` parser fuzzing passed.
- `p101-error-path-walk` and `p101-doctor` tests passed.
- The playground strict build and unit test passed.
- `p101 lab --quick --strict-corpus` passed: the clean case remained clean and
  `fd-leak` still produced `P101-FD-001`.
- `github-actions/preflight.sh` rebuilt all 46 repositories and passed the
  complete governed acceptance graph on macOS. Receipt:
  `/private/tmp/p101-stabilization-preflight-final-8/receipt.md`.

All managed worktrees were clean at the end of the receipt. Cross-platform
confidence still requires the authoritative Linux and FreeBSD CI jobs; this
local preflight intentionally does not claim otherwise.
