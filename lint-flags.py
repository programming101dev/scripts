#!/usr/bin/env python3
"""lint-flags.py — consistency check for the ACTIVE flags in flags/*.txt.

Compilers resolve repeated settings of the same option with LAST-ONE-WINS,
so redundancy across the curated set splits into three classes:

  harmless   umbrella + members (-Wformat=2 + -Wformat-security, -Wall +
             members): deliberate armor — members stay pinned even if the
             umbrella's meaning drifts between compiler versions.
  additive   -fsanitize=a -fsanitize=b, -Wsuggest-attribute=x/y: each value
             ADDS; never a conflict.  Whitelisted below.
  conflict   the dangerous class this tool exists for:
               NEGATION  -Wfoo ... -Wno-foo  (or =0): one cancels the other
               ORDER     -Wfoo=2 ... -Wfoo   plain/weaker form AFTER a
                         stronger one silently downgrades it

Order is significant: flags/*.txt line order == probe cache order == final
command-line order.  A weaker value BEFORE a stronger one is fine (and is
how cross-compiler coverage works: clang-only plain form first, GCC-only
=N form last); the reverse is a downgrade.

Exit status: 0 clean, 1 conflicts found.  Run it after editing flags/.
"""

import glob
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLAGS_DIR = os.path.join(SCRIPT_DIR, "flags")

# Values of these options accumulate; repeated use is never a conflict.
ADDITIVE = {
    "-fsanitize", "-fno-sanitize", "-fsanitize-recover",
    "-fno-sanitize-recover", "-fsanitize-trap", "-fno-sanitize-trap",
    "-Wsuggest-attribute", "-fanalyzer-checker", "-fplugin",
    "-include", "-imacros", "-D", "-U", "-I", "-L", "-l",
}

# Known strength orderings for override-style options (weakest -> strongest),
# with the plain spelling normalized to the value it aliases.  Unlisted
# values compare by integer when both are numeric.
STRENGTH = {
    "-Wshadow": ["=compatible-local", "=local", "=global"],
    "-Wshift-overflow": ["=1", "=2"],
    "-Wcast-align": ["=base", "=strict"],
    "-Wstrict-aliasing": ["=0", "=1", "=2", "=3"],
    "-Wattribute-alias": ["=1", "=2"],
    "-Wformat": ["=1", "=2"],
    "-Wimplicit-fallthrough": ["=1", "=2", "=3", "=4", "=5"],
    "-Warray-bounds": ["=1", "=2"],
    "-Wuse-after-free": ["=1", "=2", "=3"],
}

# Distinct spellings that set the SAME underlying option (GCC deprecated
# aliases).  Normalized before conflict analysis so an alias can't override
# the canonical form through the back door.
SPELLING_ALIAS = {
    "-Wshadow-local": "-Wshadow=local",
    "-Wshadow-compatible-local": "-Wshadow=compatible-local",
}

# Different base names that set the SAME underlying setting with different
# strength — the driver accepts both silently and the LAST one wins.
# (weaker, stronger): having only the weaker is fine; having both with the
# weaker LAST is a downgrade.
SAME_AXIS = [
    ("-fpic", "-fPIC"),
    ("-fpie", "-fPIE"),
]

# What the plain spelling means, per option (GCC semantics).
PLAIN_ALIAS = {
    "-Wshadow": "=global",
    "-Wshift-overflow": "=1",
    "-Wcast-align": "=base",
    "-Wstrict-aliasing": "=3",
    "-Wattribute-alias": "=1",
    "-Wformat": "=1",
    "-Wimplicit-fallthrough": "=3",
    "-Warray-bounds": "=1",
    "-Wuse-after-free": "=2",
}


def actives():
    """Yield (file, lineno, token) for every active flag token, in order."""
    for path in sorted(glob.glob(os.path.join(FLAGS_DIR, "*.txt"))):
        base = os.path.basename(path)
        if base.startswith("overrides-"):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln, line in enumerate(f, 1):
                s = line.split("#", 1)[0].replace('"', " ").strip()
                for tok in s.split():
                    if tok.startswith("-"):
                        yield base, ln, tok


def split_flag(tok):
    """-> (base, value, negated). -Wno-x / -fno-x / -gno-x count as negated."""
    tok = SPELLING_ALIAS.get(tok, tok)
    neg = False
    m = re.match(r"^-(W|f|g)no-(.+)$", tok)
    if m:
        tok = f"-{m.group(1)}{m.group(2)}"
        neg = True
    if "=" in tok:
        b, v = tok.split("=", 1)
        return b, "=" + v, neg
    return tok, "", neg


def main():
    # Collect every occurrence per base; only the FINAL occurrence takes
    # effect (last-one-wins), so judge the final state against the
    # strongest value seen anywhere, not each consecutive pair.
    occ = {}  # base -> list of (file, ln, value, neg)
    for fname, ln, tok in actives():
        b, v, neg = split_flag(tok)
        if b in ADDITIVE:
            continue
        occ.setdefault(b, []).append((fname, ln, v, neg))

    problems = []
    for b, lst in occ.items():
        if len(lst) < 2:
            continue
        lf, ll, lv, lneg = lst[-1]
        for pf, pln, pv, pneg in lst[:-1]:
            where = f"{pf}:{pln} '{b}{pv}' vs final {lf}:{ll}"
            if pneg != lneg:
                problems.append(f"NEGATION  {where} — one cancels the other")
                break
        strengths = STRENGTH.get(b)
        norm = lambda x: PLAIN_ALIAS.get(b, x) if x == "" else x
        vals = [norm(v) for _, _, v, n in lst if not n]
        lv = norm(lv)
        if strengths is not None and lv in strengths and not lneg:
            strongest = max((x for x in vals if x in strengths),
                            key=strengths.index)
            if strengths.index(lv) < strengths.index(strongest):
                problems.append(
                    f"DOWNGRADE {b}: final '{b}{lv}' ({lf}:{ll}) is weaker "
                    f"than '{b}{strongest}' listed earlier — last wins")
        else:
            nums = [int(m.group(1)) for x in vals
                    if (m := re.fullmatch(r"=(\d+)", x))]
            mlast = re.fullmatch(r"=(\d+)", lv)
            if nums and mlast and int(mlast.group(1)) < max(nums):
                problems.append(
                    f"DOWNGRADE {b}: final '{b}{lv}' ({lf}:{ll}) is a lower "
                    f"level than =%d listed earlier — last wins" % max(nums))

    # same-axis pairs (-fpic/-fPIC): silent last-wins across DIFFERENT tokens
    order = []  # every active token in command-line order
    for fname, ln, tok in actives():
        order.append((tok, fname, ln))
    pos = {}
    for i, (tok, fname, ln) in enumerate(order):
        pos.setdefault(tok, []).append((i, fname, ln))
    for weak, strong in SAME_AXIS:
        if weak in pos and strong in pos:
            last_weak = pos[weak][-1]
            last_strong = pos[strong][-1]
            if last_weak[0] > last_strong[0]:
                problems.append(
                    f"DOWNGRADE {weak} ({last_weak[1]}:{last_weak[2]}) comes "
                    f"after {strong} — same setting, weaker form wins")

    if problems:
        print(f"{len(problems)} conflict(s) among active flags:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("flags/*.txt: no negation or downgrade conflicts among active flags.")
    return 0


# --help / -h: print module docstring and exit (P101 uniform CLI help)
if __name__ == "__main__" and ("--help" in sys.argv or "-h" in sys.argv):
    print(__doc__ or __file__)
    raise SystemExit(0)

if __name__ == "__main__":
    raise SystemExit(main())
