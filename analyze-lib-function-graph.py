#!/usr/bin/env python3
"""Inventory p101 wrapper functions and emit a coarse function graph.

The goal is not to replace clang. It is to produce a curriculum-planning map:
which wrappers exist, which wrappers call other wrappers, which native APIs they
wrap, and which functional clusters are large enough to deserve playgrounds.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


WRAPPER_RE = re.compile(r"\bp101_[A-Za-z0-9_]+\b")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "alignof",
    "_Alignof",
    "_Generic",
    "defined",
}
IGNORED_CALLS = {
    "P101_TRACE",
    "P101_ERROR_RAISE_ERRNO",
    "P101_ERROR_RAISE_SYSTEM",
    "P101_ERROR_RAISE_USER",
    "P101_ERROR_RAISE_MESSAGE",
    "P101_ATTRIBUTE_NEVER_NULL",
    "va_start",
    "va_arg",
    "va_end",
    "va_copy",
}


@dataclass(frozen=True)
class FunctionNode:
    name: str
    library: str
    domain: str
    header: str | None
    source: str | None
    native_guess: str


@dataclass(frozen=True)
class FunctionEdge:
    source: str
    target: str
    kind: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a p101 library wrapper function graph.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Workspace root.")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "docs", help="Output directory.")
    return parser.parse_args()


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def rel(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def active_libraries(root: Path) -> list[Path]:
    repos_file = root / "scripts" / "repos.txt"
    admitted: list[Path] = []
    for raw_line in repos_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 2:
            continue
        path = (repos_file.parent / fields[1]).resolve()
        if path.parent == (root / "libraries").resolve() and path.name.startswith("lib_"):
            admitted.append(path)
    return sorted(path for path in admitted if path.is_dir())


def configured_files(library_dir: Path, suffix: str) -> list[Path]:
    """Return files admitted by the library's install/build contract."""
    config = library_dir / "config.cmake"
    if not config.is_file():
        return []

    text = config.read_text(encoding="utf-8", errors="replace")
    targets_match = re.search(r"set\(\s*LIBRARY_TARGETS\b(.*?)\)", text, re.DOTALL)
    if targets_match is None:
        return []
    targets = shlex.split(re.sub(r"#[^\n]*", "", targets_match.group(1)))
    paths: list[Path] = []
    for target in targets:
        pattern = re.compile(rf"set\(\s*{re.escape(target)}_{re.escape(suffix)}\b(.*?)\)", re.DOTALL)
        match = pattern.search(text)
        if match is None:
            continue
        body = re.sub(r"#[^\n]*", "", match.group(1))
        for token in shlex.split(body):
            path = library_dir / token
            if path.is_file():
                paths.append(path)
    return sorted(set(paths))


def find_matching(text: str, start: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def extract_definitions(path: Path) -> dict[str, str]:
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    definitions: dict[str, str] = {}
    for match in WRAPPER_RE.finditer(text):
        name = match.group(0)
        after = match.end()
        while after < len(text) and text[after].isspace():
            after += 1
        if after >= len(text) or text[after] != "(":
            continue
        close_paren = find_matching(text, after, "(", ")")
        if close_paren is None:
            continue
        after_sig = close_paren + 1
        while after_sig < len(text) and text[after_sig].isspace():
            after_sig += 1
        if after_sig >= len(text) or text[after_sig] != "{":
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        prefix = text[line_start : match.start()]
        if ";" in prefix or "typedef" in prefix or "#define" in prefix:
            continue
        close_brace = find_matching(text, after_sig, "{", "}")
        if close_brace is None:
            continue
        definitions[name] = text[after_sig + 1 : close_brace]
    return definitions


def extract_header_prototypes(path: Path) -> set[str]:
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    names: set[str] = set()
    for match in WRAPPER_RE.finditer(text):
        name = match.group(0)
        statement_start = max(text.rfind(";", 0, match.start()), text.rfind("{", 0, match.start()), text.rfind("}", 0, match.start()))
        if re.search(r"\btypedef\b", text[statement_start + 1 : match.start()]):
            continue
        after = match.end()
        while after < len(text) and text[after].isspace():
            after += 1
        if after >= len(text) or text[after] != "(":
            continue
        close_paren = find_matching(text, after, "(", ")")
        if close_paren is None:
            continue
        after_sig = close_paren + 1
        semicolon = text.find(";", after_sig)
        body = text.find("{", after_sig)
        if semicolon >= 0 and (body < 0 or semicolon < body):
            names.add(name)
    return names


def remove_prefix(text: str, prefix: str) -> str:
    if text.startswith(prefix):
        return text[len(prefix) :]
    return text


def remove_suffix(text: str, suffix: str) -> str:
    if text.endswith(suffix):
        return text[: -len(suffix)]
    return text


def native_guess(wrapper: str) -> str:
    rest = remove_prefix(wrapper, "p101_")
    if rest.startswith("_"):
        return rest
    return rest


def header_topic(path: str | None, source: str | None) -> str:
    candidate = path or source or ""
    candidate = candidate.replace("\\", "/")
    parts = [part for part in candidate.split("/") if part]
    for part in reversed(parts):
        if part.startswith("p101_"):
            return remove_suffix(remove_prefix(part, "p101_"), ".h")
    if parts:
        return Path(parts[-1]).stem
    return "unknown"


def classify_c_math(name: str) -> str:
    trig_prefixes = (
        "p101_acos",
        "p101_asin",
        "p101_atan",
        "p101_cos",
        "p101_sin",
        "p101_tan",
    )
    exp_log_prefixes = (
        "p101_cbrt",
        "p101_erf",
        "p101_exp",
        "p101_hypot",
        "p101_lgamma",
        "p101_log",
        "p101_pow",
        "p101_sqrt",
        "p101_tgamma",
    )
    rounding_prefixes = (
        "p101_ceil",
        "p101_floor",
        "p101_fmod",
        "p101_frexp",
        "p101_ldexp",
        "p101_llrint",
        "p101_llround",
        "p101_lrint",
        "p101_lround",
        "p101_modf",
        "p101_nearbyint",
        "p101_remainder",
        "p101_remquo",
        "p101_rint",
        "p101_round",
        "p101_scalbln",
        "p101_scalbn",
        "p101_trunc",
    )
    floating_prefixes = (
        "p101_copysign",
        "p101_fabs",
        "p101_fdim",
        "p101_fma",
        "p101_fmax",
        "p101_fmin",
        "p101_ilogb",
        "p101_nan",
        "p101_nextafter",
        "p101_nexttoward",
    )
    if name.startswith(trig_prefixes):
        return "c/math-trig"
    if name.startswith(exp_log_prefixes):
        return "c/math-exp-log-power"
    if name.startswith(rounding_prefixes):
        return "c/math-rounding-remainder"
    if name.startswith(floating_prefixes):
        return "c/math-floating"
    return "c/math-other"


def classify_complex_math(name: str) -> str:
    if any(name.startswith(prefix) for prefix in ("p101_cacos", "p101_casin", "p101_catan", "p101_ccos", "p101_csin", "p101_ctan")):
        return "c/complex-trig"
    if any(name.startswith(prefix) for prefix in ("p101_cexp", "p101_clog", "p101_cpow", "p101_csqrt")):
        return "c/complex-exp-log-power"
    return "c/complex-components"


def classify_byte_text(name: str, extension: bool = False) -> str:
    suffix = "-extensions" if extension else ""
    if name.startswith("p101_mem") or name in {"p101_bzero", "p101_bcopy", "p101_bcmp"}:
        return f"c/memory-bytes{suffix}"
    return f"c/byte-strings{suffix}"


def classify_stdio(name: str, extension: bool = False) -> str:
    suffix = "-extensions" if extension else ""
    if "printf" in name or "scanf" in name:
        return f"c/stdio-formatted{suffix}"
    if any(token in name for token in ("getc", "gets", "putc", "puts", "ungetc")):
        return f"c/stdio-character-io{suffix}"
    if any(token in name for token in ("clearerr", "feof", "ferror", "fflush", "setvbuf")):
        return f"c/stdio-state-buffering{suffix}"
    return f"c/stdio-streams-files{suffix}"


def classify_wide_text(name: str, extension: bool = False) -> str:
    suffix = "-extensions" if extension else ""
    if "printf" in name or "scanf" in name or any(token in name for token in ("getwc", "getws", "putwc", "putws", "ungetwc")):
        return f"c/wide-stdio{suffix}"
    if any(token in name for token in ("btowc", "mbr", "mbs", "wcrtomb", "wctob", "wcsto")):
        return f"c/wide-conversion{suffix}"
    if name.startswith("p101_wmem"):
        return f"c/wide-memory{suffix}"
    return f"c/wide-strings{suffix}"


def classify_env(name: str) -> str:
    if "fault" in name:
        return "support/fault-injection"
    if any(token in name for token in ("fd", "alloc", "track", "exec", "fork", "report_leaks")):
        return "support/resource-events"
    if any(token in name for token in ("trace", "tracer", "call")):
        return "support/call-tracing"
    if any(token in name for token in ("log_append", "open_log", "close_owned_resource_log")):
        return "support/event-log-format"
    return "support/environment-lifecycle"


def classify_error(name: str) -> str:
    if name.startswith("p101_check_"):
        return "support/assertions"
    if name.startswith("p101_errno_"):
        return "support/error-codes"
    return "support/error-core"


def classify_threading(name: str) -> str:
    if name.startswith("p101_sched_"):
        return "systems/scheduler"
    if name.startswith("p101_pthread_attr_"):
        return "systems/thread-attributes"
    if name.startswith("p101_pthread_mutex"):
        return "systems/thread-mutexes"
    if name.startswith("p101_pthread_cond"):
        return "systems/thread-conditions"
    if name.startswith("p101_pthread_rwlock"):
        return "systems/thread-rwlocks"
    if any(token in name for token in ("cancel", "testcancel")):
        return "systems/thread-cancellation"
    if any(token in name for token in ("specific", "key_", "once")):
        return "systems/thread-local-once"
    return "systems/thread-lifecycle"


def classify_network(name: str) -> str:
    if any(name.startswith(prefix) for prefix in ("p101_send", "p101_recv")):
        return "network/io"
    if name.startswith("p101_inet_") or name in {"p101_htonl", "p101_htons", "p101_ntohl", "p101_ntohs"}:
        return "network/address-conversion"
    if name.startswith("p101_if_") or name in {"p101_getifaddrs", "p101_freeifaddrs"}:
        return "network/interfaces"
    if name.startswith("p101_ether_"):
        return "network/ethernet"
    if any(name.startswith(prefix) for prefix in ("p101_ns_", "p101_res_", "p101_dn_", "p101_b64_")):
        return "network/dns-resolver"
    if any(token in name for token in ("addrinfo", "nameinfo", "hostent", "netent", "protoent", "servent", "gai_strerror")):
        return "network/name-resolution"
    return "network/sockets"


def classify_unistd_like(name: str) -> str | None:
    fd_names = {
        "p101_close",
        "p101_creat",
        "p101_dup",
        "p101_dup2",
        "p101_fcntl",
        "p101_ftruncate",
        "p101_lockf",
        "p101_lseek",
        "p101_open",
        "p101_openat",
        "p101_pipe",
        "p101_pread",
        "p101_pwrite",
        "p101_read",
        "p101_readv",
        "p101_sync",
        "p101_write",
        "p101_writev",
    }
    filesystem_names = {
        "p101_access",
        "p101_chdir",
        "p101_chmod",
        "p101_chown",
        "p101_faccessat",
        "p101_fchdir",
        "p101_fchmod",
        "p101_fchmodat",
        "p101_fchown",
        "p101_fchownat",
        "p101_fstat",
        "p101_fstatat",
        "p101_fstatvfs",
        "p101_futimens",
        "p101_lchown",
        "p101_link",
        "p101_linkat",
        "p101_lstat",
        "p101_mkdir",
        "p101_mkdirat",
        "p101_mkfifo",
        "p101_mknod",
        "p101_readlink",
        "p101_readlinkat",
        "p101_rmdir",
        "p101_stat",
        "p101_statvfs",
        "p101_symlink",
        "p101_symlinkat",
        "p101_truncate",
        "p101_umask",
        "p101_unlink",
        "p101_unlinkat",
        "p101_utimensat",
    }
    directory_names = {
        "p101_alphasort",
        "p101_basename",
        "p101_closedir",
        "p101_dirfd",
        "p101_dirname",
        "p101_fdopendir",
        "p101_fnmatch",
        "p101_ftw",
        "p101_glob",
        "p101_globfree",
        "p101_nftw",
        "p101_opendir",
        "p101_readdir",
        "p101_rewinddir",
        "p101_scandir",
        "p101_seekdir",
        "p101_telldir",
        "p101_wordexp",
        "p101_wordfree",
    }
    identity_names = {
        "p101_endusershell",
        "p101_getegid",
        "p101_geteuid",
        "p101_getgid",
        "p101_getgroups",
        "p101_getlogin_r",
        "p101_getuid",
        "p101_getusershell",
        "p101_setegid",
        "p101_seteuid",
        "p101_setgid",
        "p101_setregid",
        "p101_setreuid",
        "p101_setuid",
        "p101_setusershell",
    }
    config_names = {
        "p101_confstr",
        "p101_fpathconf",
        "p101_getcwd",
        "p101_getdomainname",
        "p101_gethostid",
        "p101_gethostname",
        "p101_pathconf",
        "p101_setdomainname",
        "p101_sysconf",
    }
    process_names = {
        "p101_exit_immediately",
        "p101_posix_exit_immediately",
        "p101_execv",
        "p101_execve",
        "p101_execvp",
        "p101_fork",
        "p101_getpgid",
        "p101_getpgrp",
        "p101_getpid",
        "p101_getppid",
        "p101_getsid",
        "p101_setpgid",
        "p101_setsid",
    }
    terminal_names = {"p101_isatty", "p101_tcgetpgrp", "p101_tcsetpgrp", "p101_ttyname_r"}
    scheduling_names = {"p101_alarm", "p101_nice", "p101_pause", "p101_sleep"}
    if name in fd_names:
        return "systems/fd-io"
    if name in filesystem_names:
        return "systems/filesystem-paths"
    if name in directory_names:
        return "systems/directories-patterns"
    if name in identity_names:
        return "systems/users-identity"
    if name in config_names:
        return "systems/system-configuration"
    if name in process_names:
        return "systems/process-control"
    if name in terminal_names:
        return "systems/users-terminals"
    if name in scheduling_names:
        return "systems/scheduling-basics"
    if name == "p101_getopt":
        return "c/cli-parsing"
    if name == "p101_crypt":
        return "systems/security-legacy"
    if name == "p101_swab":
        return "c/byte-utility"
    return None


def classify(library: str, header: str | None, source: str | None, name: str) -> str:
    topic = header_topic(header, source)
    topic_lower = topic.lower()
    path = f"{header or ''}/{source or ''}".lower()

    if library == "lib_c":
        if topic == "math":
            return classify_c_math(name)
        if topic == "complex":
            return classify_complex_math(name)
        if topic == "fenv":
            return "c/floating-env"
        if topic == "ctype":
            return "c/char-classification"
        if topic == "string":
            return classify_byte_text(name)
        if topic == "wchar":
            return classify_wide_text(name)
        if topic == "wctype":
            return "c/wide-char-classification"
        if topic == "stdio":
            return classify_stdio(name)
        if topic in {"stdlib", "inttypes"}:
            return f"c/{topic}"
        if topic in {"stdatomic"}:
            return "c/atomics"
        if topic in {"setjmp", "signal"}:
            return "c/control-flow"
        return f"c/{topic}"
    if library == "lib_error":
        return classify_error(name)
    if library == "lib_env":
        return classify_env(name)
    if library == "lib_fsm":
        return "support/fsm"
    if library == "lib_convert":
        if "network" in path:
            return "network/conversion"
        return "c/conversion"
    if library == "lib_c_facts":
        return "tooling/c-facts"
    if library == "lib_tool_event":
        return "tooling/event-protocol"
    if library == "lib_util":
        return "c/byte-utility"
    if library == "lib_network":
        return classify_network(name)
    if library in {"lib_thread", "lib_sync"}:
        return classify_threading(name)
    if library == "lib_ipc":
        return "systems/ipc"
    if library == "lib_process":
        return "systems/process-signal"
    if library == "lib_filesystem":
        return classify_unistd_like(name) or "systems/directories-patterns"
    if library == "lib_io":
        if name in {"p101_poll", "p101_select", "p101_pselect"}:
            return "systems/io-multiplexing"
        if name.startswith("p101_aio_") or name == "p101_lio_listio":
            return "systems/async-io"
        return classify_unistd_like(name) or classify_stdio(name, extension=True)
    if library in {"lib_terminal", "lib_identity"}:
        return "systems/users-terminals"
    if library in {"lib_time", "lib_memory"}:
        return "systems/resource-time-memory"
    if library == "lib_diagnostics":
        return "systems/logging-diagnostics"
    if library == "lib_dynamic_linking":
        return "systems/dynamic-loading"
    if library == "lib_locale":
        return "systems/localization-conversion"
    if library == "lib_database":
        return "systems/legacy-database"
    if library == "lib_search":
        return "systems/search-structures"
    if library == "lib_cli":
        return "c/cli-parsing"
    if library == "lib_host":
        return "systems/platform-admin"
    if library == "lib_random":
        return "c/random"
    if library == "lib_math":
        return "c/math-other"
    if library == "lib_text":
        if name.startswith("p101_reg"):
            return "systems/text-patterns"
        return classify_byte_text(name, extension=True)

    if library in {"lib_posix", "lib_posix_optional", "lib_posix_xsi", "lib_unix"}:
        c_extension_topics = {
            "ctype": "c/char-classification-extensions",
            "inttypes": "c/inttypes-extensions",
            "locale": "c/locale-extensions",
            "math": "c/math-other",
            "setjmp": "c/control-flow-extensions",
            "stdio": classify_stdio(name, extension=True),
            "stdlib": "c/stdlib-extensions",
            "string": classify_byte_text(name, extension=True),
            "strings": classify_byte_text(name, extension=True),
            "time": "c/time-extensions",
            "wchar": classify_wide_text(name, extension=True),
            "wctype": "c/wide-char-classification-extensions",
        }
        if topic_lower in c_extension_topics:
            return c_extension_topics[topic_lower]
        unistd_domain = classify_unistd_like(name)
        if unistd_domain is not None:
            return unistd_domain
        if topic_lower == "aio":
            return "systems/async-io"
        if topic_lower in {"dirent", "fcntl", "fnmatch", "ftw", "glob", "libgen", "statvfs", "unistd", "wordexp"}:
            return "systems/file-io"
        if topic_lower in {"poll", "select"}:
            return "systems/io-multiplexing"
        if topic_lower in {"ipc", "mqueue", "msg", "sem", "semaphore", "shm"}:
            return "systems/ipc"
        if topic_lower in {"pthread", "sched"}:
            return classify_threading(name)
        if topic_lower in {"signal", "spawn", "wait"}:
            return "systems/process-signal"
        if topic_lower in {"mman", "resource", "times", "timex"}:
            return "systems/resource-time-memory"
        if topic_lower in {"grp", "pwd", "termios", "ttyent", "utmpx"}:
            return "systems/users-terminals"
        if topic_lower in {"err", "fmtmsg", "syslog"}:
            return "systems/logging-diagnostics"
        if topic_lower == "dlfcn":
            return "systems/dynamic-loading"
        if topic_lower == "regex":
            return "systems/text-patterns"
        if topic_lower in {"iconv", "langinfo", "nl_types"}:
            return "systems/localization-conversion"
        if topic_lower == "ndbm":
            return "systems/legacy-database"
        if topic_lower == "search":
            return "systems/search-structures"
        if topic_lower in {"fstab", "mount", "sysctl", "utsname"}:
            return "systems/platform-admin"
        if topic_lower == "getopt":
            return "c/cli-parsing"

    if any(token in path for token in ("/sys/p101_msg", "/sys/msg", "/sys/p101_sem", "/sys/sem", "/sys/p101_shm", "/sys/shm", "/sys/p101_ipc", "/sys/ipc", "p101_mqueue", "p101_semaphore")):
        return "systems/ipc"
    if any(token in path for token in ("p101_poll", "p101_select")):
        return "systems/io-multiplexing"
    if any(token in path for token in ("p101_pthread", "p101_sched")):
        return classify_threading(name)
    if any(token in path for token in ("p101_socket", "p101_netdb", "arpa/", "/net/", "p101_ifaddrs", "p101_resolv", "p101_nameser", "p101_ethernet")):
        return classify_network(name)
    if any(token in path for token in ("p101_fcntl", "p101_unistd", "p101_stdio", "/sys/p101_stat", "/sys/stat", "p101_dirent", "p101_ftw", "p101_glob", "p101_fnmatch", "p101_wordexp", "p101_libgen", "p101_statvfs", "/sys/p101_uio", "/sys/uio")):
        return classify_unistd_like(name) or "systems/file-io"
    if any(token in path for token in ("p101_wait", "p101_spawn", "p101_signal")):
        return "systems/process-signal"
    if any(token in path for token in ("p101_mman", "p101_resource", "p101_time", "p101_times", "p101_timex")):
        return "systems/resource-time-memory"
    if any(token in path for token in ("p101_pwd", "p101_grp", "p101_utmpx", "p101_ttyent", "p101_termios")):
        return "systems/users-terminals"
    if any(token in path for token in ("p101_syslog", "p101_err", "p101_fmtmsg")):
        return "systems/logging-diagnostics"
    if "p101_dlfcn" in path:
        return "systems/dynamic-loading"
    if "p101_regex" in path:
        return "systems/text-patterns"
    if any(token in path for token in ("p101_iconv", "p101_langinfo", "p101_nl_types")):
        return "systems/localization-conversion"
    if "p101_ndbm" in path:
        return "systems/legacy-database"
    if "p101_search" in path:
        return "systems/search-structures"
    return f"{library}/{topic}"


def collect(root: Path) -> tuple[dict[str, FunctionNode], list[FunctionEdge], dict[str, list[str]]]:
    headers_by_name: dict[str, Path] = {}
    sources_by_name: dict[str, Path] = {}
    definitions_by_name: dict[str, str] = {}
    declared_by_library: dict[str, list[str]] = defaultdict(list)

    for library_dir in active_libraries(root):
        library = library_dir.name
        for header in configured_files(library_dir, "HEADERS"):
            for name in extract_header_prototypes(header):
                headers_by_name.setdefault(name, header)
                declared_by_library[library].append(name)
        for source in configured_files(library_dir, "SOURCES"):
            definitions = extract_definitions(source)
            for name, body in definitions.items():
                sources_by_name.setdefault(name, source)
                definitions_by_name.setdefault(name, body)

    # The curriculum graph is a public wrapper-surface inventory. Source-only
    # p101_* helpers are implementation details, not wrappers students can call.
    # Keep their bodies available while discovering calls from public wrappers,
    # but do not turn them into manifest nodes.
    all_names = sorted(headers_by_name)
    nodes: dict[str, FunctionNode] = {}
    for name in all_names:
        header = headers_by_name.get(name)
        source = sources_by_name.get(name)
        owner_path = header or source
        library = "unknown"
        if owner_path is not None:
            try:
                library = owner_path.relative_to(root / "libraries").parts[0]
            except ValueError:
                library = "unknown"
        header_rel = rel(root, header)
        source_rel = rel(root, source)
        nodes[name] = FunctionNode(
            name=name,
            library=library,
            domain=classify(library, header_rel, source_rel, name),
            header=header_rel,
            source=source_rel,
            native_guess=native_guess(name),
        )

    edges_set: set[tuple[str, str, str]] = set()
    for name, body in definitions_by_name.items():
        node = nodes.get(name)
        if node is None:
            continue
        for call in CALL_RE.findall(body):
            if call in KEYWORDS or call in IGNORED_CALLS:
                continue
            if call.startswith("P101_"):
                continue
            if call.startswith("p101_") and call != name:
                edges_set.add((name, call, "wrapper-call"))
            elif not call.startswith("p101_"):
                guess = node.native_guess
                kind = "native-call"
                target = call
                if call == guess or call.strip("_") == guess.strip("_"):
                    kind = "wrapped-native"
                edges_set.add((name, target, kind))

    edges = [FunctionEdge(source, target, kind) for source, target, kind in sorted(edges_set)]
    return nodes, edges, declared_by_library


def domain_summaries(nodes: dict[str, FunctionNode]) -> dict[str, dict[str, Any]]:
    domains: dict[str, list[FunctionNode]] = defaultdict(list)
    for node in nodes.values():
        domains[node.domain].append(node)
    return {
        domain: {
            "count": len(items),
            "libraries": dict(sorted(Counter(item.library for item in items).items())),
            "functions": sorted(item.name for item in items),
        }
        for domain, items in sorted(domains.items())
    }


def track_recommendations(domain_counts: Counter[str]) -> list[dict[str, Any]]:
    """Return non-overlapping tracks for a single playground repository."""
    track_specs = [
        {
            "track": "c-memory-runtime",
            "purpose": "Allocation, process termination, environment variables, sorting/searching helpers, and common stdlib extensions.",
            "prefixes": (),
            "domains": ("c/stdlib", "c/stdlib-extensions", "c/random"),
        },
        {
            "track": "c-memory-bytes",
            "purpose": "Raw byte memory operations and the difference between object bytes and strings.",
            "prefixes": (),
            "domains": ("c/memory-bytes", "c/memory-bytes-extensions", "c/byte-utility"),
        },
        {
            "track": "c-byte-strings",
            "purpose": "NUL-terminated byte strings, comparisons, searching, collation, and common string extensions.",
            "prefixes": (),
            "domains": ("c/byte-strings", "c/byte-strings-extensions"),
        },
        {
            "track": "c-char-classification",
            "purpose": "Character classification, case mapping, locale-sensitive predicates, and signed-char pitfalls.",
            "prefixes": (),
            "domains": ("c/char-classification", "c/char-classification-extensions"),
        },
        {
            "track": "c-wide-strings",
            "purpose": "Wide string copying, comparison, searching, transformation, and wide-memory operations.",
            "prefixes": (),
            "domains": ("c/wide-strings", "c/wide-strings-extensions", "c/wide-memory", "c/wide-memory-extensions"),
        },
        {
            "track": "c-wide-io-conversion",
            "purpose": "Wide-character I/O, multibyte conversion state, numeric conversion, and locale-sensitive text boundaries.",
            "prefixes": (),
            "domains": ("c/wide-stdio", "c/wide-stdio-extensions", "c/wide-conversion", "c/wide-conversion-extensions"),
        },
        {
            "track": "c-wide-classification",
            "purpose": "Wide character classification, mapping, and locale-aware wide-character predicates.",
            "prefixes": (),
            "domains": ("c/wide-char-classification", "c/wide-char-classification-extensions"),
        },
        {
            "track": "c-stdio-streams",
            "purpose": "Opening, closing, positioning, reading, writing, renaming, temporary files, and stream ownership.",
            "prefixes": (),
            "domains": ("c/stdio-streams-files", "c/stdio-streams-files-extensions"),
        },
        {
            "track": "c-stdio-formatted",
            "purpose": "printf/scanf families, varargs wrappers, format checking, and formatted conversion hazards.",
            "prefixes": (),
            "domains": ("c/stdio-formatted", "c/stdio-formatted-extensions"),
        },
        {
            "track": "c-stdio-character-buffering",
            "purpose": "Character/line I/O, pushback, EOF/error state, flushing, and buffering mode.",
            "prefixes": (),
            "domains": ("c/stdio-character-io", "c/stdio-character-io-extensions", "c/stdio-state-buffering", "c/stdio-state-buffering-extensions"),
        },
        {
            "track": "c-conversion-parsing",
            "purpose": "Integer parsing, inttypes helpers, option parsing, and defensive conversion practice.",
            "prefixes": (),
            "domains": ("c/conversion", "c/inttypes", "c/inttypes-extensions", "c/cli-parsing"),
        },
        {
            "track": "c-math-trig",
            "purpose": "Trigonometric and hyperbolic math families, including float/double/long double variants.",
            "prefixes": (),
            "domains": ("c/math-trig",),
        },
        {
            "track": "c-math-exp-log-power",
            "purpose": "Exponentials, logarithms, roots, powers, and gamma/error-function families.",
            "prefixes": (),
            "domains": ("c/math-exp-log-power",),
        },
        {
            "track": "c-math-rounding",
            "purpose": "Rounding, remainder, scaling, decomposition, and integer-result math APIs.",
            "prefixes": (),
            "domains": ("c/math-rounding-remainder",),
        },
        {
            "track": "c-floating-point",
            "purpose": "Floating-point environment, NaN/nextafter/fma/min/max style helpers, and numerical edge cases.",
            "prefixes": (),
            "domains": ("c/math-floating", "c/floating-env", "c/math-other"),
        },
        {
            "track": "c-complex-components",
            "purpose": "Complex absolute value, phase, real/imaginary access, conjugation, and projection helpers.",
            "prefixes": (),
            "domains": ("c/complex-components",),
        },
        {
            "track": "c-complex-trig",
            "purpose": "Complex trigonometric and hyperbolic function families.",
            "prefixes": (),
            "domains": ("c/complex-trig",),
        },
        {
            "track": "c-complex-exp-log-power",
            "purpose": "Complex exponentials, logarithms, powers, and square roots.",
            "prefixes": (),
            "domains": ("c/complex-exp-log-power",),
        },
        {
            "track": "c-time-locale-control",
            "purpose": "Time, locale, atomics, setjmp/signal-style control flow, and their common extensions.",
            "prefixes": (),
            "domains": ("c/time", "c/time-extensions", "c/locale", "c/locale-extensions", "c/atomics", "c/control-flow", "c/control-flow-extensions"),
        },
        {
            "track": "file-io",
            "purpose": "File descriptors, open/read/write/close, vectored I/O, short reads/writes, async I/O, descriptor ownership, and cleanup.",
            "prefixes": (),
            "domains": ("systems/fd-io", "systems/async-io"),
        },
        {
            "track": "filesystem-paths",
            "purpose": "Paths, permissions, stat, links, directories-as-filesystem-objects, timestamps, and filesystem mutation.",
            "prefixes": (),
            "domains": ("systems/filesystem-paths",),
        },
        {
            "track": "directories-patterns",
            "purpose": "Directory traversal, glob/fnmatch/wordexp, path decomposition, and tree walking.",
            "prefixes": (),
            "domains": ("systems/directories-patterns",),
        },
        {
            "track": "processes-signals",
            "purpose": "fork, exec, wait, spawn, signals, inherited resources, CLOEXEC, and process failure paths.",
            "prefixes": (),
            "domains": ("systems/process-control", "systems/process-signal", "systems/scheduling-basics"),
        },
        {
            "track": "thread-lifecycle",
            "purpose": "Thread creation, joining, detaching, identity, scheduling hooks, and basic lifecycle ownership.",
            "prefixes": (),
            "domains": ("systems/thread-lifecycle", "systems/thread-attributes", "systems/scheduler"),
        },
        {
            "track": "thread-synchronization",
            "purpose": "Mutexes, condition variables, read/write locks, and synchronization cleanup rules.",
            "prefixes": (),
            "domains": ("systems/thread-mutexes", "systems/thread-conditions", "systems/thread-rwlocks"),
        },
        {
            "track": "thread-state-cancellation",
            "purpose": "Thread-local storage, once initialization, cancellation state, cancellation points, and cleanup hazards.",
            "prefixes": (),
            "domains": ("systems/thread-local-once", "systems/thread-cancellation"),
        },
        {
            "track": "ipc",
            "purpose": "POSIX and XSI IPC: message queues, semaphores, shared memory, keys, readiness, cleanup, and permissions.",
            "prefixes": (),
            "domains": ("systems/ipc", "systems/io-multiplexing"),
        },
        {
            "track": "network-sockets",
            "purpose": "socket, bind, listen, accept, connect, socketpair, shutdown, socket options, and socket metadata.",
            "prefixes": (),
            "domains": ("network/sockets",),
        },
        {
            "track": "network-io-addresses",
            "purpose": "send/recv families, byte order, inet conversion, and network address helper functions.",
            "prefixes": (),
            "domains": ("network/io", "network/address-conversion", "network/conversion"),
        },
        {
            "track": "network-names-interfaces",
            "purpose": "getaddrinfo/getnameinfo, protocol/service databases, interface enumeration, and Ethernet helpers.",
            "prefixes": (),
            "domains": ("network/name-resolution", "network/interfaces", "network/ethernet"),
        },
        {
            "track": "network-dns-resolver",
            "purpose": "Resolver state, DNS message parsing/packing, compressed names, and resolver validation helpers.",
            "prefixes": (),
            "domains": ("network/dns-resolver",),
        },
        {
            "track": "terminals-users",
            "purpose": "Terminal control, user/group lookup, identity APIs, tty databases, utmpx records, and interactive program boundaries.",
            "prefixes": (),
            "domains": ("systems/users-terminals", "systems/users-identity"),
        },
        {
            "track": "resources-platform",
            "purpose": "Resource limits, priorities, clocks/time, memory mapping/locking, host/system configuration, and portable platform administration APIs.",
            "prefixes": (),
            "domains": ("systems/resource-time-memory", "systems/platform-admin", "systems/system-configuration", "systems/security-legacy"),
        },
        {
            "track": "logging-diagnostics",
            "purpose": "syslog, err/warn-style diagnostics, formatted messages, and teachable logging/error-reporting practice.",
            "prefixes": (),
            "domains": ("systems/logging-diagnostics",),
        },
        {
            "track": "runtime-services",
            "purpose": "Dynamic loading, regex, iconv, locale/message catalogs, legacy DBM, and libc search structures.",
            "prefixes": (),
            "domains": (
                "systems/dynamic-loading",
                "systems/text-patterns",
                "systems/localization-conversion",
                "systems/legacy-database",
                "systems/search-structures",
            ),
        },
        {
            "track": "error-handling",
            "purpose": "Error objects, errno mapping, assertions/check helpers, reporting, and failure-aware control flow.",
            "prefixes": (),
            "domains": ("support/error-core", "support/error-codes", "support/assertions"),
        },
        {
            "track": "environment-lifecycle",
            "purpose": "Creating, configuring, labeling, duplicating, and destroying p101 environments.",
            "prefixes": (),
            "domains": ("support/environment-lifecycle",),
        },
        {
            "track": "event-streams",
            "purpose": "Resource events, call tracing, fault injection, event-log formatting, and observer configuration.",
            "prefixes": (),
            "domains": ("support/resource-events", "support/call-tracing", "support/fault-injection", "support/event-log-format"),
        },
        {
            "track": "fsm",
            "purpose": "Finite-state-machine structure, state transitions, callbacks, invalid transitions, and lifecycle ownership.",
            "prefixes": (),
            "domains": ("support/fsm",),
        },
        {
            "track": "tool-building",
            "purpose": "C facts, small analyzers, and writing tools that reason about p101 projects.",
            "prefixes": ("tooling/",),
            "domains": (),
        },
    ]

    assigned: set[str] = set()
    recommendations: list[dict[str, Any]] = []
    for spec in track_specs:
        domains = [
            domain
            for domain in sorted(domain_counts)
            if domain not in assigned
            and (domain in spec["domains"] or any(domain.startswith(prefix) for prefix in spec["prefixes"]))
        ]
        assigned.update(domains)
        recommendations.append(
            {
                "track": spec["track"],
                "purpose": spec["purpose"],
                "function_count": sum(domain_counts[domain] for domain in domains),
                "domains": domains,
            }
        )
    return recommendations


def repository_recommendation(domain_counts: Counter[str], tracks: list[dict[str, Any]]) -> dict[str, Any]:
    assigned = {domain for track in tracks for domain in track["domains"]}
    uncovered = sorted(domain for domain in domain_counts if domain not in assigned)
    return {
        "repository": "playgrounds",
        "layout": "single repository with small explicit tracks",
        "track_count": len(tracks),
        "covered_function_count": sum(domain_counts[domain] for domain in assigned),
        "uncovered_function_count": sum(domain_counts[domain] for domain in uncovered),
        "uncovered_domains": uncovered,
        "tracks": tracks,
    }


def write_json(
    path: Path,
    nodes: dict[str, FunctionNode],
    edges: list[FunctionEdge],
    domains: dict[str, dict[str, Any]],
    repo_plan: dict[str, Any],
) -> None:
    data = {
        "summary": {
            "function_count": len(nodes),
            "edge_count": len(edges),
            "domain_count": len(domains),
        },
        "nodes": [asdict(node) for node in sorted(nodes.values(), key=lambda item: item.name)],
        "edges": [asdict(edge) for edge in edges],
        "domains": domains,
        "repository_recommendation": repo_plan,
        "track_recommendations": repo_plan["tracks"],
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_dot(path: Path, nodes: dict[str, FunctionNode], edges: list[FunctionEdge]) -> None:
    lines = ["digraph p101_lib_functions {", "  rankdir=LR;", "  node [shape=box, fontsize=10];"]
    for node in sorted(nodes.values(), key=lambda item: item.name):
        lines.append(f'  "{node.name}" [label="{node.name}\\n{node.domain}"];')
    for edge in edges:
        if edge.kind == "native-call":
            continue
        style = "solid" if edge.kind == "wrapper-call" else "dashed"
        lines.append(f'  "{edge.source}" -> "{edge.target}" [label="{edge.kind}", style={style}];')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def domain_mermaid(repo_plan: dict[str, Any]) -> str:
    lines = ["```mermaid", "flowchart LR"]
    repo_id = repo_plan["repository"].replace("-", "_")
    lines.append(f'  {repo_id}["{repo_plan["repository"]}\\n{repo_plan["track_count"]} tracks"]')
    for rec in repo_plan["tracks"]:
        track_id = f"track_{rec['track'].replace('-', '_')}"
        lines.append(f'  {track_id}["{rec["track"]}\\n{rec["function_count"]} wrappers"] --> {repo_id}')
        for domain in rec["domains"]:
            did = re.sub(r"[^A-Za-z0-9_]", "_", domain)
            lines.append(f'  {did}["{domain}"] --> {track_id}')
    lines.append("```")
    return "\n".join(lines)


def write_markdown(
    path: Path,
    nodes: dict[str, FunctionNode],
    edges: list[FunctionEdge],
    domains: dict[str, dict[str, Any]],
    repo_plan: dict[str, Any],
) -> None:
    lib_counts = Counter(node.library for node in nodes.values())
    domain_counts = Counter(node.domain for node in nodes.values())
    wrapper_edges = sum(1 for edge in edges if edge.kind == "wrapper-call")
    wrapped_native_edges = sum(1 for edge in edges if edge.kind == "wrapped-native")

    lines: list[str] = [
        "# p101 library function graph",
        "",
        "Generated from active `libraries/lib_*` directories. `_to_delete` is excluded.",
        "",
        "## Summary",
        "",
        f"- Wrapper/function nodes: `{len(nodes)}`",
        f"- Edges: `{len(edges)}`",
        f"- Wrapper-to-wrapper edges: `{wrapper_edges}`",
        f"- Wrapper-to-native wrapped-call edges: `{wrapped_native_edges}`",
        f"- Domains: `{len(domains)}`",
        f"- Recommended repo: `{repo_plan['repository']}`",
        f"- Recommended tracks: `{repo_plan['track_count']}`",
        f"- Uncovered domains: `{len(repo_plan['uncovered_domains'])}`",
        "",
        "## Single playground repo graph",
        "",
        domain_mermaid(repo_plan),
        "",
        "## Recommended repo structure",
        "",
        f"Use one playground repository, `{repo_plan['repository']}`, with small explicit tracks inside it. Do not create a broad `systems` playground and do not create a `misc` track.",
        "",
        "| Track | Function count | Domains | Purpose |",
        "| --- | ---: | --- | --- |",
    ]
    for rec in repo_plan["tracks"]:
        lines.append(f"| `{rec['track']}` | {rec['function_count']} | {', '.join(f'`{domain}`' for domain in rec['domains'])} | {rec['purpose']} |")

    lines.extend(["", "## Coverage", ""])
    if repo_plan["uncovered_domains"]:
        lines.append(f"Uncovered domains: {', '.join(f'`{domain}`' for domain in repo_plan['uncovered_domains'])}.")
    else:
        lines.append("Every discovered domain is assigned to one primary track.")

    lines.extend(
        [
            "",
            "## Curriculum reading",
            "",
            "- The existing wrapper examples can collapse into tracks inside one playground repo once each cluster has a working \"good path\" plus focused defect labs.",
            "- `systems/ipc` is big enough to justify an IPC unit, especially when paired with `systems/io-multiplexing` so students see blocking, readiness, cleanup, and ownership together.",
            "- File I/O is split into descriptor I/O, filesystem path operations, and directory/pattern traversal; these are related but teach different failure modes.",
            "- Threading and networking are split into smaller lifecycle/synchronization/cancellation and socket/I/O/name/DNS tracks; students should not get the whole subsystem at once.",
            "- Dynamic loading, regex, iconv/catalogs, DBM, and XSI search are split out explicitly; they belong in `runtime-services`, not in a misc bucket.",
            "- Observability is split into `error-handling`, `environment-lifecycle`, `event-streams`, `fsm`, and `tool-building`; students should see the pieces separately before they compose them.",
        ]
    )

    lines.extend(["", "## Counts by library", "", "| Library | Functions |", "| --- | ---: |"])
    for library, count in sorted(lib_counts.items()):
        lines.append(f"| `{library}` | {count} |")

    lines.extend(["", "## Domain clusters", "", "| Domain | Functions | Libraries | Playground signal |", "| --- | ---: | --- | --- |"])
    for domain, count in sorted(domain_counts.items()):
        libraries = ", ".join(f"`{name}`:{value}" for name, value in sorted(domains[domain]["libraries"].items()))
        signal = "candidate"
        if domain == "systems/ipc":
            signal = "strong IPC playground cluster"
        elif domain == "systems/io-multiplexing":
            signal = "systems reference/advanced cluster"
        elif domain == "network":
            signal = "networking track"
        elif domain in {
            "systems/dynamic-loading",
            "systems/text-patterns",
            "systems/localization-conversion",
            "systems/legacy-database",
            "systems/search-structures",
        }:
            signal = "runtime-services track"
        elif domain.startswith("c/"):
            signal = "C-family track"
        elif domain.startswith("systems/"):
            signal = "systems-family track"
        elif domain.startswith("support/error") or domain == "support/assertions":
            signal = "error-handling track"
        elif domain == "support/environment-lifecycle":
            signal = "environment-lifecycle track"
        elif domain in {"support/resource-events", "support/call-tracing", "support/fault-injection", "support/event-log-format"}:
            signal = "event-streams track"
        elif domain == "support/fsm":
            signal = "fsm track"
        elif domain.startswith("tooling/") or domain == "support/util":
            signal = "tool-building track"
        lines.append(f"| `{domain}` | {count} | {libraries} | {signal} |")

    lines.extend(["", "## Large clusters and representative functions", ""])
    for domain, count in sorted(domain_counts.items(), key=lambda item: (-item[1], item[0])):
        funcs = domains[domain]["functions"]
        sample = ", ".join(f"`{name}`" for name in funcs[:35])
        more = "" if len(funcs) <= 35 else f" … +{len(funcs) - 35} more"
        lines.extend([f"### `{domain}` ({count})", "", sample + more, ""])

    lines.extend(["## Files", "", "- JSON: `lib-function-graph.json`", "- DOT: `lib-function-graph.dot`", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes, edges, _declared = collect(root)
    domains = domain_summaries(nodes)
    domain_counts = Counter(node.domain for node in nodes.values())
    tracks = track_recommendations(domain_counts)
    repo_plan = repository_recommendation(domain_counts, tracks)
    if any("misc" in domain for domain in domain_counts):
        raise RuntimeError("misc domains are not allowed; split vague buckets into explicit tracks")
    write_json(out_dir / "lib-function-graph.json", nodes, edges, domains, repo_plan)
    write_dot(out_dir / "lib-function-graph.dot", nodes, edges)
    write_markdown(out_dir / "lib-function-graph.md", nodes, edges, domains, repo_plan)
    print(f"wrote {out_dir / 'lib-function-graph.md'}")
    print(f"wrote {out_dir / 'lib-function-graph.json'}")
    print(f"wrote {out_dir / 'lib-function-graph.dot'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
