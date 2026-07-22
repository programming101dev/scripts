#!/usr/bin/env python3
"""flags-export.py — capture the current flags/*.txt curation as
flag-selection.json, the structured single source of selection truth.

Selection model in the JSON:

  files.<name>.entries[] — ordered (ORDER IS SIGNIFICANT: line order ==
      probe cache order == final command-line order; weakest values first,
      strongest last, so last-one-wins lands on the strongest form).
    .flags     the probe unit: one flag, or several that only make sense
               together ("-fsanitize=cfi -flto -fvisibility=hidden")
    .enabled   true = active (probed, used); false = considered & excluded
    .comment   rationale, carried into the rendered file
    .gcc/.clang  optional per-family override of .enabled — e.g.
               {"enabled": true, "clang": false} probes it for GCC only.

Workflow:
  ./flags-export.py            # flags/*.txt -> flag-selection.json (one-time
                               #   or to re-capture hand edits)
  edit flag-selection.json     # the durable place to flip decisions
  ./render-flags.py            # flag-selection.json -> flags/*.txt
  ./render-flags.py --check    # verify the two are in sync (CI-able)

flag-exclusions.txt (family glob patterns) stays its own file; it excludes
whole families from HARVEST consideration, not individual decisions.
"""

import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLAGS_DIR = os.path.join(SCRIPT_DIR, "flags")
SELECTION = os.path.join(SCRIPT_DIR, "flag-selection.json")


def parse_file(path):
    header = []
    entries = []
    in_header = True
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            in_header = False
            continue
        # pure comment line: header text, or a disabled entry if it holds flags
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            cleaned = body.replace('"', " ").strip()
            tok = cleaned.split()[0] if cleaned.split() else ""
            if re.match(r"^--?[A-Za-z]", tok):
                # disabled entry, possibly with trailing rationale comment
                m = re.match(r"([^#]*)#?\s*(.*)$", cleaned)
                flags = " ".join(m.group(1).replace('"', " ").split())
                entries.append({"flags": flags, "enabled": False,
                                **({"comment": m.group(2).strip()}
                                   if m.group(2).strip() else {})})
                in_header = False
            elif in_header:
                header.append(body)
            continue
        in_header = False
        # active line, maybe with trailing comment
        code, _, comment = line.partition("#")
        flags = " ".join(code.replace('"', " ").split())
        if not flags:
            continue
        e = {"flags": flags, "enabled": True}
        if comment.strip():
            e["comment"] = comment.strip()
        entries.append(e)
    return header, entries


def main():
    out = {"_doc": "Selection source of truth. Edit here, then run "
                   "render-flags.py. Order is significant (last-one-wins on "
                   "the final command line). Per-family override: add "
                   "\"gcc\": false or \"clang\": false to an entry.",
           "files": {}}
    for name in sorted(os.listdir(FLAGS_DIR)):
        if not name.endswith(".txt") or name.startswith("overrides-"):
            continue
        header, entries = parse_file(os.path.join(FLAGS_DIR, name))
        out["files"][name] = {}
        if header:
            out["files"][name]["header"] = header
        out["files"][name]["entries"] = entries
    with open(SELECTION, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    n = sum(len(v["entries"]) for v in out["files"].values())
    on = sum(1 for v in out["files"].values()
             for e in v["entries"] if e["enabled"])
    print(f"wrote {SELECTION}: {len(out['files'])} files, "
          f"{n} entries ({on} enabled, {n - on} excluded)")
    return 0


# --help / -h: print module docstring and exit (P101 uniform CLI help)
if __name__ == "__main__" and ("--help" in sys.argv or "-h" in sys.argv):
    print(__doc__ or __file__)
    raise SystemExit(0)

if __name__ == "__main__":
    raise SystemExit(main())
