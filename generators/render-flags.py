#!/usr/bin/env python3
"""render-flags.py — flag-selection.json -> flags/*.txt (+ per-family
override deny-lists).

flag-selection.json is the single source of selection truth (see
flags-export.py for the schema).  This renders it into the exact files the
probe pipeline consumes:

  flags/<name>.txt          active lines (enabled) + commented lines
                            (excluded, with rationale), in selection order
  flags/overrides-gcc.txt   tokens NOT to probe for GCC-family compilers
  flags/overrides-clang.txt tokens NOT to probe for clang-family compilers
                            (from per-entry "gcc": false / "clang": false)

generate-flags.sh detects each compiler's family and skips any probe unit
containing a denied token, so a flag can be on for both, off for both, or
on for one family only — without duplicating the lists.

  ./render-flags.py          write the files
  ./render-flags.py --check  exit 1 if flags/*.txt disagree with the JSON
                             (run after hand-editing either side)

Profiles: --selection <file> --out <dir> render a DIFFERENT selection into
a DIFFERENT flags dir (used by the --standard tier, which renders
flag-selection.standard.json into flags-standard/). Defaults are the
maximal profile (flag-selection.json -> flags/).
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAGS_DIR = os.path.join(SCRIPT_DIR, "flags")
SELECTION = os.path.join(SCRIPT_DIR, "flag-selection.json")


def parse_args():
    parser = argparse.ArgumentParser(description="Render flag-selection JSON into flags/*.txt.")
    parser.add_argument("--check", action="store_true", help="verify rendered flags instead of writing them")
    parser.add_argument("--selection", default=SELECTION, help="selection JSON path")
    parser.add_argument("--out", default=FLAGS_DIR, help="output flags directory")
    return parser.parse_args()


def load():
    with open(SELECTION, encoding="utf-8") as f:
        return json.load(f)


def render_file(name, spec):
    lines = []
    for h in spec.get("header", []):
        lines.append(f"# {h}" if h else "#")
    if spec.get("header"):
        lines.append("")
    for e in spec["entries"]:
        flags = e["flags"]
        comment = e.get("comment", "")
        suffix = f"    # {comment}" if comment else ""
        fam = [f for f in ("gcc", "clang", "c", "cxx") if e.get(f) is False]
        if fam and e.get("enabled", False):
            note = f"[{'/'.join(fam)} off via overrides] "
            suffix = f"    # {note}{comment}".rstrip()
        if e.get("enabled", False):
            lines.append(f'        "{flags}"{suffix}')
        else:
            lines.append(f'#        "{flags}"{suffix}')
    return "\n".join(lines) + "\n"


def tokens_of(path):
    """Active probe units of a rendered file, for --check comparison."""
    units = []
    if not os.path.exists(path):
        return units
    for line in open(path, encoding="utf-8", errors="replace"):
        code = line.split("#", 1)[0].replace('"', " ").strip()
        if code:
            units.append(" ".join(code.split()))
    return units


def main():
    global SELECTION, FLAGS_DIR
    args = parse_args()
    check = args.check
    SELECTION = args.selection
    FLAGS_DIR = args.out
    os.makedirs(FLAGS_DIR, exist_ok=True)
    sel = load()
    if not isinstance(sel.get("files"), dict) or not sel["files"]:
        print("render-flags.py: selection contains no files", file=sys.stderr)
        return 2
    entry_count = sum(len(s.get("entries", [])) for s in sel["files"].values() if isinstance(s, dict))
    if entry_count == 0:
        print("render-flags.py: selection contains no flag entries", file=sys.stderr)
        return 2
    overrides = {"gcc": [], "clang": [], "c": [], "cxx": []}
    rc = 0

    for name, spec in sel["files"].items():
        for e in spec["entries"]:
            if e.get("enabled", False):
                for fam in ("gcc", "clang", "c", "cxx"):
                    if e.get(fam) is False:
                        overrides[fam].extend(e["flags"].split())

    if check:
        for name, spec in sel["files"].items():
            want = [" ".join(e["flags"].split())
                    for e in spec["entries"] if e.get("enabled", False)]
            have = tokens_of(os.path.join(FLAGS_DIR, name))
            if want != have:
                print(f"OUT OF SYNC: {name}")
                for w in want:
                    if w not in have:
                        print(f"  json-only: {w}")
                for h in have:
                    if h not in want:
                        print(f"  file-only: {h}")
                rc = 1
        if rc == 0:
            print("flag-selection.json and flags/*.txt are in sync.")
        return rc

    for name, spec in sel["files"].items():
        with open(os.path.join(FLAGS_DIR, name), "w", encoding="utf-8") as f:
            f.write(render_file(name, spec))
    # a flags file on disk but absent from the selection would silently keep
    # feeding stale flags to the probe — stub it instead
    for name in sorted(os.listdir(FLAGS_DIR)):
        if (name.endswith(".txt") and not name.startswith("overrides-")
                and name not in sel["files"]):
            with open(os.path.join(FLAGS_DIR, name), "w",
                      encoding="utf-8") as f:
                f.write("# (no entries in flag-selection.json for this "
                        "file — rendered empty)\n")
    scope_label = {"gcc": "gcc-family compilers", "clang": "clang-family compilers",
                   "c": "the C language", "cxx": "the C++ language"}
    for fam in ("gcc", "clang", "c", "cxx"):
        path = os.path.join(FLAGS_DIR, f"overrides-{fam}.txt")
        if overrides[fam]:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# tokens NOT probed for {scope_label[fam]} — "
                        "generated by render-flags.py from flag-selection.json\n")
                f.write("\n".join(overrides[fam]) + "\n")
        elif os.path.exists(path):
            os.unlink(path)
    n = sum(len(s["entries"]) for s in sel["files"].values())
    print(f"rendered {len(sel['files'])} files ({n} entries); overrides: "
          f"gcc={len(overrides['gcc'])} clang={len(overrides['clang'])} "
          f"c={len(overrides['c'])} cxx={len(overrides['cxx'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
