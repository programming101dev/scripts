# p101 causal rule packs

Rule packs turn the facts in a completed p101 analysis into a small,
course-specific policy gate:

```bash
./p101 check /tmp/student-run.analysis --rules resource-clean
./p101 check /tmp/student-run.analysis \
    --rules resource-clean --rules concurrency
./p101 check /tmp/student-run.analysis --rules secure-c --json
```

The built-in packs are `resource-clean`, `concurrency`, and `secure-c`.
`--rules` may instead name a JSON file. A clean result exits 0, policy
violations exit 1, and invalid or incomplete evidence exits 2.

## Contract

A pack uses `p101-rule-pack-v1` and contains no executable code:

```json
{
  "schema": "p101-rule-pack-v1",
  "name": "example",
  "rules": [
    {
      "id": "P101-POLICY-EXAMPLE-001",
      "kind": "forbid-finding",
      "pattern": "P101-FD-*",
      "title": "Descriptor ownership is not clean",
      "lesson": "docs/rule-packs.md#descriptor-ownership"
    }
  ]
}
```

Rule IDs must be unique within a pack. Supported kinds are:

- `forbid-finding` and `require-finding`, matched against stable diagnostic
  IDs;
- `forbid-call` and `require-call`, matched against observed call-enter names;
- `require-edge`, matched against causal edge kinds;
- `require-resource`, matched against observed resource classes.

Patterns use shell-style wildcards. Packs are bounded to 1 MiB and 1024 rules,
and each violation reports at most 20 evidence identities.

## Descriptor ownership

Every successfully acquired descriptor has one owner, is released exactly
once, and is deliberately retained or marked close-on-exec at an execution
boundary.

## Allocation ownership

Every allocation has one owner and one matching release. Replacement must
preserve the old allocation when reallocation fails.

## Generic resource ownership

Every successful acquisition is paired with the matching release or an
explicit ownership transfer on every path.

## Synchronization discipline

Shared state follows one documented synchronization discipline. Threads,
locks, and condition variables have explicit lifecycle ordering.

## Security boundary

Resource and synchronization findings are security-relevant when ownership,
memory integrity, or descriptor inheritance crosses a trust boundary.

## Blind spots

Rules can judge only the validated findings and causal facts present in the
analysis bundle. Direct libc calls, third-party internals, kernel-only
activity, and missing wrapper events remain invisible. A clean result is
evidence that the selected policies passed over the admitted observations; it
is not proof that the program is secure.

## Evidence

The deterministic rule engine is covered by `tests/test-p101-model.py`. The
stack-level behavior corpus exercises clean, leak, double-release, generic
resource, execution-inheritance, and malformed-protocol cases.
