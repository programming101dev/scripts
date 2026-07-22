#!/usr/bin/env python3
import json, os, shlex, subprocess, sys

def is_source_file(p: str) -> bool:
    ext = os.path.splitext(p)[1].lower()
    return ext in [".c", ".cc", ".cpp", ".cxx", ".m", ".mm"]

def strip_output_flags(argv):
    out = []
    it = iter(range(len(argv)))
    skip_next = False
    for i in it:
        if skip_next:
            skip_next = False
            continue
        a = argv[i]
        if a == "-c":
            continue
        if a == "-o":
            skip_next = True
            continue
        if a.startswith("-o") and len(a) > 2:
            continue
        out.append(a)
    return out

def main():
    if len(sys.argv) != 4:
        print("Usage: RunAnalyzeFromCompileDB.py <compile_commands.json> <out_dir> <fail_on_diag 0|1>", file=sys.stderr)
        return 2

    db_path, out_dir, fail_flag = sys.argv[1], sys.argv[2], sys.argv[3]
    fail_on_diag = (fail_flag == "1")
    os.makedirs(out_dir, exist_ok=True)

    with open(db_path, "r", encoding="utf-8") as f:
        db = json.load(f)

    have_diag = False

    for entry in db:
        directory = entry.get("directory", "") or None
        file_ = entry.get("file", "")
        if not file_ or not is_source_file(file_):
            continue

        # Determine argv
        if "arguments" in entry and isinstance(entry["arguments"], list):
            argv = entry["arguments"]
        else:
            cmd = entry.get("command", "")
            argv = shlex.split(cmd)

        if not argv:
            continue

        # Convert to syntax-only while preserving TU flags/defs/includes
        argv = strip_output_flags(argv)
        if not argv:
            continue
        cmd = argv[:1] + ["-fsyntax-only"] + argv[1:]

        # Stable log filename
        relkey = file_.replace("/", "_").replace("\\", "_")
        out_txt = os.path.join(out_dir, f"{relkey}.txt")

        try:
            p = subprocess.run(
                cmd,
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            out = (p.stdout or "").strip()
        except Exception as e:
            out = f"Exception running analyze: {e}"

        with open(out_txt, "w", encoding="utf-8") as fo:
            fo.write(out + ("\n" if out else ""))

        if out:
            have_diag = True

    if have_diag:
        print("Analyzer (syntax-only) produced diagnostics. See:", out_dir)
        if fail_on_diag:
            return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
