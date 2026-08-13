# Native wrapper header and source layout

## Decision

Functional ownership and native API shape are separate concerns.

- A wrapper remains owned by the functional library that teaches its purpose
  (`lib_io`, `lib_text`, `lib_process`, and so on).
- Inside that library, its public header mirrors the native header that declares
  the underlying interface. For example, string wrappers live in
  `lib_text/include/p101_text/p101_string.h`, while socket wrappers live in
  `lib_network/include/p101_network/sys/p101_socket.h`.
- Implementation files mirror those public headers:
  `p101_string.h` is implemented by `src/string.c`, and
  `sys/p101_socket.h` is implemented by `src/sys/socket.c`.
- POSIX, XSI, optional-POSIX, and common-Unix provenance remains metadata in the
  API manifest. It does not create source or include directories.
- A source file may include another native-shaped header when one wrapper calls
  another, but each public wrapper has exactly one owning header and one
  correspondingly named implementation file.

## Why

The functional repositories answer “what is this operation for?” Native-shaped
files answer “where would a C programmer expect to find this interface?” Keeping
both dimensions makes the libraries teachable without inventing a parallel
header taxonomy or reviving the retired `lib_posix`, `lib_posix_xsi`,
`lib_posix_optional`, and `lib_unix` boundaries.

## Tradeoff

There are more files and build-manifest entries than in the former one-header,
one-source layout. In return, individual interfaces are easier to find, header
dependencies are visible, source and interface names agree, and changes have a
smaller compilation and review surface.

## Enforcement

The native `audit-workspace --policy functional-library-split` checker derives
the expected current header
and source from each API manifest’s recorded native header and rejects:

- a wrapper assigned to the wrong native-shaped file;
- a public header without its mirrored source, or vice versa;
- provenance directories under `include/` or `src/`;
- a build manifest that omits or invents public headers or implementation
  sources.
