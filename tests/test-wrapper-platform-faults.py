#!/usr/bin/env python3
"""Regression tests for platform fault parsing, domains, and precedence."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from wrapper_fault_contract import (
    effective_fault_selection,
    has_explicit_platform_faults,
    has_documented_faults,
    injected_fault_cases,
)


def load_refresh_module():
    path = SCRIPTS_ROOT / "generators" / "refresh-wrapper-platform-faults.py"
    spec = importlib.util.spec_from_file_location(
        "refresh_wrapper_platform_faults",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_posix_parser(refresh) -> None:
    parser = refresh.PosixErrorsParser(
        "sample",
        {"EACCES", "EAGAIN", "EINTR"},
    )
    parser.feed(
        """
        <h4>ERRORS</h4>
        <p>The function shall fail if:</p>
        <dl><dt>[EACCES]</dt><dd>Denied.</dd></dl>
        <p>The function may fail if:</p>
        <dl><dt>[EAGAIN]</dt><dd>Try again.</dd></dl>
        <p>It shall not return [EINTR]. The errors specified for
        <a href="../functions/socket.html">socket()</a> also apply.</p>
        """
    )
    check(parser.shall_fail == {"EACCES"}, "POSIX shall-fail parsing drifted")
    check(parser.may_fail == {"EAGAIN"}, "POSIX may-fail parsing drifted")
    check("EINTR" not in parser.shall_fail, "negated prose became an errno")
    check("P101LINK_socket" in " ".join(parser.error_text), "reference lost")

    parser = refresh.PosixErrorsParser("hdestroy", {"ENOMEM"})
    parser.feed(
        """
        <h4>ERRORS</h4>
        <p>The <i>hcreate</i>() and <i>hsearch</i>() functions may fail if:</p>
        <dl><dt>[ENOMEM]</dt><dd>No memory.</dd></dl>
        """
    )
    check(
        not parser.may_fail,
        "a grouped POSIX page assigned a sibling function's errno",
    )


def test_roff_parser(refresh) -> None:
    errors, references = refresh.roff_error_details(
        """
.SH ERRORS
.TP
.B EACCES
Denied.
.IP [EAGAIN]
Try again.
.PP
The errors specified for
.BR socket (2)
also apply.
.SH SEE ALSO
.BR close (2)
""",
        "sample",
        {"EACCES", "EAGAIN", "EINTR"},
    )
    check(errors == ["EACCES", "EAGAIN"], "Linux roff term parsing drifted")
    check(references == ["socket"], "roff error reference parsing drifted")

    errors, _references = refresh.roff_error_details(
        """
.Sh ERRORS
.It Bq Er EINTR
Interrupted.
.Sh SEE ALSO
""",
        "sample",
        {"EINTR"},
    )
    check(errors == ["EINTR"], "BSD roff term parsing drifted")

    errors, _references = refresh.roff_error_details(
        """
.Sh NAME
.Nm hdestroy
.Nm hcreate
.Nm hsearch
.Sh ERRORS
The
.Fn hcreate
and
.Fn hsearch
functions may fail if:
.Bl -tag -width Er
.It Bq Er ENOMEM
No memory.
.El
""",
        "hdestroy",
        {"ENOMEM"},
    )
    check(
        not errors,
        "a grouped BSD manual assigned a sibling function's errno",
    )

    errors, _references = refresh.roff_error_details(
        """
.SH NAME
sync, syncfs \\- commit filesystem caches to disk
.SH ERRORS
.BR sync ()
is always successful.
.P
.BR syncfs ()
can fail:
.TP
.B EBADF
Bad descriptor.
""",
        "sync",
        {"EBADF"},
    )
    check(
        not errors,
        "a grouped Linux manual assigned syncfs errors to sync",
    )


def test_manual_locator(refresh) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manual = root / "man3" / "sample.3"
        manual.parent.mkdir()
        manual.write_text(
            ".Sh ERRORS\n.It Bq Er EINVAL\nInvalid.\n",
            encoding="utf-8",
        )
        record = refresh.platform_record(
            root,
            "sample",
            {"EINVAL"},
            "archive://manuals.txz",
        )
    check(record["source"] == "archive://manuals.txz", "collection URL drifted")
    check(record["source_path"] == "man3/sample.3", "manual path was lost")
    check(record["errors"] == ["EINVAL"], "manual errno was lost")


def test_platform_precedence() -> None:
    contract = {
        "functions": {
            "open": {
                "posix": {
                    "effective_errors": ["EACCES", "EINTR"],
                    "source": "posix://open",
                },
                "platforms": {
                    "linux": {
                        "status": "documented",
                        "errors": ["EACCES"],
                        "references": [],
                        "effective_errors": ["EACCES"],
                        "source": "linux://open",
                    },
                    "freebsd": {
                        "status": "no-manual",
                        "effective_errors": ["EACCES", "EINTR"],
                        "source": None,
                    },
                },
            },
            "voidish": {
                "posix": {
                    "effective_errors": [],
                    "source": "posix://voidish",
                },
                "platforms": {},
            },
            "silent": {
                "posix": {
                    "effective_errors": ["EINTR"],
                    "source": "posix://silent",
                },
                "platforms": {
                    "linux": {
                        "status": "documented",
                        "effective_errors": [],
                        "source": "linux://silent",
                    },
                },
            },
        }
    }
    errors, domain, kind, source, coverage = effective_fault_selection(
        contract,
        "open",
        "linux",
    )
    check(errors == ["EACCES"], "platform manual did not override POSIX")
    check(domain == "errno", "errno domain was lost")
    check(kind == "platform-manual", "platform source kind is wrong")
    check(source == "linux://open", "platform source is wrong")
    check(coverage == "exhaustive-symbolic", "coverage kind is wrong")

    errors, domain, kind, source, coverage = effective_fault_selection(
        contract,
        "open",
        "freebsd",
    )
    check(errors == ["EACCES", "EINTR"], "POSIX fallback was not selected")
    check(domain == "errno", "fallback errno domain was lost")
    check(kind == "posix-fallback", "fallback source kind is wrong")
    check(source == "posix://open", "fallback source is wrong")
    check(coverage == "exhaustive-symbolic", "fallback coverage is wrong")
    check(
        injected_fault_cases(contract, "voidish", "linux") == ["EIO"],
        "empty documented set must retain one instrumentation smoke case",
    )
    errors, domain, kind, source, coverage = effective_fault_selection(
        contract,
        "silent",
        "linux",
    )
    check(
        errors == ["EINTR"],
        "silent platform manual erased a POSIX-documented error",
    )
    check(domain == "errno", "silent-manual errno domain was lost")
    check(kind == "posix-fallback", "silent manual did not select POSIX")
    check(source == "posix://silent", "silent-manual source is wrong")
    check(
        coverage == "exhaustive-symbolic",
        "silent-manual coverage is wrong",
    )
    check(
        has_documented_faults(contract, "silent"),
        "silent platform page hid a documented POSIX failure",
    )
    check(
        not has_documented_faults(contract, "voidish"),
        "infallible API was incorrectly marked as documented-failure",
    )
    check(
        not has_explicit_platform_faults(contract, "silent", "linux"),
        "silent platform page was treated as explicit fault evidence",
    )


def test_system_fault_selection() -> None:
    contract = {
        "functions": {},
        "system_faults": {
            "getaddrinfo": {
                "coverage_kind": "exhaustive-symbolic",
                "posix": {
                    "codes": ["EAI_AGAIN", "EAI_FAIL"],
                    "source": "posix://getaddrinfo",
                },
                "platforms": {
                    "linux": {
                        "codes": ["EAI_AGAIN", "EAI_NONAME"],
                        "source_kind": "platform-manual",
                        "source": "linux://getaddrinfo",
                    },
                    "macos": {
                        "codes": ["EAI_AGAIN"],
                        "source_kind": "platform-manual",
                        "source": "macos://getaddrinfo",
                    },
                    "freebsd": {
                        "codes": ["EAI_FAIL"],
                        "source_kind": "platform-manual",
                        "source": "freebsd://getaddrinfo",
                    },
                },
            }
        },
    }
    codes, domain, kind, source, coverage = effective_fault_selection(
        contract,
        "getaddrinfo",
        "linux",
    )
    check(
        codes == ["EAI_AGAIN", "EAI_NONAME"],
        "platform system-error codes were not selected",
    )
    check(domain == "system", "system error domain was lost")
    check(kind == "platform-manual", "system source kind is wrong")
    check(source == "linux://getaddrinfo", "system source is wrong")
    check(coverage == "exhaustive-symbolic", "system coverage is wrong")

    codes, domain, kind, source, coverage = effective_fault_selection(
        contract,
        "getaddrinfo",
        None,
    )
    check(codes == ["EAI_AGAIN", "EAI_FAIL"], "POSIX system fallback drifted")
    check(domain == "system", "fallback system domain was lost")
    check(kind == "posix-fallback", "system fallback kind is wrong")
    check(source == "posix://getaddrinfo", "system fallback source is wrong")
    check(coverage == "exhaustive-symbolic", "fallback coverage is wrong")


def main() -> int:
    refresh = load_refresh_module()
    tests = (
        lambda: test_posix_parser(refresh),
        lambda: test_roff_parser(refresh),
        lambda: test_manual_locator(refresh),
        test_platform_precedence,
        test_system_fault_selection,
    )
    for test in tests:
        test()
    print(f"wrapper platform-fault contract tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
