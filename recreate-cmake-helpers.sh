#!/usr/bin/env bash
set -euo pipefail

# --help / -h -> description, exit 0 (P101 uniform CLI help)
case " $* " in
  *" --help "*|*" -h "*)
    cat <<'P101_USAGE'
recreate-cmake-helpers.sh — takes no command-line options; run with no arguments.
P101_USAGE
    exit 0 ;;
esac

# recreate-cmake-helpers.sh — materialize cmake/ helper scripts from embedded
# copies. In the workspace, link-cmake.sh symlinks each repo to scripts/cmake/
# (single source). This script is the fallback for a STANDALONE checkout that
# has no scripts/ sibling to link to, and doubles as a way to regenerate
# scripts/cmake/ if it is ever lost. Content is embedded and produced
# MECHANICALLY (from scripts/cmake/) — do not hand-edit; edit scripts/cmake/
# and regenerate with scripts/make-recreate-cmake-helpers.sh.

dest="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/cmake}"
mkdir -p "$dest"

cat > "$dest/FailIfClangSADiagnostics.cmake" <<'P101_EOF_FAILIFCLANGSADIAGNOSTICS_CMAKE'
cmake_minimum_required(VERSION 3.20)
if(NOT DEFINED DIR)
  message(FATAL_ERROR "Need -DDIR=<clang-sa logs dir>")
endif()
file(GLOB _FILES "${DIR}/*.txt")
set(_HAVE 0)
foreach(F ${_FILES})
  file(READ "${F}" C)
  string(STRIP "${C}" C)
  if(NOT C STREQUAL "")
    set(_HAVE 1)
    break()
  endif()
endforeach()
if(_HAVE)
  message(STATUS "Clang Static Analyzer produced diagnostics; showing up to first 200 lines:")
  set(_i 0)
  foreach(F ${_FILES})
    file(READ "${F}" C)
    string(REPLACE "\r\n" "\n" C "${C}")
    string(REPLACE "\r"   "\n" C "${C}")
    string(REPLACE "\n" ";" L "${C}")
    foreach(line ${L})
      if(_i GREATER_EQUAL 200)
        message(STATUS "... (truncated)")
        break()
      endif()
      message(STATUS "${line}")
      math(EXPR _i "${_i}+1")
    endforeach()
    if(_i GREATER_EQUAL 200)
      break()
    endif()
  endforeach()
  if("$ENV{P101_CLANG_SA_FAIL}" STREQUAL "1")
    message(FATAL_ERROR "CSA found diagnostics. See ${DIR}/*.txt")
  endif()
endif()
P101_EOF_FAILIFCLANGSADIAGNOSTICS_CMAKE

cat > "$dest/FailIfCppcheckDiagnostics.cmake" <<'P101_EOF_FAILIFCPPCHECKDIAGNOSTICS_CMAKE'
cmake_minimum_required(VERSION 3.20)
if(NOT DEFINED LOGFILE)
  message(FATAL_ERROR "Need -DLOGFILE=<path>")
endif()
if(NOT EXISTS "${LOGFILE}")
  message(FATAL_ERROR "cppcheck log not found: ${LOGFILE}")
endif()
file(READ "${LOGFILE}" _LOG)
string(REGEX MATCHALL ":[ \\t](warning|style|performance|portability|information|error):" _HITS "${_LOG}")
list(LENGTH _HITS _COUNT)
if(_COUNT GREATER 0)
  message(STATUS "cppcheck reported ${_COUNT} diagnostics. Showing first 200 lines:")
  string(REPLACE "\r\n" "\n" _LOG "${_LOG}")
  string(REPLACE "\r"   "\n" _LOG "${_LOG}")
  string(REPLACE "\n" ";" _LINES "${_LOG}")
  set(_i 0)
  foreach(_line IN LISTS _LINES)
    if(_i GREATER_EQUAL 200)
      message(STATUS "... (truncated)")
      break()
    endif()
    message(STATUS "${_line}")
    math(EXPR _i "${_i}+1")
  endforeach()
  message(FATAL_ERROR "cppcheck found issues. See ${LOGFILE}")
endif()
P101_EOF_FAILIFCPPCHECKDIAGNOSTICS_CMAKE

cat > "$dest/RunAnalyzeFromCompileDB.py" <<'P101_EOF_RUNANALYZEFROMCOMPILEDB_PY'
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
P101_EOF_RUNANALYZEFROMCOMPILEDB_PY
chmod +x "$dest/RunAnalyzeFromCompileDB.py"

cat > "$dest/RunClangTidyOverList.cmake" <<'P101_EOF_RUNCLANGTIDYOVERLIST_CMAKE'
cmake_minimum_required(VERSION 3.20)
if(NOT DEFINED CLANG_TIDY_EXEC OR NOT DEFINED DB OR NOT DEFINED FILES_CMAKE)
  message(FATAL_ERROR "Need -DCLANG_TIDY_EXEC=, -DDB=, and -DFILES_CMAKE=")
endif()
if(NOT EXISTS "${FILES_CMAKE}")
  message(FATAL_ERROR "FILES_CMAKE not found: ${FILES_CMAKE}")
endif()
include("${FILES_CMAKE}")

set(_args)
if(DEFINED ARGS_CMAKE AND NOT ARGS_CMAKE STREQUAL "")
  if(NOT EXISTS "${ARGS_CMAKE}")
    message(FATAL_ERROR "ARGS_CMAKE not found: ${ARGS_CMAKE}")
  endif()
  include("${ARGS_CMAKE}")
  if(DEFINED P101_TIDY_ARGS_LIST)
    set(_args ${P101_TIDY_ARGS_LIST})
  endif()
endif()

if(NOT DEFINED P101_TIDY_FILES_LIST)
  message(FATAL_ERROR "FILES_CMAKE did not define P101_TIDY_FILES_LIST")
endif()

set(_fail 0)
foreach(F IN LISTS P101_TIDY_FILES_LIST)
  if(F STREQUAL "")
    continue()
  endif()
  execute_process(
    COMMAND "${CLANG_TIDY_EXEC}" -p "${DB}" ${_args} "${F}"
    RESULT_VARIABLE _rv
    OUTPUT_VARIABLE _out
    ERROR_VARIABLE  _err
  )
  if(NOT _rv EQUAL 0)
    message(STATUS "clang-tidy failed for: ${F}")
    if(NOT _out STREQUAL "")
      message(STATUS "${_out}")
    endif()
    if(NOT _err STREQUAL "")
      message(STATUS "${_err}")
    endif()
    set(_fail 1)
  endif()
endforeach()

if(_fail)
  message(FATAL_ERROR "clang-tidy reported failures")
endif()
P101_EOF_RUNCLANGTIDYOVERLIST_CMAKE

cat > "$dest/SanitizeCompileCommands.py" <<'P101_EOF_SANITIZECOMPILECOMMANDS_PY'
#!/usr/bin/env python3
import json, os, shlex, sys

# NOTE: compiler -p (profiling) takes NO argument — never pair-drop it, or
# the token after it (an include dir, the source file, ...) vanishes from
# the tidy DB. Pair-dropping is reserved for flags that truly take a value.
DROP_EXACT = {"--coverage", "-coverage", "-pg", "-p"}
DROP_PAIR_FLAGS = set()

# -f flags that change language/ABI semantics: clang-tidy must see these or
# it analyzes different code than the compiler built. Everything else under
# -f (instrumentation, GCC-only codegen knobs, ...) is still dropped.
KEEP_F_PREFIXES = (
    "-fPIC", "-fpic", "-fPIE", "-fpie",
    "-fexceptions", "-fno-exceptions",
    "-frtti", "-fno-rtti",
    "-fvisibility",
    "-fsigned-char", "-funsigned-char",
    "-fshort-enums", "-fno-short-enums",
    "-ffreestanding", "-fno-builtin",
)

def should_drop(tok: str) -> bool:
    if tok in DROP_EXACT:
        return True
    if tok.startswith("-W"):
        return True
    if tok.startswith("-f"):
        return not tok.startswith(KEEP_F_PREFIXES)
    if tok.startswith("-g"):
        return True
    return False

def sanitize_args(argv):
    out = []
    skip_next = False
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a in DROP_PAIR_FLAGS:
            skip_next = True
            continue
        if should_drop(a):
            continue
        out.append(a)
    return out

def main():
    if len(sys.argv) != 3:
        print("Usage: SanitizeCompileCommands.py <in.json> <out.json>", file=sys.stderr)
        return 2

    in_path, out_path = sys.argv[1], sys.argv[2]
    with open(in_path, "r", encoding="utf-8") as f:
        db = json.load(f)

    for e in db:
        if "arguments" in e and isinstance(e["arguments"], list):
            argv = e["arguments"]
        else:
            cmd = e.get("command", "")
            argv = shlex.split(cmd)

        argv = sanitize_args(argv)
        e["arguments"] = argv
        if "command" in e:
            del e["command"]

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
        f.write("\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
P101_EOF_SANITIZECOMPILECOMMANDS_PY
chmod +x "$dest/SanitizeCompileCommands.py"

cat > "$dest/RunClangSaCtu.py" <<'P101_EOF_RUNCLANGSACTU_PY'
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
P101_EOF_RUNCLANGSACTU_PY
chmod +x "$dest/RunClangSaCtu.py"

echo "recreated cmake/ helpers in: $dest"
