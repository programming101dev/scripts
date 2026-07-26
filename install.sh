#!/usr/bin/env bash
# install.sh — cmake --install with a few niceties
set -euo pipefail

# ----------------- defaults -----------------
build_dir=""                 # empty means auto
last_build_file=".last-build-dir"

destdir=""
prefix=""
skip_cache_update=false
dry_run=false
verbose=false

usage() {
  cat <<USAGE
Usage: $0 [-b <build>] [-D <DESTDIR>] [-p <prefix>] [-S] [-n] [-v]
  -b <build>    Build directory to install from (default: .last-build-dir if present, else build)
  -D <DESTDIR>  Set DESTDIR for staged installs (e.g., packaging)
  -p <prefix>   Override CMAKE_INSTALL_PREFIX at install time
  -S            Skip cache updates (ldconfig / update_dyld_shared_cache)
  -n            Dry run (print actions only)
  -v            Verbose

Examples:
  $0
  $0 -S
  $0 -b out/build -D pkgroot -p /usr/local
USAGE
  exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

while getopts ":b:D:p:Snvh" opt; do
  case "$opt" in
    b) build_dir="$OPTARG" ;;
    D) destdir="$OPTARG" ;;
    p) prefix="$OPTARG" ;;
    S) skip_cache_update=true ;;
    n) dry_run=true ;;
    v) verbose=true ;;
    h|*) usage ;;
  esac
done

# ----------------- pick build dir -----------------
# Priority:
#  1) -b <dir>
#  2) .last-build-dir (as written by change-compiler.sh)
#  3) "build"
if [[ -z "${build_dir}" ]]; then
  if [[ -f "$last_build_file" ]]; then
    # Read first non-empty line (avoid trailing whitespace issues).
    build_dir_candidate="$(awk 'NF{print; exit}' "$last_build_file" 2>/dev/null || true)"
    if [[ -n "${build_dir_candidate:-}" ]]; then
      build_dir="$build_dir_candidate"
    fi
  fi
fi
build_dir="${build_dir:-build}"

# ----------------- sanity checks -----------------
[[ -d "$build_dir" ]] || { echo "Error: build dir '$build_dir' not found." >&2; exit 2; }

# Compose cmake --install command
cmake_cmd=(cmake --install "$build_dir")
[[ -n "$destdir" ]] && cmake_cmd+=(--component default) # keep component explicit if you use them
[[ -n "$destdir" ]] && export DESTDIR="$destdir"
[[ -n "$prefix"  ]] && cmake_cmd+=(--prefix "$prefix")

# Helper: decide if we likely need sudo for a real (non-DESTDIR) install
# Checks writability of prefix dir if it exists; otherwise checks parent dir.
needs_sudo_for_prefix() {
  local p="${1:-/usr/local}"
  if [[ -d "$p" ]]; then
    [[ -w "$p" ]] || return 0
    return 1
  fi
  local parent
  parent="$(dirname "$p")"
  [[ -w "$parent" ]] || return 0
  return 1
}

if $verbose; then
  echo "Build dir        : $build_dir"
  [[ -n "$destdir" ]] && echo "DESTDIR          : $destdir"
  [[ -n "$prefix"  ]] && echo "Install prefix   : $prefix"
  echo "Skip cache update: $skip_cache_update"
  echo "Dry run          : $dry_run"
  echo "Command          : ${cmake_cmd[*]}"
fi

run() {
  if $dry_run; then
    echo "[dry-run] $*"
  else
    eval "$@"
  fi
}

# ----------------- install -----------------
if $dry_run; then
  run "${cmake_cmd[*]}"
else
  # Only prompt for sudo if needed (prefix/DESTDIR may be writable)
  if [[ -n "$destdir" ]]; then
    # staged install likely user-writable → try without sudo
    "${cmake_cmd[@]}"
  else
    install_prefix="${prefix:-/usr/local}"
    if needs_sudo_for_prefix "$install_prefix"; then
      sudo "${cmake_cmd[@]}"
    else
      "${cmake_cmd[@]}"
    fi
  fi
fi

echo "Installation finished."

# ----------------- post: chown install manifest to match build dir owner (for convenience) -----------------
manifest="$build_dir/install_manifest.txt"
if [[ -f "$manifest" ]]; then
  # Determine owner of build dir
  if [[ "$(uname -s)" == "Darwin" ]]; then
    build_owner="$(stat -f '%Su' "$build_dir")"
  else
    build_owner="$(stat -c '%U' "$build_dir" 2>/dev/null || ls -ld "$build_dir" | awk '{print $3}')"
  fi

  if [[ -n "$build_owner" ]]; then
    if $dry_run; then
      echo "[dry-run] chown '$build_owner' '$manifest'"
    else
      # Use sudo only if we don't own the manifest already
      if [[ -O "$manifest" ]]; then
        chown "$build_owner" "$manifest" 2>/dev/null || true
      else
        sudo chown "$build_owner" "$manifest" 2>/dev/null || true
      fi
    fi
  fi
fi

# ----------------- optional: refresh loader caches -----------------
if ! $skip_cache_update && [[ -z "$destdir" ]]; then
  # Only makes sense for real installs (not staged DESTDIR)
  if [[ "$(uname -s)" == "Darwin" ]]; then
    if command -v update_dyld_shared_cache >/dev/null 2>&1; then
      run "sudo update_dyld_shared_cache -force"
    fi
  else
    if command -v ldconfig >/dev/null 2>&1; then
      run "sudo ldconfig"
    fi
  fi
fi

# ----------------- tips (non-fatal) -----------------
if [[ "$(uname -s)" != "Darwin" && -z "$destdir" ]]; then
  # Gentle reminder about RPATH, only in real installs
  if $verbose; then
    echo "Note: If your binaries fail to locate new libs at runtime, check RPATH / ld.so.conf."
  fi
fi
