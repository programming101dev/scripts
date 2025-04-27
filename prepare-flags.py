#!/usr/bin/env python3
import subprocess
import re
import os
import sys

STRICT_C_STANDARDS = ["c17"]
STRICT_CPP_STANDARDS = ["c++23"]

def get_output(command):
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)
        return output
    except subprocess.CalledProcessError as e:
        return e.output

def get_gcc_flags(compiler="gcc"):
    flags = set()
    sections = [
        "--help=common", "--help=optimizers", "--help=warnings", "--help=params",
        "--help=target", "--help=undocumented", "--help=language",
        "--help=analyzer", "--help=codegen", "--help=debug"
    ]
    for section in sections:
        output = get_output([compiler, section])
        flags.update(re.findall(r'(^|\s)(-[a-zA-Z0-9][^\s]*)', output))
    return set(flag for _, flag in flags)

def get_clang_flags(compiler="clang"):
    flags = set()
    outputs = [
        get_output([compiler, "--help"]),
        get_output([compiler, "--help-hidden"]),
    ]
    for output in outputs:
        flags.update(re.findall(r'(^|\s)(-[a-zA-Z0-9][^\s]*)', output))
    return set(flag for _, flag in flags)

def detect_compiler_family(compiler):
    try:
        output = subprocess.check_output([compiler, "--version"], text=True)
        if "clang" in output.lower():
            return "clang"
        elif "gcc" in output.lower() or "g++" in output.lower():
            return "gcc"
        else:
            return "unknown"
    except Exception:
        return "unknown"

def needs_manual_fix(flag):
    # Things that clearly need manual intervention
    if '<' in flag or '>' in flag:
        return True
    if re.search(r'\[[^\]]+\]', flag):
        return True
    if 'cpu' in flag.lower():
        return True
    if 'path' in flag.lower():
        return True
    if 'dir' in flag.lower():
        return True
    return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 prepare_flags.py <compiler1> [<compiler2> ...]")
        sys.exit(1)

    compilers = sys.argv[1:]
    os.makedirs("flag_results", exist_ok=True)

    for compiler in compilers:
        print(f"Detecting compiler: {compiler}")
        family = detect_compiler_family(compiler)

        if family == "gcc":
            print(f"  {compiler} looks like GCC")
            flags = get_gcc_flags(compiler)
        elif family == "clang":
            print(f"  {compiler} looks like Clang")
            flags = get_clang_flags(compiler)
        else:
            print(f"  {compiler} is unknown type, skipping...")
            flags = set()

        strict_flags = set()
        for flag in flags:
            if flag.startswith("-std="):
                strict_flags.update([f"-std={std}" for std in STRICT_C_STANDARDS + STRICT_CPP_STANDARDS])
            else:
                strict_flags.add(flag)

        manual_file = f"flag_results/{compiler}_manual_flags.txt"
        automatic_file = f"flag_results/{compiler}_automatic_flags.txt"

        with open(manual_file, "w") as mf, open(automatic_file, "w") as af:
            for flag in sorted(strict_flags):
                if needs_manual_fix(flag):
                    mf.write(f"{flag}\n")
                else:
                    af.write(f"{flag}\n")

        print(f"Generated: {manual_file} and {automatic_file}")

if __name__ == "__main__":
    main()

