#!/usr/bin/env python3
"""bootstrap-selection.py — generate a flag selection from ZERO.

Proof that the system is self-hosting: given only what machines can
produce — flag-database.json (harvested universes) and flag-exclusions.txt
(family policy globs) — this generates a maximal selection with no
hand-curated lists at all.  Philosophy: every flag possible, unless policy
excludes it; probing then keeps what each binary actually accepts, the
whole-set check drops mutual exclusions, and lint-flags.py flags ordering
conflicts a bootstrap can't know about.

What CAN'T be derived and where it comes from instead:
  value choices     a flag like -fstrub= needs a chosen value; the small
                    KNOWN_VALUES recipe map below carries those decisions,
                    everything else lands in the needs-value report
  combo recipes     cfi needs -flto in the same probe unit; carried in
                    KNOWN_COMBOS
  ordering wisdom   -Wshadow= variants must end on the widest; bootstrap
                    emits alphabetical order and lint-flags.py catches the
                    downgrades for a human to reorder
  judgment          the 300+ individually-excluded flags (defaults
                    restated, UB-widening optimizations) are decisions in
                    flag-selection.json; from zero they come back enabled
                    until re-judged via the harvest worklists

Usage:
  ./bootstrap-selection.py                  # -> flag-selection.bootstrap.json
  ./bootstrap-selection.py --compare        # also diff against the real
                                            #    flag-selection.json
It NEVER overwrites flag-selection.json.
"""

import fnmatch
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(SCRIPT_DIR, "flag-database.json")
EXCLUSIONS_FILE = os.path.join(SCRIPT_DIR, "flag-exclusions.txt")
SELECTION = os.path.join(SCRIPT_DIR, "flag-selection.json")
OUT = os.path.join(SCRIPT_DIR, "flag-selection.bootstrap.json")

# Value-taking flags where a specific value is the decision.
KNOWN_VALUES = {
    # warnings: always the strongest level
    "-Wformat=": "-Wformat=2",
    "-Wformat-overflow=": "-Wformat-overflow=2",
    "-Wformat-truncation=": "-Wformat-truncation=2",
    "-Wimplicit-fallthrough=": "-Wimplicit-fallthrough=5",
    "-Warray-bounds=": "-Warray-bounds=2",
    "-Warray-parameter=": "-Warray-parameter=2",
    "-Wshift-overflow=": "-Wshift-overflow=2",
    "-Wstrict-aliasing=": "-Wstrict-aliasing=3",
    "-Wattribute-alias=": "-Wattribute-alias=2",
    "-Wcast-align=": "-Wcast-align=strict",
    "-Wcatch-value=": "-Wcatch-value=3",
    "-Wuse-after-free=": "-Wuse-after-free=3",
    "-Wstringop-overflow=": "-Wstringop-overflow=4",
    "-Wnormalized=": "-Wnormalized=nfkc",
    "-Wbidi-chars=": "-Wbidi-chars=unpaired,any,ucn",
    "-Wplacement-new=": "-Wplacement-new=2",
    "-Waligned-new=": "-Waligned-new=all",
    "-Wleading-whitespace=": "-Wleading-whitespace=blanks",
    "-Wtrailing-whitespace=": "-Wtrailing-whitespace=blanks",
    "-Wsuggest-attribute=": None,  # additive: expanded below
    "-fanalyzer-checker=": "-fanalyzer-checker=taint",
    "-fsanitize-address-use-after-return=": "-fsanitize-address-use-after-return=always",
    "-fcf-protection=": "-fcf-protection=full",
    "-fstrub=": "-fstrub=all",
    "-fstrict-flex-arrays=": "-fstrict-flex-arrays=3",
    "-fcallgraph-info=": "-fcallgraph-info=su,da",
    "-mbranch-protection=": "-mbranch-protection=standard",
    "-fvtable-verify=": "-fvtable-verify=std",
    "-fprofile-update=": "-fprofile-update=prefer-atomic",
    "-fprofile-reproducible=": "-fprofile-reproducible=multithreaded",
}

# Probe units that only work as a group.
KNOWN_COMBOS = [
    "-gdwarf-5 -gembed-source",
]

# -fsanitize= categories -> selectable group file. The compiler's help
# output shows -fsanitize= as ONE value-taking option; the value space is
# probed separately (harvest-flags.py sanitize-values), and this mapping
# assigns each category to its group. Probing keeps what each binary
# accepts, so enabling everything here is safe.
SANITIZE_GROUPS = {
    "address_sanitizer_flags.txt": [
        "address", "pointer-compare", "pointer-subtract"],
    "hwaddress_sanitizer_flags.txt": ["hwaddress"],
    "leak_sanitizer_flags.txt": ["leak"],
    "memory_sanitizer_flags.txt": ["memory"],
    "thread_sanitizer_flags.txt": ["thread"],
    "dataflow_sanitizer_flags.txt": ["dataflow"],
    "pointer_overflow_sanitizer_flags.txt": ["pointer-overflow"],
    "safe_stack_sanitizer_flags.txt": ["safe-stack"],
    "shadow_call_stack_sanitizer_flags.txt": ["shadow-call-stack"],
    "type_sanitizer_flags.txt": ["type"],
    "realtime_sanitizer_flags.txt": ["realtime"],
    "numerical_sanitizer_flags.txt": ["numerical"],
    "undefined_sanitizer_flags.txt": [
        "undefined", "shift", "shift-base", "shift-exponent",
        "integer-divide-by-zero", "unreachable", "vla-bound", "null",
        "return", "signed-integer-overflow", "bounds", "bounds-strict",
        "array-bounds", "local-bounds", "alignment", "float-divide-by-zero",
        # NOTE: "object-size" omitted — inert at -O0 (clang warns under -Werror)
        "float-cast-overflow", "nonnull-attribute",
        "returns-nonnull-attribute", "bool", "enum", "vptr", "builtin",
        "function", "unsigned-integer-overflow",
        "unsigned-shift-base", "implicit-conversion",
        "implicit-signed-integer-truncation",
        "implicit-unsigned-integer-truncation",
        "implicit-integer-sign-change", "implicit-bitfield-conversion",
        "nullability-arg", "nullability-assign", "nullability-return",
        "objc-cast"],   # object-size intentionally absent, see NOTE above
    "cfi_sanitizer_flags.txt": [],  # combos below (cfi needs -flto)
}
CFI_COMBOS = [
    "-fsanitize=cfi -flto -fvisibility=hidden",
    "-fsanitize=cfi-icall -flto -fvisibility=hidden",
    "-fsanitize=cfi-cast-strict -flto -fvisibility=hidden",
    "-fsanitize=cfi-derived-cast -flto -fvisibility=hidden",
    "-fsanitize=cfi-unrelated-cast -flto -fvisibility=hidden",
    "-fsanitize=cfi-nvcall -flto -fvisibility=hidden",
    "-fsanitize=cfi-mfcall -flto -fvisibility=hidden",
    "-fsanitize=cfi-vcall -flto -fvisibility=hidden",
]

# Classes the compiler's option universe CANNOT expose, seeded explicitly:
# linker options (the linker's, not the compiler's) and short driver flags
# (-p, -pg, --coverage) that the help-output filters can't represent.
SEED_UNITS = {
    "hardening_link_flags.txt": [
        "-Wl,-z,relro", "-Wl,-z,now", "-Wl,-z,noexecstack",
        "-Wl,-z,separate-code", "-Wl,--as-needed", "-Wl,--fatal-warnings",
        "-rdynamic"],
    # OPT-IN instrumentation: seeded here because the help-output filters
    # can't represent short driver flags. Coverage (gcov) and profiling
    # (gprof) live in their own buckets, applied only when selected.
    "coverage_flags.txt": [
        "--coverage", "-fprofile-arcs", "-ftest-coverage"],
    "profile_flags.txt": [
        "-p", "-pg"],
    # SAFE-DIRECTION NEGATIVES: for these options the POSITIVE form is the
    # dangerous one (-funsafe-math-optimizations) or hides information
    # (-feliminate-unused-debug-symbols). The negative-form filter would
    # drop them, so the safe direction is seeded explicitly; the dangerous
    # positives are policy-excluded in flag-exclusions.txt.
    "code_generation_flags.txt": [
        "-fno-common", "-fno-verbose-asm", "-fno-plt", "-fno-fast-math",
        "-fno-unsafe-math-optimizations", "-fno-fp-int-builtin-inexact"],
    "debug_flags.txt": [
        "-fno-eliminate-unused-debug-symbols", "-fno-merge-debug-strings",
        "-gno-strict-dwarf", "-gno-internal-reset-location-views",
        "-fno-eliminate-unused-debug-types", "-gno-omit-unreferenced-methods"],
    # NEGATIVE forms that are load-bearing: they silence DRIVER noise that
    # would otherwise fail real builds under -Werror (clang warns
    # "argument unused" for -pg at compile). The bootstrap's negative-form
    # filter would drop them, so they are seeded.
    "warning_flags.txt": [
        "-Wno-poison-system-directories",
        "-Wno-invalid-command-line-argument",
        "-Wno-unused-command-line-argument"],
}


def load_patterns():
    pats = []
    if os.path.exists(EXCLUSIONS_FILE):
        for line in open(EXCLUSIONS_FILE, encoding="utf-8", errors="replace"):
            line = line.split("#", 1)[0].strip()
            if line:
                pats.append(line)
    return pats


def excluded(flag, pats):
    return any(fnmatch.fnmatchcase(flag, p) or
               fnmatch.fnmatchcase(flag.rstrip("="), p) for p in pats)


def main():
    compare = "--compare" in sys.argv
    db = json.load(open(DB_FILE, encoding="utf-8"))
    pats = load_patterns()
    flags = db["flags"]

    files = {}
    needs_value = []
    n_excluded = n_negative = 0
    for flag in sorted(flags):
        e = flags[flag]
        if excluded(flag, pats):
            n_excluded += 1
            continue
        m = re.match(r"^-(W|f|g)no-(.+)$", flag)
        if m and (f"-{m.group(1)}{m.group(2)}" in flags
                  or f"-{m.group(1)}{m.group(2)}=" in flags):
            n_negative += 1
            continue
        cat = e.get("category", "code_generation_flags.txt")
        if cat == "sanitizer":
            # sanitizer MODIFIER flags: route to the group they modify
            if flag.startswith("-fsanitize-address-"):
                cat = "address_sanitizer_flags.txt"
            elif flag.startswith("-fsanitize-cfi-"):
                # CFI modifiers only work alongside cfi+lto: emit as combos
                files.setdefault("cfi_sanitizer_flags.txt",
                                 {"entries": []})["entries"].append(
                    {"flags": f"-fsanitize=cfi -flto -fvisibility=hidden "
                              f"{flag}", "enabled": True})
                continue
            elif flag.startswith("-fsanitize-memory-"):
                cat = "memory_sanitizer_flags.txt"
            elif flag.startswith("-fsanitize-thread-"):
                cat = "thread_sanitizer_flags.txt"
            elif flag.startswith("-fsanitize-hwaddress-"):
                cat = "hwaddress_sanitizer_flags.txt"
            else:
                cat = "undefined_sanitizer_flags.txt"
        if flag.endswith("="):
            if flag == "-Wsuggest-attribute=":
                for sv in ("pure", "const", "noreturn", "malloc",
                           "format", "cold"):
                    files.setdefault(cat, {"entries": []})["entries"].append(
                        {"flags": f"-Wsuggest-attribute={sv}",
                         "enabled": True})
                continue
            if flag in KNOWN_VALUES:
                entry = {"flags": KNOWN_VALUES[flag], "enabled": True}
            else:
                needs_value.append(flag)
                continue
        else:
            entry = {"flags": flag, "enabled": True}
        files.setdefault(cat, {"entries": []})["entries"].append(entry)

    for combo in KNOWN_COMBOS:
        files.setdefault("debug_flags.txt", {"entries": []})["entries"].append(
            {"flags": combo, "enabled": True})
    for fname, values in SANITIZE_GROUPS.items():
        for v in values:
            files.setdefault(fname, {"entries": []})["entries"].append(
                {"flags": f"-fsanitize={v}", "enabled": True})
    for combo in CFI_COMBOS:
        files.setdefault("cfi_sanitizer_flags.txt",
                         {"entries": []})["entries"].append(
            {"flags": combo, "enabled": True})
    for fname, units in SEED_UNITS.items():
        for u in units:
            files.setdefault(fname, {"entries": []})["entries"].append(
                {"flags": u, "enabled": True})

    # ordering pass: bootstrap emits alphabetical order, but last-one-wins
    # means value variants must end on the STRONGEST form. Reuse the
    # orderings lint-flags.py maintains instead of duplicating them.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lintflags", os.path.join(SCRIPT_DIR, "generators", "lint-flags.py"))
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)

    def strength_key(entry, idx):
        toks = entry["flags"].split()
        if len(toks) == 1:
            b, v, neg = lint.split_flag(toks[0])
            order = lint.STRENGTH.get(b)
            if order is not None:
                v = lint.PLAIN_ALIAS.get(b, v) if v == "" else v
                if v in order:
                    return (0, b, order.index(v))
            for pos, (weak, strong) in enumerate(lint.SAME_AXIS):
                if toks[0] == weak:
                    return (0, f"axis{pos}", 0)
                if toks[0] == strong:
                    return (0, f"axis{pos}", 1)
        return (0, f"~{idx:06d}", 0)   # unique key: keep original position

    for spec_f in files.values():
        spec_f["entries"] = [e for _, e in sorted(
            ((strength_key(e, i), e) for i, e in enumerate(spec_f["entries"])),
            key=lambda p: p[0])]

    out = {"_doc": "BOOTSTRAPPED from flag-database.json + "
                   "flag-exclusions.txt only. Probe + whole-set + lint "
                   "refine it; judgment refines it further.",
           "files": files}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")

    n = sum(len(v["entries"]) for v in files.values())
    print(f"bootstrap: {n} enabled entries in {len(files)} files "
          f"({n_excluded} policy-excluded, {n_negative} negative forms "
          f"skipped, {len(needs_value)} value-flags need a chosen value)")
    if needs_value:
        nv = os.path.join(SCRIPT_DIR, "flag_report")
        os.makedirs(nv, exist_ok=True)
        with open(os.path.join(nv, "bootstrap-needs-value.txt"), "w",
                  encoding="utf-8") as f:
            f.write("# value-taking flags bootstrap could not enable — "
                    "pick a value, add to KNOWN_VALUES or the selection\n")
            f.write("\n".join(needs_value) + "\n")
        print(f"  -> flag_report/bootstrap-needs-value.txt")

    if compare and os.path.exists(SELECTION):
        cur = json.load(open(SELECTION, encoding="utf-8"))
        def units(sel):
            return {" ".join(e["flags"].split())
                    for v in sel["files"].values()
                    for e in v["entries"] if e.get("enabled")}
        b, c = units(out), units(cur)
        both = b & c
        print(f"compare vs curated selection: {len(both)} same, "
              f"{len(b - c)} bootstrap-only (would need re-judging), "
              f"{len(c - b)} curated-only (value picks, combos, "
              f"per-file placement)")
    return 0


# --help / -h: print module docstring and exit (P101 uniform CLI help)
if __name__ == "__main__" and ("--help" in sys.argv or "-h" in sys.argv):
    print(__doc__ or __file__)
    raise SystemExit(0)

if __name__ == "__main__":
    raise SystemExit(main())
