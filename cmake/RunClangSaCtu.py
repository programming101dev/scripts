#!/usr/bin/env python3
"""RunClangSaCtu.py — Clang Static Analyzer WITH cross-translation-unit (CTU)
analysis, driven from compile_commands.json.

Per-file `--analyze` cannot see a bug that only appears when one TU calls a
function defined in another (e.g. main() passes 0 to a divide in lib.c). CTU
fixes that: a first pass emits an AST dump per TU and an external-definition
map; the analyze pass then follows calls into those ASTs.

Requires a clang whose companion `clang-extdef-mapping` is available and AST-
compatible (mainline/Homebrew LLVM ship it; Apple's stock clang does not).
The caller is responsible for passing a matched (clang, extdef-mapping) pair;
if unavailable it should fall back to the per-file analyzer instead.

Usage:
  RunClangSaCtu.py <clang> <extdef-mapping> <compile_commands.json>
                   <ctu_work_dir> <fail_on_diag 0|1> <source_root>
                   -- <analyzer args...>
Only sources under <source_root> are analyzed (in-tree only).
"""
import json, os, subprocess, sys

SRC_EXT = (".c", ".cc", ".cpp", ".cxx", ".m", ".mm")

def is_src(p): return os.path.splitext(p)[1].lower() in SRC_EXT

def strip_out(argv):
    out, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False; continue
        if a == "-c": continue
        if a == "-o": skip = True; continue
        if a.startswith("-o") and len(a) > 2: continue
        out.append(a)
    return out

def main():
    if "--" not in sys.argv:
        print("missing -- <analyzer args>", file=sys.stderr); return 2
    split = sys.argv.index("--")
    fixed, sa_args = sys.argv[1:split], sys.argv[split+1:]
    if len(fixed) != 6:
        print(__doc__, file=sys.stderr); return 2
    clang, extdef, db_path, work, fail_flag, root = fixed
    fail_on_diag = (fail_flag == "1")
    root = os.path.realpath(root)
    # absolute so the analyze pass resolves the AST dir no matter each TU's cwd
    ast_dir = os.path.realpath(os.path.join(work, "ast"))
    os.makedirs(ast_dir, exist_ok=True)

    with open(db_path, encoding="utf-8") as f:
        db = json.load(f)

    # in-tree TUs + their compile argv. A source compiled into more than one
    # target (e.g. shared between a library and an executable) appears in the
    # DB more than once; analyze each unique file ONCE, or the extdef map gets
    # duplicate keys ("multiple definitions ... for the same key in index").
    tus = []
    seen = set()
    for e in db:
        fpath = e.get("file", "")
        if not fpath or not is_src(fpath):
            continue
        real = os.path.realpath(fpath)
        if not real.startswith(root + os.sep) or real in seen:
            continue
        seen.add(real)
        argv = e["arguments"] if isinstance(e.get("arguments"), list) else __import__("shlex").split(e.get("command", ""))
        if argv:
            tus.append((real, e.get("directory") or None, argv))
    if not tus:
        return 0

    # pass 1: emit one AST per TU + collect extdef map lines
    map_lines = []
    for fpath, cwd, argv in tus:
        ast_out = os.path.join(ast_dir, fpath.lstrip("/").replace("/", "_") + ".ast")
        cmd = strip_out(argv) + ["-emit-ast", "-o", ast_out]
        subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # extdef-mapping prints "<len>:<usr> <sourcepath>"; repoint at the .ast
        # clang-extdef-mapping wants: <file> -- <compiler flags>
        flags = [a for a in argv[1:] if a.startswith(("-I", "-D", "-std", "-isystem", "-isysroot"))]
        r = subprocess.run([extdef, fpath, "--"] + flags,
                           cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in (r.stdout or "").splitlines():
            if " " in line:
                usr, src = line.split(" ", 1)
                map_lines.append((usr, ast_out))
    # one entry per key; a duplicate key aborts CTU with "multiple definitions
    # ... for the same key in index"
    seen_keys = set()
    deduped = []
    for usr, ast in map_lines:
        if usr in seen_keys:
            continue
        seen_keys.add(usr)
        deduped.append(f"{usr} {ast}")
    with open(os.path.join(ast_dir, "externalDefMap.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(deduped) + ("\n" if deduped else ""))

    # pass 2: analyze each TU with CTU enabled, following calls into the ASTs
    ctu_cfg = ["-Xanalyzer", "-analyzer-config", "-Xanalyzer",
               "experimental-enable-naive-ctu-analysis=true",
               "-Xanalyzer", "-analyzer-config", "-Xanalyzer", f"ctu-dir={ast_dir}"]
    # Flags that make --analyze emit "argument unused" noise (it doesn't
    # instrument), which -Werror would turn into a spurious failure. The build
    # already compiled these files cleanly, so dropping them changes nothing
    # the analyzer cares about.
    _DROP = ("-fsanitize", "-fno-sanitize", "-fprofile", "-fcoverage",
             "--coverage", "-fharden", "-finstrument", "-pg")

    def analyze_flags(argv):
        return [a for a in strip_out(argv)[1:]
                if not a.startswith(_DROP) and a != "-p"]

    have_diag = False
    for fpath, cwd, argv in tus:
        # drop the original compiler (argv[0]); keep this TU's flags + file
        cmd = [clang, "--analyze", "-Wno-unused-command-line-argument"] \
              + sa_args + ctu_cfg + analyze_flags(argv)
        r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out = (r.stdout or "").strip()
        if out:
            have_diag = True
            print(out)
    if have_diag and fail_on_diag:
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
