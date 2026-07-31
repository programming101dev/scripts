#!/usr/bin/env python3
"""Populate the functional p101 libraries from the four standards-based repos.

This is a one-way workspace migration aid.  The generated api-manifest.tsv
files are the durable ownership record; standards provenance is metadata, not
the repository boundary.
"""

from __future__ import annotations

import re
import shutil
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
LIBRARIES = WORKSPACE / "libraries"

DOMAINS = {
    "io": "Descriptor, stream, asynchronous, vectored, and multiplexed I/O",
    "filesystem": "Paths, directories, metadata, traversal, and filesystems",
    "memory": "Memory mapping, locking, advice, and aligned allocation",
    "process": "Process lifecycle, execution, signals, scheduling, and limits",
    "thread": "Thread lifecycle, attributes, cancellation, and thread-local state",
    "sync": "Mutexes, condition variables, read/write locks, and semaphores",
    "ipc": "Pipes, FIFOs, shared memory, and System V IPC",
    "network": "Sockets, address conversion, naming, and interfaces",
    "terminal": "Terminal and pseudo-terminal control",
    "time": "Clocks, calendar conversion, sleeping, and time parsing",
    "identity": "Users, groups, credentials, login records, and user shells",
    "text": "Strings, wide text, patterns, regular expressions, and word expansion",
    "locale": "Locales, message catalogs, language information, and conversion",
    "math": "Portable mathematical extensions",
    "search": "Portable table, tree, list, and queue search operations",
    "dynamic_linking": "Dynamic object loading and symbol lookup",
    "diagnostics": "Diagnostics, warnings, formatted messages, and system logging",
    "database": "Portable key/value database access",
    "cli": "Command-line option and suboption parsing",
    "random": "Portable random-number generation interfaces",
    "host": "Host identity, configuration, and load information",
}

PROVENANCE = {
    "lib_posix": "POSIX",
    "lib_posix_optional": "POSIX-optional",
    "lib_posix_xsi": "XSI",
    "lib_unix": "common-Unix",
}

# Implementation units that already have one clear functional owner.
SOURCE_DOMAIN = {
    # lib_posix
    "lib_posix/src/aio.c": "io",
    "lib_posix/src/arpa/inet.c": "network",
    "lib_posix/src/ctype.c": "text",
    "lib_posix/src/dirent.c": "filesystem",
    "lib_posix/src/dlfcn.c": "dynamic_linking",
    "lib_posix/src/fcntl.c": "io",
    "lib_posix/src/fnmatch.c": "filesystem",
    "lib_posix/src/glob.c": "filesystem",
    "lib_posix/src/grp.c": "identity",
    "lib_posix/src/iconv.c": "locale",
    "lib_posix/src/langinfo.c": "locale",
    "lib_posix/src/locale.c": "locale",
    "lib_posix/src/net/if.c": "network",
    "lib_posix/src/netdb.c": "network",
    "lib_posix/src/nl_types.c": "locale",
    "lib_posix/src/poll.c": "io",
    "lib_posix/src/pthread.c": "thread",
    "lib_posix/src/pwd.c": "identity",
    "lib_posix/src/regex.c": "text",
    "lib_posix/src/sched.c": "process",
    "lib_posix/src/semaphore.c": "sync",
    "lib_posix/src/setjmp.c": "process",
    "lib_posix/src/signal.c": "process",
    "lib_posix/src/stdio.c": "io",
    "lib_posix/src/stdlib.c": "process",
    "lib_posix/src/string.c": "text",
    "lib_posix/src/strings.c": "text",
    "lib_posix/src/sys/mman.c": "memory",
    "lib_posix/src/sys/select.c": "io",
    "lib_posix/src/sys/socket.c": "network",
    "lib_posix/src/sys/stat.c": "filesystem",
    "lib_posix/src/sys/statvfs.c": "filesystem",
    "lib_posix/src/sys/times.c": "process",
    "lib_posix/src/sys/utsname.c": "host",
    "lib_posix/src/sys/wait.c": "process",
    "lib_posix/src/termios.c": "terminal",
    "lib_posix/src/time.c": "time",
    "lib_posix/src/unistd.c": "process",
    "lib_posix/src/wchar.c": "text",
    "lib_posix/src/wctype.c": "text",
    "lib_posix/src/wordexp.c": "text",
    # lib_posix_optional
    "lib_posix_optional/src/pthread.c": "thread",
    "lib_posix_optional/src/sched.c": "process",
    "lib_posix_optional/src/spawn.c": "process",
    "lib_posix_optional/src/stdlib.c": "memory",
    "lib_posix_optional/src/sys/mman.c": "memory",
    # lib_posix_xsi
    "lib_posix_xsi/src/dirent.c": "filesystem",
    "lib_posix_xsi/src/fmtmsg.c": "diagnostics",
    "lib_posix_xsi/src/ftw.c": "filesystem",
    "lib_posix_xsi/src/libgen.c": "filesystem",
    "lib_posix_xsi/src/math.c": "math",
    "lib_posix_xsi/src/ndbm.c": "database",
    "lib_posix_xsi/src/search.c": "search",
    "lib_posix_xsi/src/signal.c": "process",
    "lib_posix_xsi/src/string.c": "text",
    "lib_posix_xsi/src/strings.c": "text",
    "lib_posix_xsi/src/sys/ipc.c": "ipc",
    "lib_posix_xsi/src/sys/mman.c": "memory",
    "lib_posix_xsi/src/sys/msg.c": "ipc",
    "lib_posix_xsi/src/sys/resource.c": "process",
    "lib_posix_xsi/src/sys/sem.c": "ipc",
    "lib_posix_xsi/src/sys/shm.c": "ipc",
    "lib_posix_xsi/src/sys/stat.c": "filesystem",
    "lib_posix_xsi/src/sys/uio.c": "io",
    "lib_posix_xsi/src/syslog.c": "diagnostics",
    "lib_posix_xsi/src/time.c": "time",
    "lib_posix_xsi/src/utmpx.c": "identity",
    "lib_posix_xsi/src/wchar.c": "text",
    # lib_unix
    "lib_unix/src/arpa/inet.c": "network",
    "lib_unix/src/err.c": "diagnostics",
    "lib_unix/src/getopt.c": "cli",
    "lib_unix/src/ifaddrs.c": "network",
    "lib_unix/src/net/ethernet.c": "network",
    "lib_unix/src/stdio.c": "io",
    "lib_unix/src/string.c": "text",
    "lib_unix/src/termios.c": "terminal",
}

# Function-level ownership for implementation units that cross domains.
FUNCTION_DOMAIN: dict[str, str] = {}


def assign(domain: str, *names: str) -> None:
    for name in names:
        previous = FUNCTION_DOMAIN.setdefault(name, domain)
        if previous != domain:
            raise ValueError(f"{name} assigned to both {previous} and {domain}")


assign(
    "thread",
    "p101_pthread_atfork",
    "p101_pthread_attr_destroy",
    "p101_pthread_attr_getdetachstate",
    "p101_pthread_attr_getguardsize",
    "p101_pthread_attr_getinheritsched",
    "p101_pthread_attr_getschedparam",
    "p101_pthread_attr_getschedpolicy",
    "p101_pthread_attr_getscope",
    "p101_pthread_attr_getstack",
    "p101_pthread_attr_getstacksize",
    "p101_pthread_attr_init",
    "p101_pthread_attr_setdetachstate",
    "p101_pthread_attr_setguardsize",
    "p101_pthread_attr_setinheritsched",
    "p101_pthread_attr_setschedparam",
    "p101_pthread_attr_setschedpolicy",
    "p101_pthread_attr_setscope",
    "p101_pthread_attr_setstack",
    "p101_pthread_attr_setstacksize",
    "p101_pthread_cancel",
    "p101_pthread_create",
    "p101_pthread_detach",
    "p101_pthread_equal",
    "p101_pthread_exit",
    "p101_pthread_getschedparam",
    "p101_pthread_getspecific",
    "p101_pthread_join",
    "p101_pthread_key_create",
    "p101_pthread_key_delete",
    "p101_pthread_kill",
    "p101_pthread_self",
    "p101_pthread_setcancelstate",
    "p101_pthread_setcanceltype",
    "p101_pthread_setschedparam",
    "p101_pthread_setspecific",
    "p101_pthread_sigmask",
    "p101_pthread_testcancel",
)
assign(
    "sync",
    "p101_pthread_cond_broadcast",
    "p101_pthread_cond_destroy",
    "p101_pthread_cond_init",
    "p101_pthread_cond_signal",
    "p101_pthread_cond_timedwait",
    "p101_pthread_cond_wait",
    "p101_pthread_condattr_destroy",
    "p101_pthread_condattr_getpshared",
    "p101_pthread_condattr_init",
    "p101_pthread_condattr_setpshared",
    "p101_pthread_mutex_destroy",
    "p101_pthread_mutex_getprioceiling",
    "p101_pthread_mutex_init",
    "p101_pthread_mutex_lock",
    "p101_pthread_mutex_setprioceiling",
    "p101_pthread_mutex_trylock",
    "p101_pthread_mutex_unlock",
    "p101_pthread_mutexattr_destroy",
    "p101_pthread_mutexattr_getprioceiling",
    "p101_pthread_mutexattr_getprotocol",
    "p101_pthread_mutexattr_getpshared",
    "p101_pthread_mutexattr_gettype",
    "p101_pthread_mutexattr_init",
    "p101_pthread_mutexattr_setprioceiling",
    "p101_pthread_mutexattr_setprotocol",
    "p101_pthread_mutexattr_setpshared",
    "p101_pthread_mutexattr_settype",
    "p101_pthread_once",
    "p101_pthread_rwlock_destroy",
    "p101_pthread_rwlock_init",
    "p101_pthread_rwlock_rdlock",
    "p101_pthread_rwlock_tryrdlock",
    "p101_pthread_rwlock_trywrlock",
    "p101_pthread_rwlock_unlock",
    "p101_pthread_rwlock_wrlock",
    "p101_pthread_rwlockattr_destroy",
    "p101_pthread_rwlockattr_getpshared",
    "p101_pthread_rwlockattr_init",
    "p101_pthread_rwlockattr_setpshared",
)

assign("filesystem", "p101_renameat")
assign("process", "p101_pclose", "p101_popen")

assign("cli", "p101_getsubopt", "p101_getopt")
assign("filesystem", "p101_mkdtemp", "p101_mkstemp")
assign("process", "p101_setenv", "p101_unsetenv")

assign(
    "filesystem",
    "p101_access",
    "p101_chdir",
    "p101_chown",
    "p101_faccessat",
    "p101_fchdir",
    "p101_fchown",
    "p101_fchownat",
    "p101_fpathconf",
    "p101_ftruncate",
    "p101_getcwd",
    "p101_lchown",
    "p101_link",
    "p101_linkat",
    "p101_pathconf",
    "p101_readlink",
    "p101_readlinkat",
    "p101_rmdir",
    "p101_symlink",
    "p101_symlinkat",
    "p101_truncate",
    "p101_unlink",
    "p101_unlinkat",
)
assign(
    "io",
    "p101_close",
    "p101_dup",
    "p101_dup2",
    "p101_lockf",
    "p101_lseek",
    "p101_pread",
    "p101_pwrite",
    "p101_read",
    "p101_write",
)
assign(
    "process",
    "p101_alarm",
    "p101_execv",
    "p101_execve",
    "p101_execvp",
    "p101_fork",
    "p101_getpgid",
    "p101_getpgrp",
    "p101_getpid",
    "p101_getppid",
    "p101_getsid",
    "p101_nice",
    "p101_pause",
    "p101_posix_exit_immediately",
    "p101_setpgid",
    "p101_setsid",
    "p101_sleep",
)
assign(
    "identity",
    "p101_crypt",
    "p101_getegid",
    "p101_geteuid",
    "p101_getgid",
    "p101_getgroups",
    "p101_getlogin_r",
    "p101_getuid",
    "p101_setegid",
    "p101_seteuid",
    "p101_setgid",
    "p101_setregid",
    "p101_setreuid",
    "p101_setuid",
)
assign("host", "p101_confstr", "p101_gethostid", "p101_gethostname", "p101_sysconf")
assign("terminal", "p101_isatty", "p101_tcgetpgrp", "p101_tcsetpgrp", "p101_ttyname_r")
assign("ipc", "p101_pipe")
assign("text", "p101_swab")
assign("filesystem", "p101_sync")

assign("thread", "p101_pthread_kill", "p101_pthread_sigmask")

assign("ipc", "p101_mkfifo", "p101_shm_open", "p101_shm_unlink")
assign(
    "memory",
    "p101_mlock",
    "p101_mlockall",
    "p101_munlock",
    "p101_munlockall",
    "p101_posix_madvise",
)

assign("text", "p101_a64l", "p101_l64a")
assign("terminal", "p101_grantpt", "p101_posix_openpt", "p101_ptsname", "p101_unlockpt")
assign("process", "p101_putenv")
assign("filesystem", "p101_realpath")
assign("random", "p101_initstate", "p101_seed48", "p101_setstate", "p101_srand48", "p101_srandom")

assign("random", "p101_arc4random", "p101_arc4random_buf", "p101_arc4random_uniform")
assign("host", "p101_getloadavg", "p101_getdomainname", "p101_setdomainname")
assign("text", "p101_rpmatch")
assign("identity", "p101_endusershell", "p101_getusershell", "p101_setusershell")

MIXED_SOURCES = {
    "lib_posix/src/pthread.c",
    "lib_posix/src/signal.c",
    "lib_posix/src/stdio.c",
    "lib_posix/src/stdlib.c",
    "lib_posix/src/sys/stat.c",
    "lib_posix/src/unistd.c",
    "lib_posix_optional/src/pthread.c",
    "lib_posix_optional/src/sys/mman.c",
    "lib_posix_xsi/src/stdlib.c",
    "lib_posix_xsi/src/unistd.c",
    "lib_unix/src/stdlib.c",
    "lib_unix/src/unistd.c",
}

PLACEHOLDERS = {
    "filesystem": [
        ("lib_unix/include/p101_unix/p101_fstab.h", "header"),
        ("lib_unix/include/p101_unix/p101_ftw.h", "header"),
        ("lib_unix/include/p101_unix/sys/p101_mount.h", "header"),
        ("lib_unix/src/fstab.c", "source"),
        ("lib_unix/src/ftw.c", "source"),
        ("lib_unix/src/sys/mount.c", "source"),
    ],
    "network": [
        ("lib_unix/include/p101_unix/p101_resolv.h", "header"),
        ("lib_unix/include/p101_unix/arpa/p101_nameser.h", "header"),
        ("lib_unix/src/resolv.c", "source"),
        ("lib_unix/src/arpa/nameser.c", "source"),
    ],
    "terminal": [
        ("lib_unix/include/p101_unix/p101_ttyent.h", "header"),
        ("lib_unix/src/ttyent.c", "source"),
    ],
    "host": [
        ("lib_unix/include/p101_unix/sys/p101_sysctl.h", "header"),
        ("lib_unix/include/p101_unix/sys/p101_timex.h", "header"),
        ("lib_unix/src/sys/sysctl.c", "source"),
        ("lib_unix/src/sys/timex.c", "source"),
    ],
}
SKIPPED_SOURCE_KEYS = {
    path for rows in PLACEHOLDERS.values() for path, kind in rows if kind == "source"
}

FUNCTION_START = re.compile(
    r"(?m)^[A-Za-z_][^\n;{}]*\b(p101_[A-Za-z0-9_]+)\s*\("
)
INCLUDE = re.compile(r"(?m)^\s*#include\s+<([^>]+)>")


def source_functions(text: str) -> list[tuple[str, int, int, int]]:
    """Return (name, start, body_end, leading_start) for p101 definitions."""
    matches = list(FUNCTION_START.finditer(text))
    found: list[tuple[str, int, int, int]] = []
    previous_end = 0
    for match in matches:
        brace = text.find("{", match.end())
        semicolon = text.find(";", match.end(), brace if brace >= 0 else None)
        if brace < 0 or semicolon >= 0:
            continue
        depth = 0
        i = brace
        in_string: str | None = None
        escaped = False
        line_comment = False
        block_comment = False
        while i < len(text):
            char = text[i]
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if line_comment:
                if char == "\n":
                    line_comment = False
            elif block_comment:
                if char == "*" and nxt == "/":
                    block_comment = False
                    i += 1
            elif in_string is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == in_string:
                    in_string = None
            elif char == "/" and nxt == "/":
                line_comment = True
                i += 1
            elif char == "/" and nxt == "*":
                block_comment = True
                i += 1
            elif char in {'"', "'"}:
                in_string = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    while end < len(text) and text[end] in " \t\r\n":
                        end += 1
                    found.append((match.group(1), match.start(), end, previous_end))
                    previous_end = end
                    break
            i += 1
    return found


def transform_source(text: str, domain: str) -> str:
    text = re.sub(
        r'#include\s+[<"]p101_(?:posix|posix_optional|posix_xsi|unix)/[^>"]+[>"]',
        f'#include "p101_{domain}/{domain}.h"',
        text,
    )
    text = re.sub(
        r'#include\s+"(?:\.\./)*p101_(?:posix|posix_optional|posix_xsi|unix)_internal\.h"',
        "#include <p101_env/wrapper.h>",
        text,
    )
    replacements = {
        "P101_POSIX_OPTIONAL_FAULT_RETURN_CODE": "P101_WRAPPER_FAULT_RETURN_CODE",
        "P101_POSIX_OPTIONAL_FAULT_RETURN": "P101_WRAPPER_FAULT_RETURN",
        "P101_POSIX_XSI_FAULT_RETURN": "P101_WRAPPER_FAULT_RETURN",
        "P101_POSIX_FAULT_RETURN_CODE": "P101_WRAPPER_FAULT_RETURN_CODE",
        "P101_POSIX_FAULT_RETURN": "P101_WRAPPER_FAULT_RETURN",
        "P101_UNIX_FAULT_RETURN": "P101_WRAPPER_FAULT_RETURN",
        "p101_posix_short_count": "p101_wrapper_short_count",
        "P101_POSIX_TRACK_POINTER_ACQUIRE": "P101_TRACK_POINTER_RESOURCE_ACQUIRE",
        "P101_POSIX_TRACK_POINTER_RELEASE": "P101_TRACK_POINTER_RESOURCE_RELEASE",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def remove_static_function(text: str, name: str) -> str:
    match = re.search(
        rf"(?m)^static[^\n;]*\b{re.escape(name)}\s*\([^;]*?\)\s*\{{",
        text,
    )
    if match is None:
        return text
    opening = match.end() - 1
    depth = 0
    index = opening
    while index < len(text):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                while end < len(text) and text[end] in " \t\r\n":
                    end += 1
                return text[: match.start()] + text[end:]
        index += 1
    raise ValueError(f"unterminated body for static function {name}")


def prune_unused_static_functions(text: str) -> str:
    changed = True
    while changed:
        changed = False
        names = re.findall(r"(?m)^static[^\n;]*\b([A-Za-z_]\w*)\s*\([^;]*?\)\s*\{", text)
        for name in names:
            without_definition = remove_static_function(text, name)
            declaration = re.compile(
                rf"(?m)^static[^\n]*\b{re.escape(name)}\s*\([^;]*;\s*\n?"
            )
            without_declaration = declaration.sub("", without_definition)
            if re.search(rf"\b{re.escape(name)}\b", without_declaration) is None:
                text = without_declaration
                changed = True
                break
    return text


def prune_pthread_preamble(text: str, domain: str) -> str:
    if domain == "thread":
        for helper in ("pthread_track_held", "pthread_track_pointer_wait"):
            text = remove_static_function(text, helper)
        rejected = ("MUTEX", "RWLOCK", "TRACK_WAIT_ACQUIRE", "TRACK_WAIT_RELEASE")
    elif domain == "sync":
        for helper in ("pthread_track_joinable", "pthread_track_join_wait"):
            text = remove_static_function(text, helper)
        rejected = ("JOINABLE", "JOIN_WAIT")
    else:
        return text
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not (
            line.startswith("#define P101_PTHREAD_TRACK_")
            and any(token in line for token in rejected)
        )
    )


def header_for_function(repo: str, name: str) -> Path:
    candidates = []
    for header in (LIBRARIES / repo / "include").rglob("*.h"):
        text = header.read_text()
        if re.search(rf"\b{re.escape(name)}\s*\(", text):
            candidates.append(header)
    if len(candidates) != 1:
        joined = ", ".join(str(path) for path in candidates)
        raise ValueError(f"{repo}:{name} has {len(candidates)} headers: {joined}")
    return candidates[0]


def prototype(text: str, name: str) -> str:
    match = re.search(
        rf"(?m)^[ \t]*[A-Za-z_][^\n]*\b{re.escape(name)}\s*\(",
        text,
    )
    if match is None:
        raise ValueError(f"declaration not found for {name}")
    end = text.find(";", match.end())
    if end < 0:
        raise ValueError(f"declaration terminator not found for {name}")
    return text[match.start() : end + 1].strip()


def source_owner(source_key: str, name: str) -> str:
    if name in FUNCTION_DOMAIN:
        return FUNCTION_DOMAIN[name]
    try:
        return SOURCE_DOMAIN[source_key]
    except KeyError as exc:
        if source_key in MIXED_SOURCES:
            raise ValueError(
                f"mixed source lacks assignment: {source_key}:{name}"
            ) from exc
        raise ValueError(f"source lacks assignment: {source_key}:{name}") from exc


def current_source_path(domain: str, source_key: str) -> Path:
    origin, _, relative = source_key.partition("/src/")
    return (
        LIBRARIES
        / f"lib_{domain}"
        / "src"
        / origin.removeprefix("lib_")
        / relative
    )


def write_header(domain: str, entries: list[dict[str, object]], destination: Path) -> None:
    includes = set()
    needs_xlocale = False
    declarations = []
    for entry in sorted(entries, key=lambda item: str(item["name"])):
        header = Path(str(entry["header"]))
        header_text = header.read_text()
        includes.update(
            item
            for item in INCLUDE.findall(header_text)
            if not item.startswith("p101_")
        )
        declarations.append(prototype(header_text, str(entry["name"])))

    if "xlocale.h" in includes or "bits/types/locale_t.h" in includes:
        needs_xlocale = True
        includes.discard("xlocale.h")
        includes.discard("bits/types/locale_t.h")
        includes.add("locale.h")

    type_declarations = []
    if domain == "filesystem":
        type_declarations.append(
            "typedef int (*p101_ftw_fn)(const char *fpath, const struct stat *sb, int typeflag);"
        )
    if domain == "ipc":
        type_declarations.append(
            """union p101_semun
{
    int              val;
    struct semid_ds *buf;
    unsigned short  *array;
};"""
        )

    guard = f"LIBP101_{domain.upper()}_{domain.upper()}_H"
    native_includes = "\n".join(f"#include <{item}>" for item in sorted(includes))
    if needs_xlocale:
        native_includes += """
#if defined(__APPLE__) || defined(__FreeBSD__)
    #include <xlocale.h>
#endif"""
    types = "\n\n".join(type_declarations)
    funcs = "\n".join(f"    {item}" for item in declarations)
    text = f"""#ifndef {guard}
#define {guard}

/*
 * Copyright 2026 D'Arcy Smith.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 */

#include <p101_env/env.h>
#include <p101_error/attributes.h>
{native_includes}

{types}

#ifdef __cplusplus
extern "C"
{{
#endif

{funcs}

#ifdef __cplusplus
}}
#endif

#endif    // {guard}
"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)


def config_text(domain: str, source_paths: list[str], has_placeholders: bool) -> str:
    target = f"p101_{domain}"
    sources = "\n".join(f"        {path}" for path in source_paths)
    links = ["p101_error", "p101_env", "p101_tool_event", "p101_c"]
    if domain == "sync":
        links.append("p101_thread")
    if domain == "locale":
        links.append("iconv")
    if domain == "math":
        links.append("m")
    linked = "\n".join(f"        {item}" for item in links)
    platform_links = ""
    if domain == "identity":
        platform_links = """
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" OR CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    list(APPEND p101_identity_LINK_LIBRARIES crypt)
endif()
"""
    placeholder_note = (
        "\n# design/unsupported contains documented interfaces that are deliberately\n"
        "# neither compiled nor installed because the three-platform contract fails.\n"
        if has_placeholders
        else ""
    )
    return f"""# Project metadata
set(PROJECT_NAME "{target}")
set(PROJECT_VERSION "0.0.1")
set(PROJECT_DESCRIPTION "{DOMAINS[domain]}")
set(PROJECT_LANGUAGE "C")

set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

set(STANDARD_FLAGS
        -D_POSIX_C_SOURCE=200809L
        -D_XOPEN_SOURCE=700
        -Werror
)
set(DARWIN_STANDARD_FLAGS -D_DARWIN_C_SOURCE)
set(LINUX_STANDARD_FLAGS -D_GNU_SOURCE)
set(BSD_STANDARD_FLAGS -D_BSD_SOURCE -D__BSD_VISIBLE)

set(LIBRARY_TARGETS {target})
set({target}_SOURCES
{sources}
)
set({target}_HEADERS
        include/{target}/{domain}.h
)
set({target}_LINK_LIBRARIES
{linked}
)
{platform_links}
{placeholder_note}"""


def test_cmake(domain: str) -> str:
    upper = domain.upper()
    return f"""cmake_minimum_required(VERSION 3.14)
project(p101_{domain}_tests C CXX)
enable_testing()

set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(P101_PUBLIC_INCLUDE_DIRS "" CACHE STRING "Extra p101 include dirs")
set(P101_PUBLIC_LINK_DIRS "" CACHE STRING "Extra p101 link dirs")
separate_arguments(P101_PUBLIC_INCLUDE_DIRS_LIST NATIVE_COMMAND "${{P101_PUBLIC_INCLUDE_DIRS}}")
separate_arguments(P101_PUBLIC_LINK_DIRS_LIST NATIVE_COMMAND "${{P101_PUBLIC_LINK_DIRS}}")

find_library(P101_{upper}_LIBRARY NAMES p101_{domain} PATHS ${{P101_PUBLIC_LINK_DIRS_LIST}} NO_DEFAULT_PATH)
find_library(P101_ENV_LIBRARY NAMES p101_env PATHS ${{P101_PUBLIC_LINK_DIRS_LIST}} NO_DEFAULT_PATH)
find_library(P101_ERROR_LIBRARY NAMES p101_error PATHS ${{P101_PUBLIC_LINK_DIRS_LIST}} NO_DEFAULT_PATH)
if(NOT P101_{upper}_LIBRARY)
    find_library(P101_{upper}_LIBRARY NAMES p101_{domain})
endif()
if(NOT P101_ENV_LIBRARY)
    find_library(P101_ENV_LIBRARY NAMES p101_env)
endif()
if(NOT P101_ERROR_LIBRARY)
    find_library(P101_ERROR_LIBRARY NAMES p101_error)
endif()
if(NOT P101_{upper}_LIBRARY OR NOT P101_ENV_LIBRARY OR NOT P101_ERROR_LIBRARY)
    message(FATAL_ERROR "Could not find p101_{domain}, p101_env, and p101_error")
endif()

set(P101_TEST_INCLUDE_DIRS "${{CMAKE_CURRENT_SOURCE_DIR}}/../include" ${{P101_PUBLIC_INCLUDE_DIRS_LIST}})
set(P101_TEST_LIBRARIES "${{P101_{upper}_LIBRARY}}" "${{P101_ENV_LIBRARY}}" "${{P101_ERROR_LIBRARY}}")

foreach(test_name IN ITEMS test_library test_headers_c)
    add_executable(${{test_name}} ${{test_name}}.c)
    target_include_directories(${{test_name}} PRIVATE ${{P101_TEST_INCLUDE_DIRS}})
    target_compile_definitions(${{test_name}} PRIVATE _POSIX_C_SOURCE=200809L _XOPEN_SOURCE=700)
    target_link_libraries(${{test_name}} PRIVATE ${{P101_TEST_LIBRARIES}})
    add_test(NAME ${{test_name}} COMMAND ${{test_name}})
endforeach()

add_executable(test_headers_cxx test_headers.cpp)
target_include_directories(test_headers_cxx PRIVATE ${{P101_TEST_INCLUDE_DIRS}})
target_compile_definitions(test_headers_cxx PRIVATE _POSIX_C_SOURCE=200809L _XOPEN_SOURCE=700)
target_link_libraries(test_headers_cxx PRIVATE ${{P101_TEST_LIBRARIES}})
add_test(NAME test_headers_cxx COMMAND test_headers_cxx)

if(APPLE)
    foreach(test_name IN ITEMS test_library test_headers_c test_headers_cxx)
        target_compile_definitions(${{test_name}} PRIVATE _DARWIN_C_SOURCE)
    endforeach()
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    foreach(test_name IN ITEMS test_library test_headers_c test_headers_cxx)
        target_compile_definitions(${{test_name}} PRIVATE _GNU_SOURCE)
    endforeach()
elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    foreach(test_name IN ITEMS test_library test_headers_c test_headers_cxx)
        target_compile_definitions(${{test_name}} PRIVATE _BSD_SOURCE __BSD_VISIBLE)
    endforeach()
endif()

if(CMAKE_C_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(test_library PRIVATE -Wall -Wextra -Werror -pedantic-errors)
    target_compile_options(test_headers_c PRIVATE -Wall -Wextra -Werror -pedantic-errors)
endif()
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(test_headers_cxx PRIVATE -Wall -Wextra -Werror -pedantic-errors)
endif()
"""


def populate() -> None:
    entries_by_domain: dict[str, list[dict[str, object]]] = defaultdict(list)
    source_chunks: dict[tuple[str, str], list[str]] = defaultdict(list)

    for repo in PROVENANCE:
        source_root = LIBRARIES / repo / "src"
        for source in sorted(source_root.rglob("*.c")):
            source_key = f"{repo}/{source.relative_to(LIBRARIES / repo)}"
            if source_key in SKIPPED_SOURCE_KEYS:
                continue
            text = source.read_text()
            functions = source_functions(text)
            if not functions:
                continue
            preamble = text[: functions[0][1]]
            by_domain: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for name, start, end, leading_start in functions:
                domain = source_owner(source_key, name)
                gap = text[leading_start:start] if leading_start != 0 else ""
                by_domain[domain].append((name, gap + text[start:end]))
                header = header_for_function(repo, name)
                entries_by_domain[domain].append(
                    {
                        "name": name,
                        "repo": repo,
                        "source": source,
                        "source_key": source_key,
                        "header": header,
                        "provenance": PROVENANCE[repo],
                    }
                )
            for domain, chunks in by_domain.items():
                if source_key in MIXED_SOURCES:
                    generated = preamble + "".join(chunk for _, chunk in chunks)
                else:
                    generated = text
                source_chunks[(domain, source_key)].append(transform_source(generated, domain))

    all_names = [str(entry["name"]) for entries in entries_by_domain.values() for entry in entries]
    duplicates = sorted(name for name in set(all_names) if all_names.count(name) > 1)
    if duplicates:
        raise ValueError(f"duplicate wrapper ownership: {', '.join(duplicates)}")

    for domain in DOMAINS:
        repo = LIBRARIES / f"lib_{domain}"
        if not repo.exists():
            raise FileNotFoundError(repo)
        for generated in ("include", "src", "test", "design"):
            path = repo / generated
            if path.exists():
                shutil.rmtree(path)

        entries = entries_by_domain[domain]
        write_header(domain, entries, repo / "include" / f"p101_{domain}" / f"{domain}.h")

        generated_sources = []
        for (owner, source_key), chunks in sorted(source_chunks.items()):
            if owner != domain:
                continue
            output = current_source_path(domain, source_key)
            output.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(chunks)
            if source_key == "lib_posix/src/pthread.c":
                content = prune_pthread_preamble(content, domain)
            if source_key in MIXED_SOURCES:
                content = prune_unused_static_functions(content)
            if domain == "sync" and source_key == "lib_posix/src/pthread.c":
                content = content.replace(
                    f'#include "p101_{domain}/{domain}.h"',
                    f'#include "p101_{domain}/{domain}.h"\n#include <p101_thread/thread.h>',
                    1,
                )
            output.write_text(content)
            generated_sources.append(str(output.relative_to(repo)))

        placeholder_rows = []
        for old_path, kind in PLACEHOLDERS.get(domain, []):
            source = LIBRARIES / old_path
            output = repo / "design" / "unsupported" / old_path
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
            placeholder_rows.append(f"{kind}\t{old_path}\tnot-built\tnot-installed\n")
        if placeholder_rows:
            (repo / "design" / "unsupported" / "README.md").write_text(
                "# Unsupported design placeholders\n\n"
                "These files document interfaces considered during the portability audit.\n"
                "They are not compiled or installed because the same public contract is not\n"
                "available on Linux, macOS, and FreeBSD.\n"
            )

        (repo / "config.cmake").write_text(
            config_text(domain, generated_sources, bool(placeholder_rows))
        )
        (repo / "README.md").write_text(
            f"# lib_{domain}\n\n"
            f"{DOMAINS[domain]} for the p101 portable systems subset.\n\n"
            "The public API is the intersection implemented on Linux, macOS, and FreeBSD.\n"
            "POSIX, XSI, optional-POSIX, and common-Unix origins are recorded in\n"
            "`api-manifest.tsv`; provenance does not determine ownership.\n\n"
            "## Build and verification\n\n"
            "```sh\n./change-compiler.sh -c clang\n./check.sh\n```\n\n"
            "Instrumentation sees only calls routed through `p101_*` wrappers. It does\n"
            "not observe direct libc calls or work performed inside third-party code.\n"
        )
        shutil.copy2(WORKSPACE / "AGENTS.md", repo / "AGENTS.md")

        manifest = [
            "function\tprovenance\tcurrent_source\tcurrent_header\t"
            "original_source\toriginal_header\tlinux\tmacos\tfreebsd\n"
        ]
        for entry in sorted(entries, key=lambda item: str(item["name"])):
            manifest.append(
                f"{entry['name']}\t{entry['provenance']}\t"
                f"{current_source_path(domain, str(entry['source_key'])).relative_to(WORKSPACE)}\t"
                f"libraries/lib_{domain}/include/p101_{domain}/{domain}.h\t"
                f"{Path(str(entry['source'])).relative_to(WORKSPACE)}\t"
                f"{Path(str(entry['header'])).relative_to(WORKSPACE)}\t"
                "yes\tyes\tyes\n"
            )
        (repo / "api-manifest.tsv").write_text("".join(manifest))
        if placeholder_rows:
            (repo / "unsupported-manifest.tsv").write_text(
                "kind\toriginal_path\tbuild\tinstall\n" + "".join(placeholder_rows)
            )

        test_dir = repo / "test"
        test_dir.mkdir(parents=True)
        (test_dir / "CMakeLists.txt").write_text(test_cmake(domain))
        (test_dir / "test_headers_c.c").write_text(
            f"#include <p101_{domain}/{domain}.h>\n\nint main(void)\n{{\n    return 0;\n}}\n"
        )
        (test_dir / "test_headers.cpp").write_text(
            f"#include <p101_{domain}/{domain}.h>\n\nint main()\n{{\n    return 0;\n}}\n"
        )
        (test_dir / "test_library.c").write_text(
            f"""#include <p101_{domain}/{domain}.h>
#include <p101_env/env.h>
#include <p101_error/error.h>
#include <stdlib.h>

int main(void)
{{
    struct p101_error *err;
    struct p101_env   *env;

    err = p101_error_create(false);
    if(err == NULL)
    {{
        return EXIT_FAILURE;
    }}
    env = p101_env_create(err, NULL);
    if(env == NULL)
    {{
        p101_error_destroy(err);
        return EXIT_FAILURE;
    }}
    p101_env_destroy(env);
    p101_error_destroy(err);
    return EXIT_SUCCESS;
}}
"""
        )

    manifest = [
        "function\tdomain\tprovenance\tcurrent_source\tcurrent_header\t"
        "original_source\toriginal_header\n"
    ]
    for domain, entries in sorted(entries_by_domain.items()):
        for entry in sorted(entries, key=lambda item: str(item["name"])):
            manifest.append(
                f"{entry['name']}\t{domain}\t{entry['provenance']}\t"
                f"{current_source_path(domain, str(entry['source_key'])).relative_to(WORKSPACE)}\t"
                f"libraries/lib_{domain}/include/p101_{domain}/{domain}.h\t"
                f"{Path(str(entry['source'])).relative_to(WORKSPACE)}\t"
                f"{Path(str(entry['header'])).relative_to(WORKSPACE)}\n"
            )
    (SCRIPT_DIR / "wrapper-library-map.tsv").write_text("".join(manifest))

    print(
        f"populated {len(DOMAINS)} libraries with "
        f"{sum(len(value) for value in entries_by_domain.values())} wrappers"
    )


if __name__ == "__main__":
    populate()
