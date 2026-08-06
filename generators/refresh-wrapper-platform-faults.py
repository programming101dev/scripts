#!/usr/bin/env python3
"""Build the checked-in wrapper errno catalogue from authoritative manuals.

POSIX.1-2024 supplies the portable baseline.  A platform refresh reads that
platform's installed section 2/3 manuals and replaces the baseline for that
platform when a manual entry exists.  The resulting JSON is deterministic and
is consumed by the wrapper-unit-test generator; network access is only needed
when explicitly refreshing the cached POSIX HTML.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))
WORKSPACE = SCRIPTS_ROOT.parent
CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-platform-faults.json"
)
INSTRUMENTATION_PATH = SCRIPTS_ROOT / "contracts" / "instrumentation-contract.json"
POSIX_BASE = "https://pubs.opengroup.org/onlinepubs/9799919799"
POSIX_INDEX_URL = f"{POSIX_BASE}/idx/functions.html"
POSIX_ERRNO_URL = f"{POSIX_BASE}/basedefs/errno.h.html"

# Some manuals intentionally group several interfaces on one page but describe
# an ERRORS list for only one sibling without carrying machine-readable scope
# into each item. These reviewed exclusions prevent that prose ambiguity from
# becoming a false wrapper obligation.
PLATFORM_ERROR_OVERRIDES = {
    ("freebsd", "getpgrp"): {
        "errors": [],
        "reason": "The shared getpgrp(2) page assigns ESRCH to getpgid(), not getpgrp().",
    },
    ("linux", "freelocale"): {
        "errors": [],
        "reason": "The shared newlocale(3) page ERRORS list applies to locale creation, not freelocale().",
    },
}


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

# These interfaces report failures in their own return-code domains rather
# than through errno.  The lists below are reviewed extractions from the same
# POSIX and platform manuals admitted by this generator.  They are deliberately
# kept separate from errno: generated tests must assert P101_ERROR_SYSTEM and
# the native return code, not manufacture an errno failure.
SYSTEM_FAULT_CODES = {
    "getaddrinfo": {
        "coverage_kind": "exhaustive-symbolic",
        "posix": [
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NONAME",
            "EAI_SERVICE",
            "EAI_SOCKTYPE",
            "EAI_SYSTEM",
        ],
        "linux": [
            "EAI_ADDRFAMILY",
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NODATA",
            "EAI_NONAME",
            "EAI_SERVICE",
            "EAI_SOCKTYPE",
            "EAI_SYSTEM",
        ],
        "macos": [
            "EAI_ADDRFAMILY",
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_BADHINTS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NODATA",
            "EAI_NONAME",
            "EAI_OVERFLOW",
            "EAI_PROTOCOL",
            "EAI_SERVICE",
            "EAI_SOCKTYPE",
            "EAI_SYSTEM",
        ],
        "freebsd": [
            "EAI_ADDRFAMILY",
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_BADHINTS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NODATA",
            "EAI_NONAME",
            "EAI_OVERFLOW",
            "EAI_PROTOCOL",
            "EAI_SERVICE",
            "EAI_SOCKTYPE",
            "EAI_SYSTEM",
        ],
    },
    "getnameinfo": {
        "coverage_kind": "exhaustive-symbolic",
        "posix": [
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NONAME",
            "EAI_OVERFLOW",
            "EAI_SYSTEM",
        ],
        "linux": [
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NONAME",
            "EAI_OVERFLOW",
            "EAI_SYSTEM",
        ],
        "macos": [
            "EAI_ADDRFAMILY",
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_BADHINTS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NODATA",
            "EAI_NONAME",
            "EAI_OVERFLOW",
            "EAI_PROTOCOL",
            "EAI_SERVICE",
            "EAI_SOCKTYPE",
            "EAI_SYSTEM",
        ],
        "freebsd": [
            "EAI_ADDRFAMILY",
            "EAI_AGAIN",
            "EAI_BADFLAGS",
            "EAI_BADHINTS",
            "EAI_FAIL",
            "EAI_FAMILY",
            "EAI_MEMORY",
            "EAI_NODATA",
            "EAI_NONAME",
            "EAI_OVERFLOW",
            "EAI_PROTOCOL",
            "EAI_SERVICE",
            "EAI_SOCKTYPE",
            "EAI_SYSTEM",
        ],
    },
    "regcomp": {
        "coverage_kind": "exhaustive-symbolic",
        "posix": [
            "REG_BADBR",
            "REG_BADPAT",
            "REG_BADRPT",
            "REG_EBRACE",
            "REG_EBRACK",
            "REG_ECOLLATE",
            "REG_ECTYPE",
            "REG_EESCAPE",
            "REG_EPAREN",
            "REG_ERANGE",
            "REG_ESPACE",
            "REG_ESUBREG",
        ],
        "linux": [
            "REG_BADBR",
            "REG_BADPAT",
            "REG_BADRPT",
            "REG_EBRACE",
            "REG_EBRACK",
            "REG_ECOLLATE",
            "REG_ECTYPE",
            "REG_EEND",
            "REG_EESCAPE",
            "REG_EPAREN",
            "REG_ERANGE",
            "REG_ESIZE",
            "REG_ESPACE",
            "REG_ESUBREG",
        ],
        "macos": [
            "REG_ASSERT",
            "REG_BADBR",
            "REG_BADMAX",
            "REG_BADPAT",
            "REG_BADRPT",
            "REG_EBRACE",
            "REG_EBRACK",
            "REG_ECOLLATE",
            "REG_ECTYPE",
            "REG_EESCAPE",
            "REG_EMPTY",
            "REG_EPAREN",
            "REG_ERANGE",
            "REG_ESPACE",
            "REG_ESUBREG",
            "REG_ILLSEQ",
            "REG_INVARG",
        ],
        "freebsd": [
            "REG_ASSERT",
            "REG_BADBR",
            "REG_BADPAT",
            "REG_BADRPT",
            "REG_EBRACE",
            "REG_EBRACK",
            "REG_ECOLLATE",
            "REG_ECTYPE",
            "REG_EESCAPE",
            "REG_EMPTY",
            "REG_EPAREN",
            "REG_ERANGE",
            "REG_ESPACE",
            "REG_ESUBREG",
            "REG_ILLSEQ",
            "REG_INVARG",
        ],
    },
    "regexec": {
        "coverage_kind": "platform-documented-plus-smoke",
        "posix": [],
        "linux": [],
        "macos": [
            "REG_ASSERT",
            "REG_BADBR",
            "REG_BADMAX",
            "REG_BADPAT",
            "REG_BADRPT",
            "REG_EBRACE",
            "REG_EBRACK",
            "REG_ECOLLATE",
            "REG_ECTYPE",
            "REG_EESCAPE",
            "REG_EMPTY",
            "REG_EPAREN",
            "REG_ERANGE",
            "REG_ESPACE",
            "REG_ESUBREG",
            "REG_ILLSEQ",
            "REG_INVARG",
        ],
        "freebsd": [
            "REG_ASSERT",
            "REG_BADBR",
            "REG_BADPAT",
            "REG_BADRPT",
            "REG_EBRACE",
            "REG_EBRACK",
            "REG_ECOLLATE",
            "REG_ECTYPE",
            "REG_EESCAPE",
            "REG_EMPTY",
            "REG_EPAREN",
            "REG_ERANGE",
            "REG_ESPACE",
            "REG_ESUBREG",
            "REG_ILLSEQ",
            "REG_INVARG",
        ],
    },
    "glob": {
        "coverage_kind": "exhaustive-symbolic",
        "posix": ["GLOB_ABORTED", "GLOB_NOSPACE"],
        "linux": ["GLOB_ABORTED", "GLOB_NOSPACE"],
        "macos": ["GLOB_ABORTED", "GLOB_NOSPACE"],
        "freebsd": ["GLOB_ABORTED", "GLOB_NOSPACE"],
    },
    "wordexp": {
        "coverage_kind": "exhaustive-symbolic",
        "posix": [
            "WRDE_BADCHAR",
            "WRDE_BADVAL",
            "WRDE_CMDSUB",
            "WRDE_NOSPACE",
            "WRDE_SYNTAX",
        ],
        "linux": [
            "WRDE_BADCHAR",
            "WRDE_BADVAL",
            "WRDE_CMDSUB",
            "WRDE_NOSPACE",
            "WRDE_SYNTAX",
        ],
        "macos": [
            "WRDE_BADCHAR",
            "WRDE_BADVAL",
            "WRDE_CMDSUB",
            "WRDE_NOSPACE",
            "WRDE_SYNTAX",
        ],
        "freebsd": [
            "WRDE_BADCHAR",
            "WRDE_BADVAL",
            "WRDE_CMDSUB",
            "WRDE_NOSPACE",
            "WRDE_SYNTAX",
        ],
    },
    "fmtmsg": {
        "coverage_kind": "exhaustive-symbolic",
        "posix": ["MM_NOCON", "MM_NOMSG", "MM_NOTOK"],
        "linux": ["MM_NOCON", "MM_NOMSG", "MM_NOTOK"],
        "macos": ["MM_NOCON", "MM_NOMSG", "MM_NOTOK"],
        "freebsd": ["MM_NOCON", "MM_NOMSG", "MM_NOTOK"],
    },
}

# These APIs document a failure channel but not a finite portable code set.
# A representative code checks the complete wrapper failure class; the receipt
# keeps this distinct from exhaustive symbolic-code coverage.
UNBOUNDED_SYSTEM_FAULTS = {
    "dlclose": "dynamic-loader diagnostic",
    "dlopen": "dynamic-loader diagnostic",
    "dlsym": "dynamic-loader diagnostic",
    "fnmatch": "implementation-defined non-match error",
}

# Header ownership is explicit contract metadata.  Consumers must not infer a
# code's type or origin from its spelling (for example, an EAI_ prefix).
SYSTEM_FAULT_HEADERS = {
    "dlclose": "dlfcn.h",
    "dlopen": "dlfcn.h",
    "dlsym": "dlfcn.h",
    "fmtmsg": "fmtmsg.h",
    "fnmatch": "fnmatch.h",
    "getaddrinfo": "netdb.h",
    "getnameinfo": "netdb.h",
    "glob": "glob.h",
    "regcomp": "regex.h",
    "regexec": "regex.h",
    "wordexp": "wordexp.h",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def wrapper_inventory() -> list[dict[str, str]]:
    roles = json.loads(INSTRUMENTATION_PATH.read_text(encoding="utf-8"))[
        "library_roles"
    ]
    existing_bindings: dict[str, str | None] = {}
    if CONTRACT_PATH.is_file():
        existing_contract = json.loads(
            CONTRACT_PATH.read_text(encoding="utf-8")
        )
        for binding in existing_contract.get("wrappers", {}).values():
            function_usr = binding.get("function_usr")
            if isinstance(function_usr, str) and function_usr:
                existing_bindings[function_usr] = binding.get("function")
    inventory: list[dict[str, str]] = []
    for library, role in sorted(roles.items()):
        manifest = WORKSPACE / "libraries" / library / "api-manifest.tsv"
        if not manifest.is_file():
            continue
        for row in rows(manifest):
            wrapper = row["function"]
            function_usr = row.get("function_usr", "")
            if not function_usr:
                raise ValueError(
                    f"{manifest}: {wrapper} has no declaration identity"
                )
            native = existing_bindings.get(function_usr)
            if role == "native-wrapper" and not native:
                raise ValueError(
                    f"{wrapper} ({function_usr}) has no reviewed native "
                    "function mapping in the platform-fault contract"
                )
            inventory.append(
                {
                    "wrapper": wrapper,
                    "function_usr": function_usr,
                    "library": library,
                    "role": role,
                    "native": native,
                    "provenance": row.get("provenance", ""),
                }
            )
    return inventory


class FunctionIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.functions: dict[str, str] = {}

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        match = re.fullmatch(r"\.\./functions/([^/#]+)\.html", href)
        if match is not None:
            name = match.group(1)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                self.functions[name] = f"{POSIX_BASE}/functions/{name}.html"


class PosixErrorsParser(HTMLParser):
    def __init__(self, function: str, errno_names: set[str]) -> None:
        super().__init__()
        self.function = function
        self.errno_names = errno_names
        self.in_heading = False
        self.in_errors = False
        self.heading_text: list[str] = []
        self.in_error_term = False
        self.error_term: list[str] = []
        self.in_paragraph = False
        self.paragraph_text: list[str] = []
        self.applies = True
        self.mode = "shall_fail"
        self.shall_fail: set[str] = set()
        self.may_fail: set[str] = set()
        self.error_text: list[str] = []
        self.saw_errors_heading = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "h4":
            self.in_heading = True
            self.heading_text = []
            return
        if not self.in_errors:
            return
        if tag == "p":
            self.in_paragraph = True
            self.paragraph_text = []
            return
        if tag == "dt":
            self.in_error_term = True
            self.error_term = []
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            match = re.search(r"(?:^|/)([^/#]+)\.html(?:#.*)?$", href)
            if match is not None:
                self.error_text.append(f" P101LINK_{match.group(1)} ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "dt" and self.in_error_term:
            text = " ".join(self.error_term)
            if self.applies:
                for name in re.findall(r"\bE[A-Z0-9_]+\b", text):
                    if name not in self.errno_names:
                        continue
                    target = (
                        self.may_fail
                        if self.mode == "may_fail"
                        else self.shall_fail
                    )
                    target.add(name)
            self.in_error_term = False
            return
        if tag == "p" and self.in_paragraph:
            text = " ".join(" ".join(self.paragraph_text).split())
            lowered = text.lower()
            if "shall fail" in lowered or "may fail" in lowered:
                names = set(
                    re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\)", text)
                )
                self.applies = not names or self.function in names
                self.mode = (
                    "may_fail" if "may fail" in lowered else "shall_fail"
                )
            self.in_paragraph = False
            return
        if tag != "h4" or not self.in_heading:
            return
        heading = " ".join(self.heading_text).strip().upper()
        self.in_errors = heading == "ERRORS"
        self.saw_errors_heading = self.saw_errors_heading or self.in_errors
        self.in_heading = False

    def handle_data(self, data: str) -> None:
        if self.in_heading:
            self.heading_text.append(data)
            return
        if self.in_paragraph:
            self.paragraph_text.append(data)
        if self.in_error_term:
            self.error_term.append(data)
            return
        if not self.in_errors:
            return
        self.error_text.append(data)


def posix_index(path: Path) -> dict[str, str]:
    parser = FunctionIndexParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    return parser.functions


def posix_errno_names(path: Path) -> set[str]:
    return set(
        re.findall(
            r"\[(E[A-Z0-9_]+)\]",
            path.read_text(encoding="utf-8", errors="replace"),
        )
    )


def posix_record(
    function: str,
    url: str | None,
    page_dir: Path,
    errno_names: set[str],
) -> dict[str, Any]:
    if url is None:
        return {
            "status": "not-listed",
            "source": POSIX_INDEX_URL,
            "shall_fail": [],
            "may_fail": [],
            "references": [],
        }
    page = page_dir / f"{function}.html"
    if not page.is_file():
        raise FileNotFoundError(
            f"missing {page}; download {url} before generating the contract"
        )
    page_bytes = page.read_bytes()
    if len(page_bytes) < 256 or re.search(
        rb"</html\s*>", page_bytes, re.IGNORECASE
    ) is None:
        raise ValueError(f"incomplete cached POSIX page: {page}")
    parser = PosixErrorsParser(function, errno_names)
    parser.feed(page_bytes.decode("utf-8", errors="replace"))
    if not parser.saw_errors_heading:
        raise ValueError(
            f"cached POSIX page has no ERRORS heading: {page} ({url})"
        )
    references: set[str] = set()
    error_text = " ".join(" ".join(parser.error_text).split())
    for trigger in (
        "errors specified for",
        "errno values as described by",
        "errors described for",
    ):
        start = 0
        while True:
            index = error_text.lower().find(trigger, start)
            if index < 0:
                break
            sentence = error_text[index : index + 2000].split(".", 1)[0]
            references.update(
                re.findall(r"P101LINK_([A-Za-z_][A-Za-z0-9_]*)", sentence)
            )
            start = index + len(trigger)
    return {
        "status": "documented",
        "source": url,
        "shall_fail": sorted(parser.shall_fail),
        "may_fail": sorted(parser.may_fail - parser.shall_fail),
        "references": sorted(references - {function}),
        "source_bytes": len(page_bytes),
        "source_sha256": hashlib.sha256(page_bytes).hexdigest(),
    }


def read_manual(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
            return stream.read()
    return path.read_text(encoding="utf-8", errors="replace")


def manual_candidates(root: Path, function: str) -> list[Path]:
    candidates: list[Path] = []
    for section in ("2", "3"):
        directory = root / f"man{section}"
        if not directory.is_dir():
            continue
        candidates.extend(sorted(directory.glob(f"{function}.{section}")))
        candidates.extend(sorted(directory.glob(f"{function}.{section}.*")))
    return candidates


def roff_section(text: str, section: str) -> list[str]:
    selected: list[str] = []
    active = False
    for line in text.splitlines():
        heading = re.match(r"^\.(?:Sh|SH)\s+\"?([^\"].*?)\"?\s*$", line)
        if heading is not None:
            active = heading.group(1).strip().upper() == section
            continue
        if active:
            selected.append(line)
    return selected


def roff_interface_names(text: str) -> set[str]:
    lines = roff_section(text, "NAME")
    names = set(
        re.findall(
            r"^\.Nm\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            "\n".join(lines),
            re.MULTILINE,
        )
    )
    plain = " ".join(lines)
    if r"\-" in plain:
        plain = plain.split(r"\-", 1)[0]
    names.update(
        re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", plain)
    )
    return {
        name
        for name in names
        if name not in {"Nm", "Nd", "BR", "B", "TH"}
    }


def roff_called_names(lines: list[str]) -> set[str]:
    text = "\n".join(lines)
    names = set(
        re.findall(
            r"^\.(?:Fn|Nm)\s+\"?([A-Za-z_][A-Za-z0-9_]*)",
            text,
            re.MULTILINE,
        )
    )
    names.update(
        re.findall(
            r"^\.(?:BR|B)\s+\"?([A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\\fR)?\s*(?:\\?\(|\(\))",
            text,
            re.MULTILINE,
        )
    )
    names.update(
        re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\)", text)
    )
    return names


def roff_error_details(
    text: str,
    function: str,
    errno_names: set[str],
) -> tuple[list[str], list[str]]:
    error_lines = roff_section(text, "ERRORS")
    interface_names = roff_interface_names(text)
    section_interface_mentions = (
        roff_called_names(error_lines) & interface_names
    )
    section_applies = (
        not section_interface_mentions
        or function in section_interface_mentions
    )
    found: set[str] = set()
    references: set[str] = set()
    scope_lines: list[str] = []
    list_scope: set[str] | None = None
    pending_term = False
    current_errors: set[str] = set()
    current_description: list[str] = []

    def flush_error() -> None:
        nonlocal current_errors, current_description
        if not current_errors:
            return
        description_names = (
            roff_called_names(current_description) & interface_names
        )
        applies = (
            list_scope is None
            or not list_scope
            or function in list_scope
        )
        if (
            applies
            and description_names
            and function not in description_names
        ):
            applies = False
        if applies and section_applies:
            found.update(current_errors)
        current_errors = set()
        current_description = []

    for line in error_lines:
        if re.match(r"^\.(?:Pp|PP|LP|P)\b", line):
            flush_error()
            scope_lines = []
            list_scope = None
            pending_term = False
            continue
        if re.match(r"^\.Bl\b", line):
            list_scope = roff_called_names(scope_lines) & interface_names
            continue
        if re.match(r"^\.El\b", line):
            flush_error()
            scope_lines = []
            list_scope = None
            pending_term = False
            continue
        is_term = re.match(r"^\.(?:It|IP|TP)\b", line) is not None
        if is_term:
            flush_error()
            if list_scope is None:
                list_scope = roff_called_names(scope_lines) & interface_names
            line_errors = {
                name
                for name in re.findall(r"\bE[A-Z0-9_]+\b", line)
                if name in errno_names
            }
            current_errors = line_errors
            pending_term = not line_errors
            continue
        if pending_term:
            line_errors = {
                name
                for name in re.findall(r"\bE[A-Z0-9_]+\b", line)
                if name in errno_names
            }
            if line_errors:
                current_errors.update(line_errors)
                pending_term = False
                continue
        if current_errors:
            current_description.append(line)
        elif list_scope is None:
            scope_lines.append(line)
    flush_error()

    paragraphs = re.split(
        r"^\.(?:Pp|PP|LP|P)\b.*$",
        "\n".join(error_lines),
        flags=re.MULTILINE,
    )
    triggers = (
        "errors specified for",
        "errno values as described by",
        "errors described for",
    )
    for paragraph in paragraphs:
        normalized = " ".join(paragraph.lower().split())
        if not any(trigger in normalized for trigger in triggers):
            continue
        lines = paragraph.splitlines()
        mentioned = roff_called_names(lines) & interface_names
        if not section_applies or (mentioned and function not in mentioned):
            continue
        references.update(
            re.findall(
                r"^\.Xr\s+([A-Za-z_][A-Za-z0-9_]*)\s+[23]\b",
                paragraph,
                re.MULTILINE,
            )
        )
        references.update(
            re.findall(
                r"^\.(?:BR|B)\s+([A-Za-z_][A-Za-z0-9_]*)\s+\([23]\)",
                paragraph,
                re.MULTILINE,
            )
        )
    return sorted(found), sorted(references)


def platform_record(
    root: Path,
    function: str,
    errno_names: set[str],
    source_prefix: str,
) -> dict[str, Any]:
    candidates = manual_candidates(root, function)
    if not candidates:
        return {
            "status": "no-manual",
            "source": None,
            "source_path": None,
            "errors": [],
        }
    page = candidates[0]
    text = read_manual(page)
    redirect = re.match(r"^\.so\s+(man[23]/\S+)\s*$", text.strip())
    if redirect is not None:
        target = root / redirect.group(1)
        if target.is_file():
            page = target
            text = read_manual(page)
    errors, references = roff_error_details(text, function, errno_names)
    return {
        "status": "documented",
        "source": source_prefix,
        "source_path": str(page.relative_to(root)),
        "errors": errors,
        "references": references,
    }


def effective_posix_errors(
    function: str,
    records_by_function: dict[str, Any],
    visiting: set[str],
) -> set[str]:
    if function in visiting or function not in records_by_function:
        return set()
    record = records_by_function[function]["posix"]
    direct = set(record["shall_fail"]) | set(record["may_fail"])
    for reference in record["references"]:
        direct.update(
            effective_posix_errors(
                reference,
                records_by_function,
                visiting | {function},
            )
        )
    return direct


def explicit_platform_errors(
    function: str,
    platform_name: str,
    records_by_function: dict[str, Any],
    visiting: set[str],
) -> set[str]:
    if function in visiting or function not in records_by_function:
        return set()
    function_record = records_by_function[function]
    platform_record = function_record["platforms"].get(platform_name)
    if platform_record is None or platform_record["status"] != "documented":
        return set()
    effective = set(platform_record["errors"])
    for reference in platform_record.get("references", []):
        effective.update(
            explicit_platform_errors(
                reference,
                platform_name,
                records_by_function,
                visiting | {function},
            )
        )
    return effective


def effective_platform_errors(
    function: str,
    platform_name: str,
    records_by_function: dict[str, Any],
    visiting: set[str],
) -> set[str]:
    effective = explicit_platform_errors(
        function,
        platform_name,
        records_by_function,
        visiting,
    )
    platform_record = records_by_function.get(function, {}).get(
        "platforms", {}
    ).get(platform_name)
    reviewed_empty_override = (
        isinstance(platform_record, dict)
        and platform_record.get("status") == "documented"
        and "reviewed_override" in platform_record
    )
    if not effective and not reviewed_empty_override:
        effective.update(
            effective_posix_errors(function, records_by_function, set())
        )
    return effective


def curl_config(
    index: dict[str, str],
    page_dir: Path,
    functions: set[str],
) -> str:
    lines: list[str] = []
    page_dir.mkdir(parents=True, exist_ok=True)
    for function in sorted(functions & index.keys()):
        url = index[function]
        output = page_dir / f"{function}.html"
        if output.is_file():
            continue
        lines.extend(
            (f'url = "{url}"', f'output = "{output.with_suffix(".html.part")}"')
        )
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the wrapper platform-fault source of truth."
    )
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--errno-page", type=Path, required=True)
    parser.add_argument("--page-dir", type=Path, required=True)
    parser.add_argument("--curl-config", type=Path)
    parser.add_argument(
        "--platform",
        choices=("linux", "macos", "freebsd"),
    )
    parser.add_argument("--man-root", type=Path)
    parser.add_argument("--platform-source")
    parser.add_argument("--output", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args()

    index = posix_index(args.index)
    inventory = wrapper_inventory()
    native_functions = {
        item["native"]
        for item in inventory
        if item["role"] == "native-wrapper"
    }
    if args.curl_config is not None:
        atomic_write_text(
            args.curl_config,
            curl_config(index, args.page_dir, set(index)),
        )
        return 0

    for function in sorted(index):
        page = args.page_dir / f"{function}.html"
        partial = page.with_suffix(".html.part")
        if not partial.exists():
            continue
        partial_bytes = partial.read_bytes()
        if len(partial_bytes) < 256 or re.search(
            rb"</html\s*>", partial_bytes, re.IGNORECASE
        ) is None:
            raise ValueError(f"incomplete POSIX download: {partial}")
        os.replace(partial, page)

    errno_names = posix_errno_names(args.errno_page)
    # Keep the full indexed function set so ERRORS sections that delegate to
    # another interface (for example getifaddrs() -> socket()/malloc()) can be
    # resolved without losing transitive errors.  Native wrapper aliases that
    # are not in POSIX remain explicit not-listed records.
    functions = sorted(native_functions | index.keys())
    existing: dict[str, Any] = {}
    if args.output.is_file():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
    records_by_function: dict[str, Any] = {}
    for function in functions:
        prior_platforms = (
            existing.get("functions", {})
            .get(function, {})
            .get("platforms", {})
        )
        records_by_function[function] = {
            "posix": posix_record(
                function,
                index.get(function),
                args.page_dir,
                errno_names,
            ),
            "platforms": prior_platforms,
        }

    if args.platform is not None:
        if args.man_root is None or args.platform_source is None:
            parser.error(
                "--platform requires --man-root and --platform-source"
            )
        for function in functions:
            refreshed = platform_record(
                args.man_root,
                function,
                errno_names,
                args.platform_source,
            )
            override = PLATFORM_ERROR_OVERRIDES.get(
                (args.platform, function)
            )
            if override is not None and refreshed["status"] == "documented":
                refreshed["errors"] = override["errors"]
                refreshed["references"] = []
                refreshed["reviewed_override"] = override["reason"]
            records_by_function[function]["platforms"][
                args.platform
            ] = refreshed

    for function in functions:
        posix = records_by_function[function]["posix"]
        posix["effective_errors"] = sorted(
            effective_posix_errors(function, records_by_function, set())
        )
        for platform_name in ("linux", "macos", "freebsd"):
            platform_value = records_by_function[function]["platforms"].get(
                platform_name
            )
            if platform_value is None:
                platform_value = {
                    "status": "not-harvested",
                    "source": None,
                    "source_path": None,
                    "errors": [],
                    "references": [],
                }
                records_by_function[function]["platforms"][
                    platform_name
                ] = platform_value
            platform_value["effective_errors"] = sorted(
                effective_platform_errors(
                    function,
                    platform_name,
                    records_by_function,
                    set(),
                )
            )
            platform_has_explicit_faults = bool(
                explicit_platform_errors(
                    function,
                    platform_name,
                    records_by_function,
                    set(),
                )
            )
            platform_requires_posix_fallback = (
                platform_value["status"] == "documented"
                and not platform_has_explicit_faults
                and "reviewed_override" not in platform_value
                and bool(posix["effective_errors"])
            )
            if (
                platform_value["status"] == "documented"
                and not platform_requires_posix_fallback
            ):
                platform_value["effective_source_kind"] = "platform-manual"
                platform_value["effective_source"] = platform_value["source"]
                platform_value["effective_source_path"] = platform_value.get(
                    "source_path"
                )
            else:
                platform_value["effective_source_kind"] = "posix-fallback"
                platform_value["effective_source"] = posix["source"]
                platform_value["effective_source_path"] = None

    wrappers = {
        item["wrapper"]: {
            "function_usr": item["function_usr"],
            "library": item["library"],
            "role": item["role"],
            "function": (
                item["native"] if item["role"] == "native-wrapper" else None
            ),
            "provenance": item["provenance"],
        }
        for item in inventory
    }
    platform_coverage: dict[str, Any] = {}
    native_wrapper_functions = [
        binding["function"]
        for binding in wrappers.values()
        if binding["role"] == "native-wrapper"
        and binding["function"] in records_by_function
    ]
    for platform_name in ("linux", "macos", "freebsd"):
        manual_functions = sum(
            record["platforms"][platform_name][
                "effective_source_kind"
            ]
            == "platform-manual"
            for record in records_by_function.values()
        )
        manual_wrapper_count = sum(
            records_by_function[function]["platforms"][platform_name][
                "effective_source_kind"
            ]
            == "platform-manual"
            for function in native_wrapper_functions
        )
        sources = sorted(
            {
                record["platforms"][platform_name]["source"]
                for record in records_by_function.values()
                if record["platforms"][platform_name]["source"] is not None
            }
        )
        platform_coverage[platform_name] = {
            "authoritative_sources": sources,
            "manual_override_functions": manual_functions,
            "posix_fallback_functions": len(functions) - manual_functions,
            "manual_override_wrappers": manual_wrapper_count,
            "posix_fallback_wrappers": len(native_wrapper_functions)
            - manual_wrapper_count,
        }
    system_faults: dict[str, Any] = {}
    for function, specification in sorted(SYSTEM_FAULT_CODES.items()):
        function_record = records_by_function[function]
        posix_record_value = function_record["posix"]
        platform_values: dict[str, Any] = {}
        for platform_name in ("linux", "macos", "freebsd"):
            platform_record_value = function_record["platforms"][
                platform_name
            ]
            platform_values[platform_name] = {
                "codes": specification[platform_name],
                "source": platform_record_value["effective_source"],
                "source_kind": platform_record_value[
                    "effective_source_kind"
                ],
                "source_path": platform_record_value[
                    "effective_source_path"
                ],
            }
        system_faults[function] = {
            "coverage_kind": specification["coverage_kind"],
            "symbol_header": SYSTEM_FAULT_HEADERS[function],
            "posix": {
                "codes": specification["posix"],
                "source": posix_record_value["source"],
            },
            "platforms": platform_values,
        }
    for function, description in sorted(UNBOUNDED_SYSTEM_FAULTS.items()):
        function_record = records_by_function[function]
        posix_record_value = function_record["posix"]
        platform_values = {}
        for platform_name in ("linux", "macos", "freebsd"):
            platform_record_value = function_record["platforms"][
                platform_name
            ]
            platform_values[platform_name] = {
                "codes": ["EINVAL"],
                "source": platform_record_value["effective_source"],
                "source_kind": platform_record_value[
                    "effective_source_kind"
                ],
                "source_path": platform_record_value[
                    "effective_source_path"
                ],
            }
        system_faults[function] = {
            "coverage_kind": "representative-unbounded-class",
            "description": description,
            "symbol_header": SYSTEM_FAULT_HEADERS[function],
            "posix": {
                "codes": ["EINVAL"],
                "source": posix_record_value["source"],
            },
            "platforms": platform_values,
        }
    contract = {
        "schema": "p101-wrapper-platform-faults-v2",
        "standard": {
            "name": "POSIX.1-2024",
            "function_index": POSIX_INDEX_URL,
            "errno_header": POSIX_ERRNO_URL,
        },
        "platform_precedence": (
            "A documented platform manual replaces the POSIX set on that "
            "platform; POSIX is the fallback when no platform manual exists."
        ),
        "platform_coverage": platform_coverage,
        "errno_names": sorted(errno_names),
        "system_faults": system_faults,
        "functions": records_by_function,
        "wrappers": wrappers,
    }
    atomic_write_text(
        args.output,
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
    )
    print(
        f"wrapper platform-fault contract: {len(wrappers)} APIs, "
        f"{len(functions)} catalogued functions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
