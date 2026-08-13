# Functional-library split migrations

The migration from the retired standards-based libraries (`lib_posix`,
`lib_posix_optional`, `lib_posix_xsi`, and `lib_unix`) is complete. Its
one-shot migration programs were removed so they cannot recreate an obsolete
layout.

The maintained architecture is:

- functional repositories own APIs by purpose;
- public headers mirror the native C/POSIX/Unix header names;
- implementation sources mirror those public headers;
- standards provenance remains in `api-manifest.tsv`.

The original migration remains available in Git history. The executable
contract is the `functional-library-split` policy in
`../../programs/p101-audit/components/workspace/src/functional_layout.c`.
