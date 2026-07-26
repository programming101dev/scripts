#!/usr/bin/env bash
# uninstall.sh — remove installed headers/libs/pkgconfig/cmake for a lib
# Auto-detects NAME so you can reuse across repos.
# Works on macOS, Linux, *BSD. Uses sudo only when required.
set -euo pipefail

ASSUME_YES=false
DRY_RUN=false
VERBOSE=false
NAME=""              # will be auto-detected unless -N is provided
CAP_NAME=""          # capitalized NAME (portable)
declare -a PREFIXES=()

usage() {
  cat <<USAGE
Usage: $0 [-n] [-y] [-v] [-N <name>] [-p <prefix> ...]
  -n            Dry run (show what would be removed, don't delete)
  -y            Assume 'yes' to prompts (non-interactive)
  -v            Verbose
  -N <name>     Override library/package name (auto-detect by default)
  -p <prefix>   Add install prefix to search (repeatable). Checked before defaults.
Examples:
  $0
  $0 -n -v
  $0 -N mylib -p /custom/prefix
USAGE
  exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

while getopts ":nyvN:p:h" opt; do
  case "$opt" in
    n) DRY_RUN=true ;;
    y) ASSUME_YES=true ;;
    v) VERBOSE=true ;;
    N) NAME="$OPTARG" ;;
    p) PREFIXES+=("$OPTARG") ;;
    h|*) usage ;;
  esac
done

log() { $VERBOSE && printf '[info] %s\n' "$*"; }
say() { printf '%s\n' "$*"; }
err() { printf '[error] %s\n' "$*" >&2; }

# --- portable capitalize first letter (Bash 3.2 safe) ---
cap1() { awk '{print toupper(substr($0,1,1)) substr($0,2)}' <<<"$1"; }

# --- auto-detect NAME if not provided ---
detect_name() {
  # 1) include/<dir> (common pattern like include/p101_error)
  if [[ -z "${NAME}" && -d "include" ]]; then
    # pick first subdirectory under include/
    local d
    for d in include/*; do
      [[ -d "$d" ]] || continue
      NAME="$(basename "$d")"
      log "Detected NAME from include/: $NAME"
      break
    done
  fi

  # 2) build/install_manifest.txt
  if [[ -z "${NAME}" && -f "build/install_manifest.txt" ]]; then
    # try to find include/<name>/ or lib/lib<name>.*
    local cand
    cand="$(awk -F'/include/' '/\/include\//{split($2,a,"/"); if(a[1]!="") {print a[1]; exit}}' build/install_manifest.txt || true)"
    if [[ -n "$cand" ]]; then
      NAME="$cand"
      log "Detected NAME from install_manifest (include path): $NAME"
    else
      cand="$(awk -F'/lib/lib' '/\/lib\/lib[^/]+\./{sub(/\..*/,"",$2); print $2; exit}' build/install_manifest.txt || true)"
      if [[ -n "$cand" ]]; then
        NAME="$cand"
        log "Detected NAME from install_manifest (lib path): $NAME"
      fi
    fi
  fi

  # 3) CMakeLists.txt: project(<name>)
  if [[ -z "${NAME}" && -f "CMakeLists.txt" ]]; then
    local cmname
    cmname="$(awk 'BEGIN{IGNORECASE=1}
      /project[[:space:]]*\(/ {
        s=$0
        sub(/.*project[[:space:]]*\(/,"",s)
        sub(/[[:space:]].*/,"",s)
        sub(/\).*/,"",s)
        print s; exit
      }' CMakeLists.txt 2>/dev/null || true)"
    if [[ -n "$cmname" ]]; then
      NAME="$cmname"
      log "Detected NAME from CMakeLists.txt: $NAME"
    fi
  fi

  # 4) fallback: repo directory name
  if [[ -z "${NAME}" ]]; then
    NAME="$(basename "$PWD")"
    log "Falling back to repo directory name: $NAME"
  fi

  CAP_NAME="$(cap1 "$NAME")"
}

confirm() {
  $ASSUME_YES && return 0
  printf '%s [y/N] ' "$*"
  read -r ans || ans=""
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

needs_sudo() {
  local p="$1"
  local parent; parent="$(dirname "$p")"
  [[ -w "$p" || -w "$parent" ]] && return 1 || return 0
}

do_rm() {
  # do_rm [-r] path
  local recursive=""
  if [[ "${1:-}" == "-r" ]]; then recursive="-r"; shift; fi
  local path="$1"
  if $DRY_RUN; then
    say "DRY-RUN: rm $recursive -f -- $path"
    return 0
  fi
  if needs_sudo "$path"; then
    log "Using sudo to remove: $path"
    sudo rm $recursive -f -- "$path" || return $?
  else
    rm $recursive -f -- "$path" || return $?
  fi
}

do_rmdir() {
  local d="$1"
  [[ -d "$d" ]] || return 0
  if $DRY_RUN; then
    say "DRY-RUN: rmdir -- $d"
    return 0
  fi
  if needs_sudo "$d"; then
    sudo rmdir "$d" 2>/dev/null || true
  else
    rmdir "$d" 2>/dev/null || true
  fi
}

dedupe_append() {
  # dedupe_append arr_name items...
  local arr="$1"; shift
  local x
  for x in "$@"; do
    [[ -z "${x// /}" ]] && continue
    if ! eval 'for _y in "${'"$arr"'[@]:-}"; do [[ "$_y" == "'"$x"'" ]] && _hit=1; done; [[ -n "${_hit:-}" ]] && unset _hit || :'; then :; fi
    if ! eval 'for _y in "${'"$arr"'[@]:-}"; do [[ "$_y" == "'"$x"'" ]] && return 0; done'; then
      eval "$arr+=(\"\$x\")"
    fi
  done
}

# --- main flow ---
detect_name
say "Package name: $NAME"

OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
  PREFIX_DEFAULTS=("/usr/local" "/opt/homebrew")
else
  PREFIX_DEFAULTS=("/usr/local" "/usr")
fi

declare -a SEARCH_PREFIXES=()
dedupe_append SEARCH_PREFIXES "${PREFIXES[@]:-}"
dedupe_append SEARCH_PREFIXES "${PREFIX_DEFAULTS[@]}"

if [[ ${#SEARCH_PREFIXES[@]} -eq 0 ]]; then
  err "No prefixes to search."
  exit 2
fi

say "Uninstalling '${NAME}' from prefixes: ${SEARCH_PREFIXES[*]}"

# Build candidate paths
declare -a TARGETS=()
for P in "${SEARCH_PREFIXES[@]}"; do
  TARGETS+=("$P/include/${NAME}")
  for L in "$P/lib" "$P/lib64" "$P/lib/arm64" "$P/lib/aarch64-linux-gnu" "$P/lib/x86_64-linux-gnu"; do
    TARGETS+=("$L/lib${NAME}.a")
    TARGETS+=("$L/lib${NAME}.so")
    TARGETS+=("$L/lib${NAME}.so.0")
    TARGETS+=("$L/lib${NAME}.so.*")
    TARGETS+=("$L/lib${NAME}.dylib")
    TARGETS+=("$L/lib${NAME}.*.dylib")
    TARGETS+=("$L/lib${NAME}.la")
    TARGETS+=("$L/cmake/${NAME}")
    TARGETS+=("$L/cmake/$(cap1 "$NAME")")
    TARGETS+=("$L/pkgconfig/${NAME}.pc")
    TARGETS+=("$L/pkgconfig/${NAME}-*.pc")
  done
  TARGETS+=("$P/share/${NAME}")
  TARGETS+=("$P/share/${NAME}/cmake")
  TARGETS+=("$P/share/cmake/${NAME}")
  TARGETS+=("$P/share/cmake/$(cap1 "$NAME")")
  TARGETS+=("$P/share/pkgconfig/${NAME}.pc")
  TARGETS+=("$P/share/pkgconfig/${NAME}-*.pc")
done

# Filter to existing matches (expand globs safely)
declare -a EXISTING=()
shopt -s nullglob
for pat in "${TARGETS[@]}"; do
  for hit in $pat; do
    [[ -e "$hit" || -L "$hit" ]] && EXISTING+=("$hit")
  done
done
shopt -u nullglob

if [[ ${#EXISTING[@]} -eq 0 ]]; then
  say "Nothing found to remove for '${NAME}'."
  exit 0
fi

say "The following paths will be removed (${#EXISTING[@]}):"
for p in "${EXISTING[@]}"; do
  echo "  $p"
done

if ! confirm "Proceed with removal?"; then
  say "Aborted."
  exit 1
fi

# Remove files/dirs
for p in "${EXISTING[@]}"; do
  if [[ -d "$p" && ! -L "$p" ]]; then
    log "Removing directory: $p"
    do_rm -r "$p" || err "Failed to remove $p"
  else
    log "Removing file: $p"
    do_rm "$p" || err "Failed to remove $p"
  fi
done

# Best-effort cleanup of now-empty dirs
for P in "${SEARCH_PREFIXES[@]}"; do
  do_rmdir "$P/share/pkgconfig"
  do_rmdir "$P/lib/pkgconfig"
  do_rmdir "$P/lib/cmake/${NAME}" || true
  do_rmdir "$P/lib/cmake/$(cap1 "$NAME")" || true
  do_rmdir "$P/lib/cmake" || true
  do_rmdir "$P/share/cmake/${NAME}" || true
  do_rmdir "$P/share/cmake/$(cap1 "$NAME")" || true
  do_rmdir "$P/share/cmake" || true
done

say "Uninstall complete for '${NAME}'."

# Refresh linker caches (if not dry-run)
if ! $DRY_RUN; then
  if command -v ldconfig >/dev/null 2>&1; then
    log "Running ldconfig"
    if needs_sudo "/sbin/ldconfig"; then sudo ldconfig; else ldconfig; fi
  elif command -v update_dyld_shared_cache >/dev/null 2>&1; then
    log "Running update_dyld_shared_cache (macOS)"
    if needs_sudo "/usr/sbin/update_dyld_shared_cache"; then
      sudo update_dyld_shared_cache -force || true
    else
      update_dyld_shared_cache -force || true
    fi
  fi
fi
