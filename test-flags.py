#!/usr/bin/env python3
import subprocess
import os
import sys

def categorize_flag(flag):
    if flag.startswith("-Wanalyzer") or flag == "-fanalyzer":
        return "Analyzer"
    if flag.startswith("-W") and not flag.startswith("-Wanalyzer"):
        return "Warning"
    if flag.startswith("-O"):
        return "Optimization"
    if flag.startswith("-g"):
        return "Debugging"
    if flag.startswith("-fno-sanitize") or flag.startswith("-fsanitize"):
        return "Sanitizer"
    if flag.startswith("-f"):
        return "Codegen"
    return "Other"

def try_compile(compiler, flag, extra_flags, source):
    try:
        subprocess.check_output(
            [compiler, "-Werror", flag] + extra_flags + [source, "-o", "/tmp/test_output"],
            stderr=subprocess.DEVNULL
        )
        return True
    except subprocess.CalledProcessError:
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_flags.py <compiler1> [<compiler2> ...]")
        sys.exit(1)

    compilers = sys.argv[1:]
    os.makedirs("flag_results", exist_ok=True)

    with open("test.c", "w") as f:
        f.write("int main(void) { return 0; }\n")
    with open("test.cpp", "w") as f:
        f.write("int main() { return 0; }\n")

    for compiler in compilers:
        print(f"Testing compiler: {compiler}")

        all_flags = []
        for typ in ["manual", "automatic"]:
            filename = f"flag_results/{compiler}_{typ}_flags.txt"
            if os.path.exists(filename):
                with open(filename) as f:
                    all_flags.extend(line.strip() for line in f if line.strip())

        supported = {}
        unsupported = {}

        for flag in all_flags:
            if "c++" in flag:
                src = "test.cpp"
                extra = ["-std=c++23"]
            else:
                src = "test.c"
                extra = ["-std=c17"]

            if try_compile(compiler, flag, extra, src):
                cat = categorize_flag(flag)
                supported.setdefault(cat, []).append(flag)
            else:
                cat = categorize_flag(flag)
                unsupported.setdefault(cat, []).append(flag)

        sup_file = f"flag_results/{compiler}_supported_flags.txt"
        unsup_file = f"flag_results/{compiler}_unsupported_flags.txt"

        with open(sup_file, "w") as f:
            for cat in sorted(supported):
                f.write(f"# {cat} Flags\n")
                for flag in supported[cat]:
                    f.write(f"{flag}\n")
                f.write("\n")

        with open(unsup_file, "w") as f:
            for cat in sorted(unsupported):
                f.write(f"# {cat} Flags\n")
                for flag in unsupported[cat]:
                    f.write(f"{flag}\n")
                f.write("\n")

        print(f"Generated: {sup_file} and {unsup_file}")

if __name__ == "__main__":
    main()

