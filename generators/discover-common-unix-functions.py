#!/usr/bin/env python3
"""Find common non-POSIX Unix functions for the portable functional libraries.

The intended workflow is:

  documented(Linux) ∩ documented(FreeBSD) ∩ documented(macOS)
      - POSIX interfaces
      - wrappers already present in the p101 libraries
      -> compile probes
      -> human classification

This script does the documentation harvest, set math, existing-wrapper
subtraction, and optional probe generation. It intentionally treats the result
as a backlog, not truth: the generated probes should be compiled on the real
target systems before a wrapper is assigned to its functional library.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


IDENT_RE = re.compile(r"^_?[a-z][A-Za-z0-9_]*$")
ROFF_FONT_RE = re.compile(r"\\f[PBIR]")
ROFF_ESCAPE_RE = re.compile(r"\\[&e~^|{}]")
TRAILING_MAN_SUFFIX_RE = re.compile(r"\.[0-9][A-Za-z0-9]*(?:\.(?:gz|bz2|xz))?$")



HTML_TAG_RE = re.compile(r"<[^>]+>")
POSIX_FUNCTION_HREF_RE = re.compile(r"(?:^|/)functions/([A-Za-z0-9_]+)\.html(?:#.*)?$")
POSIX_LOCAL_HREF_RE = re.compile(r"^([A-Za-z0-9_]+)\.html(?:#.*)?$")


class PosixLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.symbols: set[str] = set()
        self.hrefs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        href = dict(attrs).get("href")
        if href is None:
            return

        if POSIX_FUNCTION_HREF_RE.search(href) or POSIX_LOCAL_HREF_RE.search(href):
            self._href = href
            self._text = []
            self.hrefs.add(href)

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return

        text = html.unescape("".join(self._text)).strip()
        if is_identifier(text):
            self.symbols.add(text)

        self._href = None
        self._text = []


DEFAULT_WRAPPER_DIRS = (
    "libraries/lib_c",
    "libraries/lib_cli",
    "libraries/lib_database",
    "libraries/lib_diagnostics",
    "libraries/lib_dynamic_linking",
    "libraries/lib_filesystem",
    "libraries/lib_host",
    "libraries/lib_identity",
    "libraries/lib_io",
    "libraries/lib_ipc",
    "libraries/lib_locale",
    "libraries/lib_math",
    "libraries/lib_memory",
    "libraries/lib_network",
    "libraries/lib_process",
    "libraries/lib_random",
    "libraries/lib_search",
    "libraries/lib_sync",
    "libraries/lib_terminal",
    "libraries/lib_text",
    "libraries/lib_thread",
    "libraries/lib_time",
)

DEFAULT_EXCLUDED_SYMBOLS = frozenset(
    {
        # Obsolete BSD byte/string aliases; prefer the standard C functions.
        "bcmp",
        "bcopy",
        "bzero",
        "index",
        "rindex",
        # Legacy/unsafe interfaces.
        "getpass",
        "getw",
        "mktemp",
        "putw",
        # Old ctype-ish ASCII helpers.
        "isascii",
        "toascii",
        # Old BSD signal-mask APIs; prefer sigprocmask()/sigaction().
        "sigblock",
        "siginterrupt",
        "sigpause",
        "sigsetmask",
        "sigvec",
        # Old BSD integer-conversion names; prefer strtoll()/strtoull().
        "strtoq",
        "strtouq",
        # Historical exported globals.
        "sys_errlist",
        "sys_nerr",
        "sys_siglist",
    }
)

COMMON_PROBE_INCLUDES = (
    "#define _GNU_SOURCE 1",
    "#define _DEFAULT_SOURCE 1",
    "#define _BSD_SOURCE 1",
    "#define _DARWIN_C_SOURCE 1",
    "#define __BSD_VISIBLE 1",
    "#include <sys/types.h>",
    "#include <sys/param.h>",
    "#include <sys/stat.h>",
    "#include <sys/time.h>",
    "#include <sys/socket.h>",
    "#include <sys/sysctl.h>",
    "#include <sys/mman.h>",
    "#include <arpa/inet.h>",
    "#include <ctype.h>",
    "#include <dirent.h>",
    "#include <dlfcn.h>",
    "#include <err.h>",
    "#include <errno.h>",
    "#include <fcntl.h>",
    "#include <fts.h>",
    "#include <ftw.h>",
    "#include <grp.h>",
    "#include <ifaddrs.h>",
    "#include <inttypes.h>",
    "#include <libgen.h>",
    "#include <locale.h>",
    "#include <math.h>",
    "#include <netdb.h>",
    "#include <netinet/in.h>",
    "#include <pthread.h>",
    "#include <pwd.h>",
    "#include <regex.h>",
    "#include <resolv.h>",
    "#include <signal.h>",
    "#include <stdarg.h>",
    "#include <stdbool.h>",
    "#include <stddef.h>",
    "#include <stdint.h>",
    "#include <stdio.h>",
    "#include <stdlib.h>",
    "#include <string.h>",
    "#include <strings.h>",
    "#include <termios.h>",
    "#include <time.h>",
    "#include <unistd.h>",
    "#include <wchar.h>",
    "#include <wctype.h>",
)


@dataclass
class Hit:
    paths: set[str] = field(default_factory=set)
    sections: set[str] = field(default_factory=set)


def clean_roff(text: str) -> str:
    text = ROFF_FONT_RE.sub("", text)
    text = ROFF_ESCAPE_RE.sub("", text)
    text = text.replace("\\-", "-")
    text = text.replace("\\&", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_identifier(name: str) -> bool:
    return bool(IDENT_RE.match(name))


def man_section(path: Path) -> str | None:
    name = path.name
    for compressed_suffix in (".gz", ".bz2", ".xz"):
        if name.endswith(compressed_suffix):
            name = name[: -len(compressed_suffix)]
            break

    parts = name.split(".")
    if len(parts) < 2:
        return None

    section = parts[-1]
    if section not in {"2", "3"}:
        return None

    parent = path.parent.name
    if parent.startswith("man") and not parent.startswith(("man2", "man3")):
        return None

    return section


def symbol_from_filename(path: Path) -> str | None:
    name = TRAILING_MAN_SUFFIX_RE.sub("", path.name)
    if is_identifier(name):
        return name
    return None


def read_text(path: Path) -> str:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as file:
                return file.read()

        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def section_lines(lines: list[str], section_name: str) -> list[str]:
    wanted = section_name.upper()
    inside = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith(".SH ") or upper.startswith(".SH\t") or stripped.startswith(".Sh "):
            prefix_len = 4 if stripped.startswith(".Sh ") else 3
            title = clean_roff(stripped[prefix_len:]).strip('"').upper()
            inside = title == wanted
            continue

        if upper.startswith(".SH") or stripped.startswith(".Sh"):
            inside = False
            continue

        if inside:
            out.append(line)

    return out


def names_from_name_section(text: str, fallback: str | None) -> set[str]:
    lines = text.splitlines()
    name_lines = section_lines(lines, "NAME")
    names: set[str] = set()

    # mdoc style: .Nm foo, .Nm bar. A bare ".Nm" means the page name.
    for line in name_lines:
        stripped = line.strip()
        if stripped == ".Nm" and fallback:
            names.add(fallback)
            continue

        if stripped.startswith(".Nm "):
            rest = clean_roff(stripped[4:])
            rest = rest.split("-")[0]
            for part in re.split(r"[, ]+", rest):
                part = part.strip()
                if is_identifier(part):
                    names.add(part)

    # man style: "foo, bar - description"
    normalized = clean_roff("\n".join(name_lines))
    before_dash = normalized.split(" - ", 1)[0]
    before_dash = before_dash.split(" — ", 1)[0]
    before_dash = before_dash.split(" \\- ", 1)[0]

    for part in re.split(r"[, ]+", before_dash):
        part = part.strip()
        if is_identifier(part):
            names.add(part)

    return names


def looks_like_callable_doc(text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    if re.search(rf"\.(?:Fn|Fo)\s+{escaped}\b", text):
        return True

    normalized = clean_roff(text)
    return bool(re.search(rf"\b{escaped}\s*\(", normalized))


def harvest_manpages(root: Path) -> dict[str, Hit]:
    hits: dict[str, Hit] = {}

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        section = man_section(path)
        if section is None:
            continue

        fallback = symbol_from_filename(path)
        text = read_text(path)
        names = names_from_name_section(text, fallback)
        names = {name for name in names if looks_like_callable_doc(text, name)}

        if fallback and not names and looks_like_callable_doc(text, fallback):
            names.add(fallback)

        for name in names:
            hit = hits.setdefault(name, Hit())
            hit.paths.add(path.relative_to(root).as_posix())
            hit.sections.add(section)

    return hits



def clean_html_fragment(fragment: str) -> str:
    text = HTML_TAG_RE.sub(" ", fragment)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def names_from_posix_name_blocks(text: str) -> set[str]:
    names: set[str] = set()
    pattern = re.compile(
        r"<h[1-6][^>]*>.*?\bNAME\b.*?</h[1-6]>\s*<blockquote>(.*?)</blockquote>",
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(text):
        block = clean_html_fragment(match.group(1))
        before_description = re.split(r"\s+(?:—|-|\\-)\s+", block, maxsplit=1)[0]
        for part in re.split(r"[,\s]+", before_description):
            symbol = part.strip()
            if is_identifier(symbol):
                names.add(symbol)

    return names


def resolve_posix_href(source: Path, href: str) -> Path | None:
    href = href.split("#", 1)[0]
    if not href.endswith(".html"):
        return None

    candidates = [
        (source.parent / href).resolve(),
        (source.parent / href.rsplit("/", 1)[-1]).resolve(),
        (source.parent / "functions" / href.rsplit("/", 1)[-1]).resolve(),
        (source.parent.parent / "functions" / href.rsplit("/", 1)[-1]).resolve(),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def harvest_posix_html(paths: Iterable[Path]) -> set[str]:
    symbols: set[str] = set()
    seen: set[Path] = set()
    pending = [path.resolve() for path in paths]

    while pending:
        path = pending.pop()
        if path in seen or not path.exists() or not path.is_file():
            continue

        seen.add(path)
        text = read_text(path)
        symbols.update(names_from_posix_name_blocks(text))

        parser = PosixLinkParser()
        parser.feed(text)
        symbols.update(parser.symbols)

        for href in sorted(parser.hrefs):
            resolved = resolve_posix_href(path, href)
            if resolved is not None and resolved not in seen:
                pending.append(resolved)

    return symbols


def load_symbol_file(path: Path | None) -> set[str]:
    if path is None:
        return set()

    symbols: set[str] = set()
    with path.open(encoding="utf-8", errors="replace", newline="") as file:
        for raw in file:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue

            # Accept plain text, simple CSV, or copied interface-index rows.
            first = re.split(r"[,;\t ]+", line, maxsplit=1)[0]
            first = first.strip()
            if is_identifier(first):
                symbols.add(first)

    return symbols


def load_wrapped_native_functions(paths: Iterable[Path]) -> set[str]:
    wrappers: set[str] = set()

    for root in paths:
        if not root.exists():
            continue
        manifests = (
            [root / "api-manifest.tsv"]
            if (root / "api-manifest.tsv").is_file()
            else sorted(root.rglob("api-manifest.tsv"))
        )
        for manifest in manifests:
            with manifest.open(encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream, delimiter="\t"):
                    native = row.get("native_function", "")
                    native_usr = row.get("native_function_usr", "")
                    if native == "-" and native_usr == "-":
                        continue
                    if (
                        not is_identifier(native)
                        or native_usr != f"c:@F@{native}"
                    ):
                        raise ValueError(
                            f"{manifest}: invalid native semantic identity"
                        )
                    wrappers.add(native)

    return wrappers


def compact_paths(paths: set[str], limit: int) -> str:
    selected = sorted(paths)[:limit]
    extra = len(paths) - len(selected)
    out = "|".join(selected)
    if extra > 0:
        out = f"{out}|+{extra} more"
    return out


def status_for(
    symbol: str, posix: set[str], wrappers: set[str], excluded: set[str]
) -> str:
    if symbol in wrappers:
        return "already-wrapped"
    if symbol in posix:
        return "posix"
    if symbol in excluded:
        return "excluded-legacy"
    return "candidate"


def write_csv(
    output: Path,
    linux: dict[str, Hit],
    freebsd: dict[str, Hit],
    macos: dict[str, Hit],
    posix: set[str],
    wrappers: set[str],
    excluded: set[str],
    include_all: bool,
    evidence_limit: int,
) -> list[str]:
    common = set(linux) & set(freebsd) & set(macos)
    rows: list[dict[str, str]] = []
    candidates: list[str] = []

    for symbol in sorted(common):
        status = status_for(symbol, posix, wrappers, excluded)
        if status == "candidate":
            candidates.append(symbol)
        if not include_all and status != "candidate":
            continue

        rows.append(
            {
                "symbol": symbol,
                "status": status,
                "linux_sections": "|".join(sorted(linux[symbol].sections)),
                "freebsd_sections": "|".join(sorted(freebsd[symbol].sections)),
                "macos_sections": "|".join(sorted(macos[symbol].sections)),
                "linux_evidence": compact_paths(linux[symbol].paths, evidence_limit),
                "freebsd_evidence": compact_paths(freebsd[symbol].paths, evidence_limit),
                "macos_evidence": compact_paths(macos[symbol].paths, evidence_limit),
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as file:
        fieldnames = (
            "symbol",
            "status",
            "linux_sections",
            "freebsd_sections",
            "macos_sections",
            "linux_evidence",
            "freebsd_evidence",
            "macos_evidence",
        )
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return candidates


def emit_probes(symbols: Iterable[str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_dir = output_dir / "probes"
    probe_dir.mkdir(exist_ok=True)

    for old_probe in probe_dir.glob("*.c"):
        old_probe.unlink()

    for symbol in symbols:
        source = "\n".join(
            (
                *COMMON_PROBE_INCLUDES,
                "",
                "int main(void)",
                "{",
                f"    (void)&{symbol};",
                "    return 0;",
                "}",
                "",
            )
        )
        (probe_dir / f"{symbol}.c").write_text(source, encoding="utf-8")

    runner = """#!/usr/bin/env sh
set -eu

cc="${CC:-cc}"
out_dir="${1:-probe-results}"
mkdir -p "${out_dir}"

for source in probes/*.c; do
    symbol="$(basename "${source}" .c)"
    if "${cc}" -std=c17 -Werror "${source}" -o "${out_dir}/${symbol}" \
        >"${out_dir}/${symbol}.stdout" 2>"${out_dir}/${symbol}.stderr"; then
        printf '%s,yes\\n' "${symbol}"
    else
        printf '%s,no\\n' "${symbol}"
    fi
done
"""
    run_path = output_dir / "run-probes.sh"
    run_path.write_text(runner, encoding="utf-8")
    run_path.chmod(0o755)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover documented common non-POSIX Unix functions."
    )
    parser.add_argument("--linux", required=True, type=Path, help="Linux man-pages tree")
    parser.add_argument("--freebsd", required=True, type=Path, help="FreeBSD manpage/source tree")
    parser.add_argument("--macos", required=True, type=Path, help="macOS Libc/manpage tree")
    parser.add_argument(
        "--posix-symbols",
        type=Path,
        help="Optional POSIX interface symbol file, one symbol per line or first CSV column",
    )
    parser.add_argument(
        "--posix-html",
        action="append",
        type=Path,
        help=(
            "Optional POSIX HTML TOC or function page. If linked sibling pages "
            "exist locally, their NAME blocks are harvested too. May be repeated."
        ),
    )
    parser.add_argument(
        "--wrapper-root",
        action="append",
        type=Path,
        help=(
            "Library tree whose API manifests declare wrapped native "
            "identities; may be repeated"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("common-unix-functions.csv"),
        help="CSV output path",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Include POSIX, already-wrapped, and excluded common symbols in the CSV",
    )
    parser.add_argument(
        "--exclude-symbol",
        action="append",
        default=[],
        help="Additional symbol to exclude from candidates; may be repeated",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Do not apply the built-in legacy/unsafe symbol exclusion list",
    )
    parser.add_argument(
        "--emit-probes",
        type=Path,
        help="Directory for generated compile probes and run-probes.sh",
    )
    parser.add_argument(
        "--evidence-limit",
        type=int,
        default=3,
        help="Maximum evidence paths per OS per symbol in CSV",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    wrapper_roots = args.wrapper_root

    if wrapper_roots is None:
        repo_root = Path(__file__).resolve().parents[2]
        wrapper_roots = [repo_root / path for path in DEFAULT_WRAPPER_DIRS]

    linux = harvest_manpages(args.linux)
    freebsd = harvest_manpages(args.freebsd)
    macos = harvest_manpages(args.macos)
    posix = load_symbol_file(args.posix_symbols)
    if args.posix_html:
        posix.update(harvest_posix_html(args.posix_html))
    wrappers = load_wrapped_native_functions(wrapper_roots)
    excluded = set(args.exclude_symbol)
    if not args.no_default_excludes:
        excluded.update(DEFAULT_EXCLUDED_SYMBOLS)

    candidates = write_csv(
        args.output,
        linux,
        freebsd,
        macos,
        posix,
        wrappers,
        excluded,
        args.include_all,
        args.evidence_limit,
    )

    if args.emit_probes:
        emit_probes(candidates, args.emit_probes)

    common = set(linux) & set(freebsd) & set(macos)
    print(f"linux symbols: {len(linux)}")
    print(f"freebsd symbols: {len(freebsd)}")
    print(f"macos symbols: {len(macos)}")
    print(f"common documented symbols: {len(common)}")
    print(f"existing wrapped symbols: {len(wrappers)}")
    print(f"posix symbols subtracted: {len(posix)}")
    print(f"excluded legacy/unsafe symbols: {len(excluded)}")
    print(f"candidate symbols: {len(candidates)}")
    print(f"wrote: {args.output}")

    if args.emit_probes:
        print(f"wrote probes: {args.emit_probes}")

    if args.posix_symbols is None and not args.posix_html:
        print(
            "warning: no --posix-symbols file supplied; POSIX subtraction was skipped",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
