#!/usr/bin/env bash
# Fetch documentation/source trees used by discover-common-unix-functions.py.
#
# The analyzer is intentionally offline-only. This helper creates or updates a
# local cache of the public trees that contain the manpages/docs to analyze.

set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

usage() {
  cat <<USAGE >&2
Usage: $0 [-d <destination>]

Fetch/update documentation source trees for:
  - Linux man-pages
  - FreeBSD source tree manpages
  - Apple open-source Libc distribution
  - POSIX function index/pages

Default destination:
  ../unix-doc-sources

Example:
  $0 -d /tmp/unix-doc-sources
USAGE
  exit "${1:-1}"
}
case " $* " in
  *" --help "*|*" -h "*) usage 0 ;;
esac

destination="../unix-doc-sources"

while getopts ":d:h" opt; do
  case "$opt" in
    d) destination="$OPTARG" ;;
    h) usage 0 ;;
    \?|:) usage 2 ;;
  esac
done

mkdir -p "$destination"
destination="$(CDPATH='' cd "$destination" && pwd)"

update_or_clone() {
  local url="$1"
  local dir="$2"
  shift 2

  if [[ -d "$dir/.git" ]]; then
    git -C "$dir" fetch --depth 1 origin
    git -C "$dir" checkout --detach FETCH_HEAD
  else
    git clone --depth 1 "$@" "$url" "$dir"
  fi
}

update_sparse_or_clone() {
  local url="$1"
  local dir="$2"
  shift 2

  if [[ -d "$dir/.git" ]]; then
    git -C "$dir" fetch --depth 1 origin
    git -C "$dir" checkout --detach FETCH_HEAD
  else
    git clone --depth 1 --filter=blob:none --sparse "$url" "$dir"
    git -C "$dir" sparse-checkout set "$@"
  fi
}

linux_dir="$destination/linux-man-pages"
freebsd_dir="$destination/freebsd-src"
macos_dir="$destination/apple-Libc"
posix_dir="$destination/posix-functions"

update_or_clone \
  "https://git.kernel.org/pub/scm/docs/man-pages/man-pages.git" \
  "$linux_dir"

update_sparse_or_clone \
  "https://github.com/freebsd/freebsd-src.git" \
  "$freebsd_dir" \
  "lib/libc" \
  "share/man"

update_or_clone \
  "https://github.com/apple-oss-distributions/Libc.git" \
  "$macos_dir"

mkdir -p "$posix_dir"
python3 - "$posix_dir" <<'PY'
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


BASE = "https://pubs.opengroup.org/onlinepubs/9799919799/functions/"


class FunctionLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        href = dict(attrs).get("href")
        if href is None:
            return

        page = href.split("#", 1)[0]
        name = page.rsplit("/", 1)[-1]
        if "/functions/" in href and href.find("#tag_17_") >= 0 and name.endswith(".html"):
            self.hrefs.add(name)


def fetch(url: str, output: Path) -> bool:
    if output.exists() and output.stat().st_size > 0:
        return True

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            output.write_bytes(response.read())
        return True
    except (OSError, urllib.error.URLError) as error:
        print(f"warning: failed to fetch {url}: {error}", file=sys.stderr)
        return False


out_dir = Path(sys.argv[1])
toc_path = out_dir / "toc.html"
failures = 0

if not fetch(urllib.parse.urljoin(BASE, "toc.html"), toc_path):
    raise SystemExit("error: failed to fetch the POSIX function index")

parser = FunctionLinkParser()
parser.feed(toc_path.read_text(encoding="utf-8", errors="replace"))
if not parser.hrefs:
    raise SystemExit("error: POSIX function index contained no function pages")

for href in sorted(parser.hrefs):
    output = out_dir / href
    if fetch(urllib.parse.urljoin(BASE, href), output):
        time.sleep(0.02)
    else:
        failures += 1

if failures:
    raise SystemExit(f"error: failed to fetch {failures} POSIX function page(s)")
PY

cat <<REPORT
Fetched documentation sources:
  linux:  $linux_dir
  freebsd: $freebsd_dir
  macos:  $macos_dir
  posix:  $posix_dir

Next:
  ./generators/discover-common-unix-functions.py \\
    --linux "$linux_dir" \\
    --freebsd "$freebsd_dir" \\
    --macos "$macos_dir" \\
    --posix-html "$posix_dir/toc.html" \\
    --output "$destination/common-unix-functions.csv" \\
    --emit-probes "$destination/probe-work"
REPORT
