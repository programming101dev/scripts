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
    for a in argv:
        if skip:
            skip = False; continue
        if a == "-c": continue
        if a == "-o": skip = True; continue
        if a.startswith("-o") and len(a) > 2: continue
        out.append(a)
    return out

def extdef_flags(argv):
    """Keep compile flags needed for clang-extdef-mapping.

    Some important flags are option/value pairs in compile_commands.json:
    `-isysroot /path`, `-isystem /path`, `-I /path`, etc. A prefix-only
    filter keeps the option and drops the following value, which makes clang
    treat the next unrelated flag as the operand. On Darwin that turns
    `-D_POSIX_C_SOURCE=...` into the sysroot and system headers disappear.
    """
    out = []
    takes_value = {
        "-I", "-F", "-D", "-U", "-arch", "-target",
        "-isystem", "-isysroot", "-idirafter", "-iquote", "-include",
    }
    keep_prefixes = (
        "-I", "-F", "-D", "-U", "-std=", "-arch=", "-target=",
        "-isystem", "-isysroot", "-idirafter", "-iquote", "-include",
    )
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in takes_value:
            out.append(arg)
            if i + 1 < len(argv):
                out.append(argv[i + 1])
                i += 2
            else:
                i += 1
            continue
        if arg.startswith(keep_prefixes):
            out.append(arg)
        i += 1
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
    db_directory = os.path.dirname(os.path.realpath(db_path))

    # in-tree TUs + their compile argv. A source compiled into more than one
    # target (e.g. shared between a library and an executable) appears in the
    # DB more than once; analyze each unique file ONCE, or the extdef map gets
    # duplicate keys ("multiple definitions ... for the same key in index").
    tus = []
    seen = set()
    source_entries = 0
    for e in db:
        raw_file = e.get("file", "")
        if not raw_file or not is_src(raw_file):
            continue
        source_entries += 1
        directory = e.get("directory") or db_directory
        if not os.path.isabs(directory):
            directory = os.path.join(db_directory, directory)
        directory = os.path.realpath(directory)
        fpath = (
            raw_file
            if os.path.isabs(raw_file)
            else os.path.join(directory, raw_file)
        )
        real = os.path.realpath(fpath)
        if not real.startswith(root + os.sep) or real in seen:
            continue
        seen.add(real)
        argv = e["arguments"] if isinstance(e.get("arguments"), list) else __import__("shlex").split(e.get("command", ""))
        if argv:
            tus.append((real, directory, argv))
    if source_entries == 0:
        print("CTU analysis skipped: compile database has no translation units")
        return 0
    if not tus:
        print("CTU analysis found no in-tree translation units", file=sys.stderr)
        return 2

    # pass 1: emit one AST per TU + collect extdef map lines
    map_lines = []
    for fpath, cwd, argv in tus:
        ast_out = os.path.join(ast_dir, fpath.lstrip("/").replace("/", "_") + ".ast")
        cmd = strip_out(argv) + ["-emit-ast", "-o", ast_out]
        r = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
        )
        if r.returncode != 0:
            print(f"CTU emit-ast failed for {fpath} (exit {r.returncode})", file=sys.stderr)
            if r.stdout:
                print(r.stdout, file=sys.stderr)
            return r.returncode
        # extdef-mapping prints "<len>:<usr> <sourcepath>"; repoint at the .ast
        # clang-extdef-mapping wants: <file> -- <compiler flags>
        flags = extdef_flags(argv)
        r = subprocess.run(
            [extdef, fpath, "--"] + flags,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
        )
        if r.returncode != 0:
            print(f"CTU extdef mapping failed for {fpath} (exit {r.returncode})", file=sys.stderr)
            if r.stdout:
                print(r.stdout, file=sys.stderr)
            return r.returncode
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
        r = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
        )
        out = (r.stdout or "").strip()
        if out:
            have_diag = True
            print(out)
        if r.returncode != 0:
            print(f"CTU analyze failed for {fpath} (exit {r.returncode})", file=sys.stderr)
            return r.returncode
    if have_diag and fail_on_diag:
        return 1
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as error:
        print(
            f"CTU subprocess timed out after {error.timeout} seconds: "
            + " ".join(map(str, error.cmd)),
            file=sys.stderr,
        )
        raise SystemExit(2) from error
