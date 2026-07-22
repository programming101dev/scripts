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
