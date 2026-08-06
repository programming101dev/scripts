# p101-c-facts external corpus

This opt-in suite checks `lib_c_facts` against source shapes that the p101
workspace does not contain. It uses 50 cases in five equal cohorts:

- mature C projects;
- mature C++ projects;
- intentionally defective C cases from the Juliet suite;
- intentionally defective C++ cases from the Juliet suite;
- International Obfuscated C Code Contest winners.

The manifest pins every upstream tree to a full commit. The runner fetches those
trees into a disposable cache; third-party code is not copied into this
repository. Mature projects contribute a deterministic, sorted sample of at
most 20 implementation files. The stress cohorts name exact files.

Run the complete corpus:

```sh
./checks/check-c-facts-external-corpus.sh -o /tmp/p101-c-facts-external
```

Run a bounded slice while developing:

```sh
./checks/check-c-facts-external-corpus.sh --cohort ioccc \
  -o /tmp/p101-c-facts-ioccc
./checks/check-c-facts-external-corpus.sh --case curl \
  -o /tmp/p101-c-facts-curl
```

After one online run, replay from the pinned cache without network access:

```sh
./checks/check-c-facts-external-corpus.sh --offline \
  -o /tmp/p101-c-facts-offline
```

## Contract

Admitted inputs are the checked-in case and parser-context TSV manifests, the
pinned Git trees, the selected source files, and the configured
`p101-c-facts` executable. The parser-context manifest records project compile
definitions needed to make a sampled translation unit meaningful rather than
hiding them in the runner. Outputs are `results.tsv`, `summary.md`, and one
directory per case containing the exact source list, raw P101FACT v6 records,
and parser diagnostics.

A mature-project case passes when fact acquisition emits file facts and
produces at least three function, call, or type facts per selected source. That
deliberately modest semantic-density floor catches a vacuous scan that merely
opens files. A project that needs generated headers or fuller build context may
be `PASS-PARTIAL`; its Clang errors remain in the case diagnostics and in the
summary count rather than being hidden. Hostile cases have the same allowance
for the documented partial status, but must emit facts and must never terminate
unexpectedly.
The IOCCC cohort is selected by measured preprocessor density and must
collectively exercise source-level macro definitions. `results.tsv` reports
both source macro definitions and macro records emitted by the facts contract.

The runner derives builtin-header and macOS SDK arguments from a configurable
Clang driver (`--clang` or `P101_C_FACTS_CLANG`). This is a parser resilience
and fact-acquisition receipt, not a proof that the selected projects build or
that every construct in them was modeled. Samples do not replace each upstream
project's compile database, generated headers, or build configuration. Missing
build-generated context can reduce semantic precision even when libclang
produces a usable translation unit. Revisions should be updated deliberately
and reviewed like test-fixture changes.

The lightweight manifest test does not access the network:

```sh
./tests/test-c-facts-external-corpus.sh
```
