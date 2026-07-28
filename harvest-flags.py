#!/usr/bin/env python3
"""harvest-flags.py — canonical flag list from INSTALLED compilers + selection status.

The installed compiler binary is the only ground truth that always exists
(Apple clang has no matching public source; distro compilers carry patches),
and it can report its own option universe:

  gcc:    gcc --help=warnings / common / optimizers / ... (one line per option)
  clang:  clang --autocomplete=-W / -f / -g / -O  (complete lists, with
          descriptions; shipped since clang 7 — works for Apple clang too),
          with --help/--help-hidden as a fallback for very old builds.

Selection model — your flags/*.txt files already ARE the include/exclude
mechanism, and this tool honors it:

  active line in flags/*.txt      -> INCLUDED  [+]
  commented line in flags/*.txt   -> EXCLUDED  [-]  (considered and rejected)
  not mentioned anywhere          -> NEW       [?]  (needs a decision)

Outputs per compiler, under flag_report/ (never touches flags/*.txt):

  <cc>-canonical.txt   the full universe with [+]/[-]/[?] status per flag
  <cc>-new-flags.txt   only the [?] flags, bucketed by the flags/ file they
                       would belong in — your curation worklist

Curation loop: run this, review <cc>-new-flags.txt, move lines you want into
the suggested flags/*.txt (active) and lines you reject in as comments, bump
version.txt, and let generate-flags.sh probe what actually works. Negative
variants (-Wno-*/-fno-*) whose positive form is in the universe are counted
but kept out of the worklist — the positive spelling is the decision point.

Usage:
  ./harvest-flags.py gcc clang
  ./harvest-flags.py gcc-15 g++-15 clang-22
Requires: python3 (already required by the build). No network.
"""

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLAGS_DIR = os.path.join(SCRIPT_DIR, "flags")
REPORT_DIR = os.path.join(SCRIPT_DIR, "flag_report")
EXCLUSIONS_FILE = os.path.join(SCRIPT_DIR, "flag-exclusions.txt")

GCC_SECTIONS = [
    "warnings", "common", "optimizers", "analyzer",
    "codegen", "debug", "language", "undocumented",
    # target = the -m* namespace (arch/ISA tuning + a few CPU-hardening flags
    # like -mharden-sls/-mspeculative-load-hardening/-mbranch-protection). Most
    # are out of scope and caught by the -m* pattern in flag-exclusions.txt;
    # the hardening ones are curated individually in flag-selection.json.
    "target",
]
CLANG_AUTOCOMPLETE_PREFIXES = ["-W", "-f", "-g", "-O"]


MAP_FILE = os.path.join(SCRIPT_DIR, "compiler_paths.txt")


def map_resolve(name):
    """Resolve a compiler name through the pinned compiler_paths.txt map
    (written by check-compilers.sh); returns the name unchanged if unmapped
    or already a path."""
    if os.path.sep in name or not os.path.isfile(MAP_FILE):
        return name
    with open(MAP_FILE, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith(name + "="):
                path = line.split("=", 1)[1]
                if os.access(path, os.X_OK):
                    return path
    return name


def run(cmd):
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              check=False, env=env)
    except OSError:
        return None


def detect_family(cc):
    out = run([cc, "--version"])
    if out is None:
        return "missing", ""
    text = (out.stdout or "") + (out.stderr or "")
    low = text.lower()
    if "clang" in low:
        return "clang", text.splitlines()[0] if text else "clang"
    if "gcc" in low or "g++" in low or "free software foundation" in low:
        return "gcc", text.splitlines()[0] if text else "gcc"
    return "unknown", text


def clean_flag(tok):
    """Normalize one option token from help output.
    Returns (flag, needs_value) or None if it isn't a curatable flag."""
    tok = tok.strip().rstrip(".,;")
    if not tok.startswith("-") or tok.startswith("--"):
        return None
    # placeholder tails from help text: -Wfoo=<n>, -Wbar=[a|b], -Wbaz=N
    needs_value = False
    # NOTE: '#' excluded from the name charset — a literal # in a flag name
    # (-W#warnings) would be truncated by every #-comment parser downstream
    m = re.match(r"^(-[A-Za-z][A-Za-z0-9_+.-]*)(=|\[=)?", tok)
    if not m:
        return None
    flag = m.group(1)
    rest = tok[len(flag):]
    if rest.startswith(("=", "[=", "<")):
        needs_value = True
        flag += "="
    if flag.rstrip("=").endswith(","):        # -Wa, -Wl, -Wp, pass-throughs
        return None
    if len(flag.rstrip("=")) < 3:             # bare -W, -f, -g, -O headers
        return None
    return flag, needs_value


# ---------------- gcc: --help=<section> ----------------

def harvest_gcc(cc):
    flags = {}
    for section in GCC_SECTIONS:
        out = run([cc, f"--help={section}"])
        if out is None or out.returncode != 0:
            continue
        for line in out.stdout.splitlines():
            # option lines are indented exactly two spaces; deeper indents are
            # description continuations (which may mention other flags — skip)
            m = re.match(r"^  (-\S+)", line)
            if not m:
                continue
            cleaned = clean_flag(m.group(1))
            if cleaned:
                flags.setdefault(cleaned[0], cleaned[1])
    return flags


# ---------------- clang: --autocomplete, fallback --help ----------------

def harvest_clang(cc):
    flags = {}
    ok = False
    # `--autocomplete=-` enumerates the WHOLE driver option table — a superset
    # of the -W/-f/-g/-O prefixes, so it also surfaces -m* (where the CPU
    # hardening flags live), -R* remarks, etc. that a prefix-only harvest never
    # sees. Still query the four prefixes too: on some older clangs the bare '-'
    # listing is thinner, and setdefault means the union is taken cheaply.
    for query in ["-"] + CLANG_AUTOCOMPLETE_PREFIXES:
        out = run([cc, f"--autocomplete={query}"])
        if out is None or out.returncode != 0 or not out.stdout.strip():
            continue
        ok = True
        for line in out.stdout.splitlines():
            tok = line.split("\t", 1)[0]
            cleaned = clean_flag(tok)
            if cleaned:
                flags.setdefault(cleaned[0], cleaned[1])
    # --help-hidden lists options omitted from autocomplete (and is the only
    # source on very old clangs where autocomplete is unavailable).
    helpargs = ["--help-hidden"] if ok else ["--help", "--help-hidden"]
    for helparg in helpargs:
        out = run([cc, helparg])
        if out is None:
            continue
        for line in out.stdout.splitlines():
            m = re.match(r"^\s+(-\S+)", line)
            if not m:
                continue
            cleaned = clean_flag(m.group(1))
            if cleaned:
                flags.setdefault(cleaned[0], cleaned[1])
    return flags


# ---------------- curated selection state ----------------

def base_key(flag):
    return flag.split("=", 1)[0]


def load_selection():
    """(included, excluded) sets of base keys from flags/*.txt."""
    included, excluded = set(), set()
    if not os.path.isdir(FLAGS_DIR):
        return included, excluded
    tok_re = re.compile(r"-[A-Za-z][^\s\"']*")
    for fn in sorted(os.listdir(FLAGS_DIR)):
        if not fn.endswith(".txt") or fn.startswith("overrides-"):
            continue
        with open(os.path.join(FLAGS_DIR, fn), encoding="utf-8",
                  errors="replace") as f:
            for raw in f:
                stripped = raw.strip()
                if not stripped:
                    continue
                is_comment = stripped.startswith("#")
                for tok in tok_re.findall(raw):
                    key = base_key(tok.rstrip('".,'))
                    (excluded if is_comment else included).add(key)
    # an active mention anywhere wins over a commented mention elsewhere
    excluded -= included
    return included, excluded


def load_exclusion_patterns():
    """Family-level exclusions from flag-exclusions.txt: one glob per line
    (e.g. -Wmicrosoft-*), '#' comments allowed. Anything matching is treated
    as excluded without needing hundreds of commented lines in flags/*.txt —
    for whole categories that are out of scope (other languages' frontends,
    other platforms' targets, driver plumbing)."""
    patterns = []
    if not os.path.isfile(EXCLUSIONS_FILE):
        return patterns
    with open(EXCLUSIONS_FILE, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip().strip('"')
            if line:
                patterns.append(line)
    return patterns


def matches_exclusion(flag, patterns):
    k = base_key(flag)
    return any(fnmatch.fnmatchcase(k, p) or fnmatch.fnmatchcase(flag, p)
               for p in patterns)


def bucket_for(flag):
    if flag.startswith("-Wanalyzer-") or flag == "-fanalyzer":
        return "analyzer_flags.txt"
    if flag.startswith("-W"):
        return "warning_flags.txt"
    if flag.startswith(("-fsanitize=", "-fsanitize-")):
        return "sanitizer files"
    if flag.startswith("-g"):
        return "debug_flags.txt"
    if flag.startswith("-O"):
        return "optimization_flags.txt"
    if flag.startswith("-R"):
        return "REMARKS (informational; usually excluded)"
    if flag.startswith("-m"):
        # separated so the handful of CPU-hardening -m flags don't hide in the
        # hundreds of arch/ISA-tuning ones — scan this bucket for hardening
        return "machine/arch -m* (mostly ISA tuning — scan for hardening)"
    if flag in ("-pg", "-p"):
        return "profile_flags.txt"
    if flag == "--coverage" or re.match(
            r"-f(profile|test-coverage|coverage|condition-coverage|cs-profile)",
            flag):
        return "coverage_flags.txt"
    if re.match(r"-f(instrument|stack-protector|harden|patchable"
                r"|stack-check|stack-clash)", flag):
        return "hardening_compiler_flags.txt"
    return "code_generation_flags.txt (or other)"


def negative_of(flag):
    """-Wno-x -> -Wx, -fno-y -> -fy, -gno-z -> -gz (None if not negative)."""
    m = re.match(r"^-(W|f|g)no-(.+)$", flag)
    return f"-{m.group(1)}{m.group(2)}" if m else None


# ---------------- value spaces for =-flags ----------------
# Neither driver can enumerate its own -fsanitize= value space, so the master
# candidate list below is the union of every category either family has ever
# shipped (through gcc 16 / clang 22).  It only PROPOSES: acceptance is
# probed against the actual binary, so the per-compiler report is ground
# truth.  Where an installed man page exists (macOS, FreeBSD, full Linux
# installs) it is scanned too, so a brand-new category not in this list
# still announces itself.
SANITIZE_CANDIDATES = [
    "address", "hwaddress", "kernel-address", "kernel-hwaddress",
    "memtag", "memtag-stack", "memtag-heap", "memtag-globals",
    "thread", "leak", "memory", "kernel-memory", "dataflow",
    "cfi", "cfi-cast-strict", "cfi-derived-cast", "cfi-icall", "cfi-mfcall",
    "cfi-nvcall", "cfi-unrelated-cast", "cfi-vcall", "kcfi",
    "safe-stack", "shadow-call-stack", "realtime", "type", "numerical",
    "undefined", "alignment", "bool", "builtin", "bounds", "bounds-strict",
    "array-bounds", "local-bounds", "enum", "float-cast-overflow",
    "float-divide-by-zero", "function", "integer", "integer-divide-by-zero",
    "implicit-conversion", "implicit-integer-conversion",
    "implicit-integer-arithmetic-value-change", "implicit-integer-truncation",
    "implicit-signed-integer-truncation",
    "implicit-unsigned-integer-truncation", "implicit-integer-sign-change",
    "implicit-bitfield-conversion", "nonnull-attribute", "null",
    "nullability", "nullability-arg", "nullability-assign",
    "nullability-return", "objc-cast", "object-size",
    "pointer-compare", "pointer-subtract", "pointer-overflow",
    "return", "returns-nonnull-attribute",
    "shift", "shift-base", "shift-exponent", "signed-integer-overflow",
    "unsigned-integer-overflow", "unsigned-shift-base",
    "unreachable", "vla-bound", "vptr",
]


def doc_sanitize_candidates(cc_bin):
    """Extract -fsanitize=<value> tokens from the binary's installed man
    page, when one exists (best-effort; minimized systems have none)."""
    env = dict(os.environ, LC_ALL="C", LANG="C", MANPAGER="cat", PAGER="cat")
    try:
        out = subprocess.run(["man", os.path.basename(cc_bin)],
                             capture_output=True, text=True, check=False,
                             env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if out.returncode != 0:
        return set()
    return set(re.findall(r"-fsanitize=([a-z][a-z0-9-]*)", out.stdout or ""))


def curated_sanitize_values():
    """Every -fsanitize=<value> mentioned anywhere in flags/*.txt — active
    OR commented — i.e. every category that already has a decision."""
    vals = set()
    if not os.path.isdir(FLAGS_DIR):
        return vals
    for name in os.listdir(FLAGS_DIR):
        if not name.endswith(".txt") or name.startswith("overrides-"):
            continue
        with open(os.path.join(FLAGS_DIR, name), encoding="utf-8",
                  errors="replace") as f:
            vals |= set(re.findall(r"-fsanitize=([a-z][a-z0-9-]*)", f.read()))
    return vals


def probe_sanitize_value(cc_bin, value, tmpdir):
    """Ask the actual binary. Returns one of:
    accepted / needs-lto / target-rejected / rejected"""
    src = os.path.join(tmpdir, "sv.c")
    if not os.path.exists(src):
        with open(src, "w", encoding="utf-8") as f:
            f.write("int main(void){return 0;}\n")
    base = [cc_bin, f"-fsanitize={value}", "-fsyntax-only", src]
    out = run(base)
    if out is None:
        return "rejected"
    if out.returncode == 0:
        return "accepted"
    err = (out.stderr or "") + (out.stdout or "")
    if "for target" in err:
        return "target-rejected"
    if "unsupported argument" in err or "unrecognized argument" in err \
            or "unsupported option" in err:
        return "rejected"
    # e.g. "only allowed with '-flto'" (cfi) — retry with the known combo
    out2 = run([cc_bin, f"-fsanitize={value}", "-flto",
                "-fvisibility=hidden", "-fsyntax-only", src])
    if out2 is not None and out2.returncode == 0:
        return "needs-lto"
    return "rejected"


DB_FILE = os.path.join(SCRIPT_DIR, "flag-database.json")


def merge_database(per_cc):
    """Merge this run's universes into flag-database.json — the canonical,
    machine-generated record of every flag every queried binary exposes.
    per_cc: {cc_name: (family, label, universe_dict)}.  Entries for
    compilers not queried this run are preserved, so runs on different
    machines (Linux gcc, Apple clang, FreeBSD) accumulate into one file."""
    db = {"_doc": "Canonical flag universe, harvested from installed "
                  "compiler binaries by harvest-flags.py. Regenerate by "
                  "running it; entries merge across machines/compilers. "
                  "SELECTION lives in flag-selection.json, not here.",
          "flags": {}}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, encoding="utf-8") as f:
                db = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"could not read existing {DB_FILE}: {exc}") from exc
    flags = db.setdefault("flags", {})
    queried = set(per_cc)
    for cc, (family, label, universe) in per_cc.items():
        for flag, needs_value in universe.items():
            e = flags.setdefault(flag, {})
            if needs_value:
                e["takes_value"] = True
            e.setdefault("category", bucket_for(flag).split()[0])
            e.setdefault("families", {})[family] = True
            e.setdefault("seen_in", {})[cc] = label
    # drop stale seen_in entries for compilers we re-queried
    for flag, e in list(flags.items()):
        seen = e.get("seen_in", {})
        for cc in queried:
            fam = per_cc[cc][0]
            if cc in seen and flag not in per_cc[cc][2]:
                del seen[cc]
        if not seen:
            del flags[flag]
    fd, tmp_path = tempfile.mkstemp(prefix=".flag-database.", suffix=".json", dir=SCRIPT_DIR)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, DB_FILE)
    return len(flags)


def group_primaries():
    """The selectable sanitizer groups (flags/<name>_sanitizer_flags.txt)
    and each group's primary probe unit: the first active line's tokens.
    Combo lines (cfi + -flto) come through whole, so pair-testing uses the
    same shape the build does."""
    groups = {}
    if not os.path.isdir(FLAGS_DIR):
        return groups
    for name in sorted(os.listdir(FLAGS_DIR)):
        m = re.match(r"(.+)_sanitizer_flags\.txt$", name)
        if not m:
            continue
        with open(os.path.join(FLAGS_DIR, name), encoding="utf-8",
                  errors="replace") as f:
            for line in f:
                s = line.split("#", 1)[0].replace('"', " ").strip()
                if s and s.split()[0].startswith("-fsanitize="):
                    groups[m.group(1)] = s.split()
                    break
    return groups


def report_sanitize_combos(cc, cc_bin, base, tmpdir):
    """Pairwise-combine every selectable sanitizer group and ask the driver.
    Flags that DEPEND ON or FORBID each other can't be seen one flag at a
    time — only the combination reveals them.  Writes
    <cc>-sanitize-combos.txt; returns the number of conflicting pairs."""
    src = os.path.join(tmpdir, "sv.c")
    if not os.path.exists(src):
        with open(src, "w", encoding="utf-8") as f:
            f.write("int main(void){return 0;}\n")
    groups = group_primaries()
    names = sorted(groups)
    # only pair groups this binary accepts at all
    ok = [n for n in names
          if (r := run([cc_bin] + groups[n] + ["-fsyntax-only", src]))
          is not None and r.returncode == 0]
    conflicts = []
    lines = []
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            r = run([cc_bin] + groups[a] + groups[b] + ["-fsyntax-only", src])
            if r is not None and r.returncode == 0:
                lines.append(f"[ok      ] {a} + {b}")
            else:
                err = ((r.stderr if r else "") or "").strip().splitlines()
                lines.append(f"[CONFLICT] {a} + {b}"
                             + (f"  — {err[0]}" if err else ""))
                conflicts.append((a, b))
    path = os.path.join(REPORT_DIR, f"{base}-sanitize-combos.txt")
    with open(path, "w", encoding="utf-8") as out:
        out.write(f"# Pairwise sanitizer-GROUP compatibility for {cc}, "
                  "asked of the driver itself.\n")
        out.write("# A CONFLICT pair cannot be selected together in "
                  "sanitizers.txt / -s for this compiler.\n")
        out.write(f"# groups accepted by this binary: {' '.join(ok)}\n\n")
        out.write("\n".join(lines) + "\n")
    return len(conflicts)


def report_sanitize_values(cc, cc_bin, base, curated, tmpdir):
    """Probe the whole candidate value space; write <cc>-sanitize-values.txt.
    Returns (accepted_count, new_accepted list)."""
    candidates = sorted(set(SANITIZE_CANDIDATES) | doc_sanitize_candidates(cc_bin))
    rows = []
    new_accepted = []
    accepted = 0
    for v in candidates:
        st = probe_sanitize_value(cc_bin, v, tmpdir)
        known = v in curated
        if st in ("accepted", "needs-lto"):
            accepted += 1
            if not known:
                new_accepted.append(v)
        rows.append((v, st, known))
    path = os.path.join(REPORT_DIR, f"{base}-sanitize-values.txt")
    with open(path, "w", encoding="utf-8") as out:
        out.write(f"# -fsanitize= value space of {cc}, probed against the "
                  "binary itself.\n")
        out.write("# KNOWN = already mentioned (active or commented) in "
                  "flags/*.txt; NEW = needs a decision.\n")
        out.write("# (the same value space feeds -fsanitize-recover= / "
                  "-fsanitize-trap=)\n\n")
        for v, st, known in rows:
            out.write(f"[{st:>15}] {'KNOWN' if known else 'NEW  '} "
                      f"-fsanitize={v}\n")
    return accepted, new_accepted


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("compilers", nargs="+",
                    help="compiler names/paths (gcc, clang, gcc-15, ...)")
    args = ap.parse_args()

    included, excluded = load_selection()
    excl_patterns = load_exclusion_patterns()
    if not included and not excluded:
        print(f"warning: no curated flags found under {FLAGS_DIR}",
              file=sys.stderr)

    curated_sans = curated_sanitize_values()
    os.makedirs(REPORT_DIR, exist_ok=True)
    rc = 0
    per_cc = {}
    tmpdir = tempfile.mkdtemp(prefix="harvest-sv-")
    try:
        for cc in args.compilers:
            cc_bin = map_resolve(cc)
            family, label = detect_family(cc_bin)
            if family == "missing":
                print(f"[{cc}] not found in PATH; skipping", file=sys.stderr)
                rc = 1
                continue
            if family == "unknown":
                print(f"[{cc}] cannot identify compiler family; skipping",
                      file=sys.stderr)
                rc = 1
                continue

            universe = harvest_gcc(cc_bin) if family == "gcc" else harvest_clang(cc_bin)
            if not universe:
                print(f"[{cc}] harvested nothing — unexpected; skipping",
                      file=sys.stderr)
                rc = 1
                continue
            per_cc[os.path.basename(cc)] = (family, label, universe)

            def status(flag):
                k = base_key(flag)
                if k in included:
                    return "+"
                if k in excluded or matches_exclusion(flag, excl_patterns):
                    return "-"
                return "?"

            base = os.path.basename(cc)

        # full canonical list with selection status
            canon_path = os.path.join(REPORT_DIR, f"{base}-canonical.txt")
            counts = {"+": 0, "-": 0, "?": 0}
            with open(canon_path, "w", encoding="utf-8") as out:
                out.write(f"# Canonical flag universe of {cc} ({label})\n")
                out.write("# [+] included in flags/*.txt   [-] excluded "
                          "(commented)   [?] no decision yet\n")
                out.write("# [=] takes a value\n\n")
                for flag in sorted(universe):
                    s = status(flag)
                    counts[s] += 1
                    out.write(f"[{s}] {flag}{'   [=]' if universe[flag] else ''}\n")

        # worklist: only the undecided, minus negative variants of decided/
        # present positive forms
            new_items = []
            skipped_neg = 0
            for flag, nv in sorted(universe.items()):
                if status(flag) != "?":
                    continue
                pos = negative_of(flag)
                if pos is not None and (base_key(pos) in included
                                        or base_key(pos) in excluded
                                        or pos in universe
                                        or pos + "=" in universe):
                    skipped_neg += 1
                    continue
                new_items.append((flag, nv))

            work_path = os.path.join(REPORT_DIR, f"{base}-new-flags.txt")
            with open(work_path, "w", encoding="utf-8") as out:
                out.write(f"# Undecided flags for {cc} ({label})\n")
                out.write(f"# universe={len(universe)}  included={counts['+']}  "
                          f"excluded={counts['-']}  undecided={len(new_items)}\n")
                out.write(f"# ({skipped_neg} -Wno-/-fno- variants of decided or "
                          "listed positive forms omitted)\n")
                out.write("# Move each line into the suggested flags/ file — "
                          "active to include, commented to exclude — then bump "
                          "version.txt and let generate-flags.sh probe them.\n\n")
                buckets = {}
                for flag, nv in new_items:
                    buckets.setdefault(bucket_for(flag), []).append((flag, nv))
                for bucket in sorted(buckets):
                    out.write(f"## suggested destination: {bucket}\n")
                    for flag, nv in buckets[bucket]:
                        out.write(f"{flag}{'   [=]' if nv else ''}\n")
                    out.write("\n")

        # value space of -fsanitize=, probed against the binary
            n_accepted, new_vals = report_sanitize_values(
                cc, cc_bin, base, curated_sans, tmpdir)
            n_conflicts = report_sanitize_combos(cc, cc_bin, base, tmpdir)

            print(f"[{cc}] universe {len(universe)} | included {counts['+']} | "
                  f"excluded {counts['-']} | undecided {len(new_items)} "
                  f"(+{skipped_neg} negatives omitted)")
            print(f"      sanitize values: {n_accepted} accepted"
                  + (f" | NEW: {' '.join(new_vals)}" if new_vals else " | no new")
                  + f" | conflicting group pairs: {n_conflicts}")
            print(f"      -> {os.path.relpath(canon_path, SCRIPT_DIR)}")
            print(f"      -> {os.path.relpath(work_path, SCRIPT_DIR)}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if per_cc:
        try:
            total = merge_database(per_cc)
        except ValueError as exc:
            print(f"harvest-flags.py: {exc}", file=sys.stderr)
            return 2
        print(f"flag-database.json: {total} canonical flags "
              f"(merged {len(per_cc)} compiler(s))")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
