#!/usr/bin/env python3
"""Regression tests for errno-manual parsing and platform precedence."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

from wrapper_errno_contract import (
    effective_error_selection,
    injected_error_cases,
)


SCRIPT_DIR = Path(__file__).resolve().parent


def load_refresh_module():
    path = SCRIPT_DIR / "refresh-wrapper-errno-contract.py"
    spec = importlib.util.spec_from_file_location(
        "refresh_wrapper_errno_contract",
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
        }
    }
    errors, kind, source = effective_error_selection(
        contract,
        "open",
        "linux",
    )
    check(errors == ["EACCES"], "platform manual did not override POSIX")
    check(kind == "platform-manual", "platform source kind is wrong")
    check(source == "linux://open", "platform source is wrong")

    errors, kind, source = effective_error_selection(
        contract,
        "open",
        "freebsd",
    )
    check(errors == ["EACCES", "EINTR"], "POSIX fallback was not selected")
    check(kind == "posix-fallback", "fallback source kind is wrong")
    check(source == "posix://open", "fallback source is wrong")
    check(
        injected_error_cases(contract, "voidish", "linux") == ["EIO"],
        "empty documented set must retain one instrumentation smoke case",
    )


def main() -> int:
    refresh = load_refresh_module()
    tests = (
        lambda: test_posix_parser(refresh),
        lambda: test_roff_parser(refresh),
        lambda: test_manual_locator(refresh),
        test_platform_precedence,
    )
    for test in tests:
        test()
    print(f"wrapper errno contract tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
