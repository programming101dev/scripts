#!/usr/bin/env bash
# build-repo.sh — configure + build (+ optional install) every repo in repos.txt

set -euo pipefail

# Always operate from the scripts repository root (repos.txt lives there, and
# the relative destination paths in it are relative to that directory).
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
REFRESH_REPO_SH="${PWD}/distribution/refresh-repo.sh"
BUILD_LANE_SH="${PWD}/workspace/build-lane.sh"

# ----------------- defaults -----------------
c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""
forward_skip_cache=false   # if true, skip the platform loader-cache refresh
skip_install=false
interactive=false
defer_install=false
finalize_only=false
build_key=""
runtime_build_key=""
repository_build_cache="${P101_REPOSITORY_BUILD_CACHE:-}"
defer_build_markers="${P101_DEFER_BUILD_MARKERS:-0}"
build_level="${P101_BUILD_LEVEL:-1}"

usage() {
  cat <<USAGE >&2
Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-B <lane>] [-U <runtime-lane>] [-S] [-I] [--interactive] [--defer-install|--finalize-only]
  -c  C compiler         (e.g. gcc-15, clang)
  -x  C++ compiler       (e.g. g++-15, clang++)
  -f  clang-format       (default: clang-format; path or name)
  -t  clang-tidy         (default: clang-tidy;  path or name)
  -k  cppcheck           (default: cppcheck;    path or name)
  -s  sanitizers list    (e.g. address,undefined) — if omitted, repo may read sanitizers.txt
  -B  quality lane key   (normally computed from the complete compiler pair)
  -U  runtime lane key   (instrumentation-free companion to -B)
  -S  skip the platform loader-cache refresh after installation
  -I  skip installation after building repositories
  -i, --interactive
      Pause after a configure, build, or install failure. Push the fix from
      another terminal, then press Enter to pull it and retry that same phase.
      Enter 'q' to abort.
  --defer-install
      Build the selected compiler pair's instrumentation-free runtime artifacts, but do
      not install them. Used by update-all's parallel host worker.
  --finalize-only
      Select the already-built compiler artifacts, publish deterministic build
      markers, and install them unless -I was supplied. Does not configure or
      compile anything.

Example:
  $0 -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck -s address,undefined -S
USAGE
  exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

# Extract long options before getopts. Do not use eval: tool and compiler
# paths may contain shell metacharacters.
filtered_args=()
for argument in "$@"; do
  case "$argument" in
    --interactive) interactive=true ;;
    --defer-install) defer_install=true ;;
    --finalize-only) finalize_only=true ;;
    *) filtered_args+=("$argument") ;;
  esac
done
if [[ "${#filtered_args[@]}" -gt 0 ]]; then
  set -- "${filtered_args[@]}"
else
  set --
fi
unset filtered_args argument

# ----------------- args -----------------
while getopts ":c:x:f:t:k:s:B:U:SIi" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    B) build_key="$OPTARG" ;;
    U) runtime_build_key="$OPTARG" ;;
    S) forward_skip_cache=true ;;
    I) skip_install=true ;;
    i) interactive=true ;;
    \?|:) usage ;;
  esac
done

[[ -n "$c_compiler"   ]] || { echo "Error: -c (C compiler) is required" >&2; usage; }
[[ -n "$cxx_compiler" ]] || { echo "Error: -x (C++ compiler) is required" >&2; usage; }
[[ -x "$REFRESH_REPO_SH" ]] || { echo "Error: refresh-repo.sh is missing or not executable" >&2; exit 2; }
[[ -x "$BUILD_LANE_SH" ]] || { echo "Error: build-lane.sh is missing or not executable" >&2; exit 2; }
if $defer_install && $finalize_only; then
  echo "Error: --defer-install and --finalize-only are mutually exclusive" >&2
  exit 2
fi
case "$build_level" in
  1|2|3) ;;
  *) echo "Error: P101_BUILD_LEVEL must be 1, 2, or 3" >&2; exit 2 ;;
esac
if [[ -n "$repository_build_cache" ]]; then
  case "$repository_build_cache" in
    /*) ;;
    *)
      echo "Error: P101_REPOSITORY_BUILD_CACHE must be an absolute path" >&2
      exit 2
      ;;
  esac
  mkdir -p -- "$repository_build_cache"
  repository_build_cache="$(CDPATH='' cd -P -- "$repository_build_cache" && pwd -P)"
fi

# ----------------- helpers -----------------
say() { printf '%b\n' "$*"; }
hr()  { printf '%*s\n' "$(tput cols 2>/dev/null || echo 80)" '' | tr ' ' -; }

marker_transaction_directory=""
marker_transaction_count=0
marker_transaction_committed=false
marker_queue=""
current_marker_snapshot=""

marker_write_atomic() {
  local repository="$1"
  local marker="$2"
  local value="$3"
  local temporary="$repository/.${marker}.tmp.$$"

  printf '%s\n' "$value" > "$temporary"
  mv -f -- "$temporary" "$repository/$marker"
}

marker_snapshot_repository() {
  local repository="$1"
  local marker
  local value

  marker_transaction_count=$((marker_transaction_count + 1))
  current_marker_snapshot="$marker_transaction_directory/state-$marker_transaction_count"
  mkdir -p -- "$current_marker_snapshot"
  printf '%s\n' "$repository" > "$current_marker_snapshot/repository"
  for marker in .last-build-dir .last-runtime-build-dir; do
    value=""
    if [[ -f "$repository/$marker" ]]; then
      IFS= read -r value < "$repository/$marker" || true
    fi
    if [[ -n "$value" && -d "$repository/$value" ]]; then
      cp -- "$repository/$marker" "$current_marker_snapshot/$marker"
    else
      : > "$current_marker_snapshot/$marker.absent"
    fi
  done
}

marker_restore_snapshot() {
  local state="$1"
  local repository
  local marker

  IFS= read -r repository < "$state/repository"
  for marker in .last-build-dir .last-runtime-build-dir; do
    if [[ -f "$state/$marker" ]]; then
      marker_write_atomic "$repository" "$marker" "$(cat "$state/$marker")"
    else
      rm -f -- "$repository/$marker"
    fi
  done
}

marker_restore_current_repository() {
  [[ -n "$current_marker_snapshot" ]] || return 0
  marker_restore_snapshot "$current_marker_snapshot"
}

marker_restore_all() {
  local state

  [[ -d "$marker_transaction_directory" ]] || return 0
  for state in "$marker_transaction_directory"/state-*; do
    [[ -d "$state" ]] || continue
    marker_restore_snapshot "$state"
  done
}

marker_queue_value() {
  local marker="$1"
  local value="$2"

  printf '%s\t%s\t%s\n' "$PWD" "$marker" "$value" >> "$marker_queue"
}

marker_queue_absent() {
  local marker="$1"

  printf '%s\t%s\t-\n' "$PWD" "$marker" >> "$marker_queue"
}

marker_publish_queue() {
  local repository
  local marker
  local value

  while IFS=$'\t' read -r repository marker value || [[ -n "$repository" ]]; do
    [[ -n "$repository" && -n "$marker" ]] || continue
    if [[ "$value" == - ]]; then
      rm -f -- "$repository/$marker"
    else
      [[ -d "$repository/$value" ]] || {
        printf 'Error: refusing to publish missing build marker target: %s/%s\n' \
          "$repository" "$value" >&2
        return 2
      }
      marker_write_atomic "$repository" "$marker" "$value"
    fi
  done < "$marker_queue"
}

marker_transaction_finish() {
  local status="$1"

  trap - EXIT
  if ! $marker_transaction_committed; then
    marker_restore_all || status=2
  fi
  [[ -z "$marker_transaction_directory" ]] || rm -rf -- "$marker_transaction_directory"
  exit "$status"
}

content_addressed_build_directory() {
  local base="$1"
  local identity

  if [[ -z "$repository_build_cache" ]]; then
    printf '%s\n' "$base"
    return 0
  fi
  identity="$({
    printf '%s\n' "$repository_source_identity"
    printf '%s\n' "$orchestrator_source_identity"
  } | git hash-object --stdin)"
  printf '%s__%s\n' "$base" "$identity"
}

publish_exact_lane_alias() {
  local directory="$1"
  local lane="$2"
  local alias="build-${lane}"
  local temporary
  local target="$directory"

  # P101Linking resolves workspace dependencies through the stable exact-lane
  # name.  Content addressing is an implementation detail of the cache, so
  # publish a repository-local alias only after the selected artifact builds
  # successfully.  A failed build therefore leaves the last qualified alias
  # intact for diagnosis and later reuse.
  [[ "$directory" != "$alias" ]] || return 0
  # Content-addressed repository paths are convenience links into the external
  # cache.  The cache collector may remove an unselected convenience link
  # while retaining the underlying lane for another compiler pair.  Point the
  # stable compiler-lane alias directly at that underlying directory so it
  # never becomes dangling merely because another pair became the host lane.
  if [[ -n "$repository_build_cache" && -L "$directory" ]]; then
    target="$(CDPATH='' cd -P -- "$directory" && pwd -P)"
  fi
  temporary="${alias}.tmp.$$"
  rm -f -- "$temporary"
  ln -s -- "$target" "$temporary"
  if [[ -L "$alias" ]]; then
    rm -f -- "$alias"
  elif [[ -d "$alias" ]]; then
    rm -rf -- "$alias"
  elif [[ -e "$alias" ]]; then
    printf 'Error: exact-lane alias path is not a directory or symlink: %s\n' \
      "$alias" >&2
    rm -f -- "$temporary"
    return 2
  fi
  mv -f -- "$temporary" "$alias"
}

write_lane_receipt() {
  local directory="$1"
  local lane="$2"
  local kind="$3"
  local cache_state="${4:-disabled}"
  local lane_sanitizers="$sanitizers"
  local lane_coverage="$coverage_mode"
  local lane_profile="$profile_mode"
  local temporary

  if [[ "$kind" == runtime ]]; then
    lane_sanitizers=""
    lane_coverage=0
    lane_profile=0
  fi
  mkdir -p -- "$directory"
  temporary="$directory/.p101-build-lane.tmp.$$"
  {
    printf 'schema=p101-build-lane-receipt-v1\n'
    printf 'lane=%s\n' "$lane"
    printf 'kind=%s\n' "$kind"
    if [[ "$kind" == runtime ]]; then
      printf 'build_level=1\n'
    else
      printf 'build_level=%s\n' "$build_level"
    fi
    printf 'c_compiler=%s\n' "$CC_PATH"
    printf 'cxx_compiler=%s\n' "$CXX_PATH"
    printf 'flags_profile=%s\n' "$flags_profile"
    printf 'sanitizers=%s\n' "$lane_sanitizers"
    printf 'coverage=%s\n' "$lane_coverage"
    printf 'profile=%s\n' "$lane_profile"
    printf 'dependency_policy=exact-workspace-lane\n'
    printf 'cache_state=%s\n' "$cache_state"
    printf 'source_identity=%s\n' "$repository_source_identity"
    printf 'orchestrator_identity=%s\n' "$orchestrator_source_identity"
  } > "$temporary"
  mv -f -- "$temporary" "$directory/p101-build-lane.txt"
}

git_untracked_source_paths() {
  local path
  local target

  git ls-files --others --exclude-standard | while IFS= read -r path || [[ -n "$path" ]]; do
    if [[ -n "$repository_build_cache" && -L "$path" ]]; then
      target="$(CDPATH='' cd -P -- "$path" 2>/dev/null && pwd -P || true)"
      case "$target" in
        "$repository_build_cache"/*) continue ;;
      esac
    fi
    printf '%s\n' "$path"
  done
}

git_source_identity() {
  local commit
  local dirty_identity
  local path
  local untracked_paths

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'external:%s' "${GITHUB_SHA:-snapshot}"
    return 0
  fi
  commit="$(git rev-parse HEAD)"
  untracked_paths="$(git_untracked_source_paths)"
  if git diff --quiet -- && git diff --cached --quiet -- &&
     [[ -z "$untracked_paths" ]]; then
    printf 'commit:%s' "$commit"
    return 0
  fi

  dirty_identity="$({
    printf 'HEAD %s\n' "$commit"
    git diff --binary HEAD --
    while IFS= read -r path || [[ -n "$path" ]]; do
      [[ -n "$path" ]] || continue
      printf 'UNTRACKED %s ' "$path"
      git hash-object -- "$path"
    done <<< "$untracked_paths"
  } | git hash-object --stdin)"
  printf 'worktree:%s' "$dirty_identity"
}

orchestrator_build_identity() {
  local path
  local policy_identity

  policy_identity="$({
    {
      printf '%s\n' CMakeLists.txt
      printf '%s\n' workspace/build-repo.sh
      printf '%s\n' workspace/build-lane.sh
      printf '%s\n' shared/compilers.sh
      if [[ -d cmake ]]; then
        find cmake -type f -print
      fi
    } | LC_ALL=C sort -u | while IFS= read -r path || [[ -n "$path" ]]; do
      [[ -f "$path" ]] || continue
      printf '%s ' "$path"
      git hash-object -- "$path"
    done
  } | git hash-object --stdin)"
  printf 'policy:%s' "$policy_identity"
}

activate_repository_build_cache() {
  local repository_label="$1"
  local directory="$2"
  local cache_directory
  local cache_state_label
  local current_target
  local receipt
  local valid_identity=false

  P101_LANE_CACHE_STATE=disabled
  [[ -n "$repository_build_cache" ]] || return 0
  case "$repository_build_cache" in
    /*) ;;
    *)
      printf 'Error: P101_REPOSITORY_BUILD_CACHE must be an absolute path: %s\n' \
        "$repository_build_cache" >&2
      return 2
      ;;
  esac

  cache_directory="$repository_build_cache/$repository_label/$directory"
  mkdir -p -- "$(dirname -- "$cache_directory")"
  if [[ -L "$directory" ]]; then
    current_target="$(CDPATH='' cd -P -- "$directory" 2>/dev/null && pwd -P || true)"
    if [[ "$current_target" != "$cache_directory" ]]; then
      printf 'Error: build-lane symlink points outside its admitted cache: %s -> %s\n' \
        "$directory" "${current_target:-missing}" >&2
      return 2
    fi
  elif [[ -e "$directory" ]]; then
    if [[ -e "$cache_directory" ]]; then
      printf 'Error: both repository and cache copies exist for build lane %s.\n' \
        "$directory" >&2
      return 2
    fi
    mv -- "$directory" "$cache_directory"
    ln -s -- "$cache_directory" "$directory"
  else
    mkdir -p -- "$cache_directory"
    ln -s -- "$cache_directory" "$directory"
  fi

  receipt="$cache_directory/p101-build-lane.txt"
  if [[ -f "$cache_directory/CMakeCache.txt" && -f "$receipt" ]] &&
     grep -Fqx "source_identity=$repository_source_identity" "$receipt" &&
     grep -Fqx "orchestrator_identity=$orchestrator_source_identity" "$receipt"; then
    valid_identity=true
  fi
  if $valid_identity; then
    P101_LANE_CACHE_STATE=hit
  else
    if [[ -n "$(find "$cache_directory" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      rm -rf -- "$cache_directory"
      mkdir -p -- "$cache_directory"
    fi
    P101_LANE_CACHE_STATE=miss
  fi
  cache_state_label="$(printf '%s' "$P101_LANE_CACHE_STATE" | tr '[:lower:]' '[:upper:]')"
  say "  -> Build cache ${cache_state_label}: ${repository_label}/${directory}"
}

lane_receipt_matches() {
  local directory="$1"
  local lane="$2"
  local kind="$3"
  local receipt="$directory/p101-build-lane.txt"

  [[ -f "$receipt" ]] &&
    grep -Fqx "schema=p101-build-lane-receipt-v1" "$receipt" &&
    grep -Fqx "lane=$lane" "$receipt" &&
    grep -Fqx "kind=$kind" "$receipt"
}

run_repo_phase() {
  local description="$1"
  local status
  local pull_status
  local answer

  shift
  while true; do
    set +e
    "$@"
    status=$?
    set -e
    if [[ "$status" -eq 0 ]]; then
      return 0
    fi
    if ! $interactive; then
      return "$status"
    fi

    while true; do
      printf '\nFAILED: %s (exit %d).\n' "$description" "$status" >&2
      printf 'Fix the issue, then press Enter to retry this phase; enter q to abort: ' >&2
      if ! IFS= read -r answer; then
        printf '\nInteractive input closed; aborting.\n' >&2
        return "$status"
      fi
      case "$answer" in
        q|Q|quit|QUIT)
          printf 'Aborting at: %s\n' "$description" >&2
          return "$status"
          ;;
      esac

        printf 'Refreshing repository upstream before retry...\n' >&2
        set +e
        "${REFRESH_REPO_SH}" .
        pull_status=$?
        set -e
        if [[ "$pull_status" -ne 0 && "$pull_status" -ne 1 ]]; then
          printf 'Repository refresh failed (exit %d); still paused.\n' "$pull_status" >&2
          continue
        fi
        printf 'Retrying: %s\n\n' "$description" >&2
        break
    done
  done
}

# compiler names resolve through the pinned map first (compiler_paths.txt,
# written by check-compilers.sh), then PATH; absolute paths pass through
MAP_FILE="compiler_paths.txt"
map_lookup() {
  local name="$1" line
  [[ -f "$MAP_FILE" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in "$name="*) printf '%s' "${line#*=}"; return 0 ;; esac
  done < "$MAP_FILE"
  return 1
}

resolve_any() {
  local v="$1" p
  if [[ "$v" = /* ]]; then
    [[ -x "$v" ]] || { echo "Error: '$v' not executable" >&2; exit 2; }
    printf '%s' "$v"
  elif p="$(map_lookup "$v")" && [[ -x "$p" ]]; then
    printf '%s' "$p"
  else
    p="$(command -v "$v" 2>/dev/null)" || { echo "Error: '$v' not found in $MAP_FILE or PATH" >&2; exit 2; }
    printf '%s' "$p"
  fi
}

configure_cmake_repository() {
  local repo_type="$1"
  local build_directory="$2"
  local lane_kind="$3"
  local lane_key="$4"
  local lane_sanitizers="$sanitizers"
  local lane_level="$build_level"
  local runtime_only=OFF
  local coverage_value=OFF
  local profile_value=OFF
  local cmake_args

  if [[ "$lane_kind" == runtime ]]; then
    lane_sanitizers=""
    lane_level=1
    runtime_only=ON
  else
    [[ "$coverage_mode" -eq 0 ]] || coverage_value=ON
    [[ "$profile_mode" -eq 0 ]] || profile_value=ON
  fi

  if [[ -z "$repository_build_cache" ]]; then
    case "$build_directory" in
      build|build-*|cmake-build-*) rm -rf -- "$build_directory" ;;
      *)
        printf 'Error: unsafe CMake build directory: %s\n' "$build_directory" >&2
        return 2
        ;;
    esac
  fi

  cmake_args=(
    -S . -B "$build_directory"
    -DCLANG_FORMAT_NAME="$CLANG_FORMAT_PATH"
    -DCLANG_TIDY_NAME="$CLANG_TIDY_PATH"
    -DCPPCHECK_NAME="$CPPCHECK_PATH"
    -DSANITIZER_LIST="$lane_sanitizers"
    -DP101_BUILD_KEY="$lane_key"
    -DP101_BUILD_LEVEL="$lane_level"
    -DP101_RUNTIME_ONLY="$runtime_only"
    -DP101_COVERAGE_MODE="$coverage_value"
    -DP101_PROFILE_MODE="$profile_value"
    -DCMAKE_BUILD_TYPE=Debug
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  )
  # Test trees commonly compile both C APIs and C++ header-compatibility
  # probes, even when the production project enables only one language.
  cmake_args+=(
    -DCMAKE_C_COMPILER="$CC_PATH"
    -DCMAKE_CXX_COMPILER="$CXX_PATH"
  )
  if [[ "$lane_kind" == runtime ]]; then
    cmake_args+=(-DP101_DISABLE_INSTRUMENTATION=ON)
  fi
  cmake "${cmake_args[@]}"
}

configure_runtime_cmake_repository() {
  local repo_type="$1"
  local build_directory="$2"
  local lane_key="$3"

  (
    export P101_COVERAGE=0
    export P101_PROFILE=0
    export CFLAGS=
    export CXXFLAGS=
    export CPPFLAGS=
    export LDFLAGS=
    configure_cmake_repository "$repo_type" "$build_directory" runtime "$lane_key"
  )
}

build_cmake_repository() {
  local build_directory="$1"
  local build_args=(cmake --build "$build_directory")

  [[ -n "${P101_QUIET:-}" ]] || build_args+=(--verbose)
  [[ -z "${JOBS:-${CMAKE_BUILD_PARALLEL_LEVEL:-}}" ]] ||
    build_args+=(--parallel "${JOBS:-${CMAKE_BUILD_PARALLEL_LEVEL}}")
  "${build_args[@]}"
}

install_cmake_repository() {
  local build_directory="$1"
  local install_prefix="/usr/local"
  local cached_prefix
  local install_args=(cmake --install "$build_directory")

  cached_prefix="$(sed -n 's/^CMAKE_INSTALL_PREFIX:[^=]*=//p' \
    "$build_directory/CMakeCache.txt" | head -n 1)"
  [[ -z "$cached_prefix" ]] || install_prefix="$cached_prefix"
  if [[ -d "$install_prefix" ]]; then
    if [[ -w "$install_prefix" ]]; then
      "${install_args[@]}"
    else
      sudo "${install_args[@]}"
    fi
  elif [[ -w "$(dirname "$install_prefix")" ]]; then
    "${install_args[@]}"
  else
    sudo "${install_args[@]}"
  fi

  if ! $forward_skip_cache; then
    if [[ "$(uname -s)" != Darwin ]] && command -v ldconfig >/dev/null 2>&1; then
      sudo ldconfig
    elif command -v update_dyld_shared_cache >/dev/null 2>&1; then
      sudo update_dyld_shared_cache -force
    fi
  fi
}

CC_PATH="$(resolve_any "$c_compiler")"
CXX_PATH="$(resolve_any "$cxx_compiler")"
CLANG_FORMAT_PATH="$(resolve_any "$clang_format_name")"
if [[ "$build_level" -eq 3 ]]; then
  CLANG_TIDY_PATH="$(resolve_any "$clang_tidy_name")"
  CPPCHECK_PATH="$(resolve_any "$cppcheck_name")"
else
  # The CMake configuration accepts concrete tool arguments even when the
  # selected level does not admit those tools. Use a ubiquitous successful
  # command as an inert value instead of making quick/medium users install the
  # full analyzer toolchain.
  CLANG_TIDY_PATH="$(resolve_any true)"
  CPPCHECK_PATH="$(resolve_any true)"
fi
orchestrator_source_identity="$(orchestrator_build_identity)"

env_on() {
  local value="${1:-}"
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  [[ -n "$value" && "$value" != 0 && "$value" != off &&
     "$value" != false && "$value" != no ]]
}

flags_profile=maximal
if [[ "${P101_NO_FLAGS:-0}" == 1 ]]; then
  flags_profile=none
elif [[ "${P101_FLAGS_PROFILE:-}" == standard ]]; then
  flags_profile=standard
fi
coverage_mode=0
profile_mode=0
if env_on "${P101_COVERAGE:-}"; then
  coverage_mode=1
fi
if env_on "${P101_PROFILE:-}"; then
  profile_mode=1
fi

if [[ -z "$build_key" ]]; then
  build_key="$($BUILD_LANE_SH -c "$CC_PATH" -x "$CXX_PATH" \
    -s "$sanitizers" -F "$flags_profile" -C "$coverage_mode" \
    -P "$profile_mode" -K quality -L "$build_level")"
fi
if [[ -z "$runtime_build_key" ]]; then
  runtime_build_key="$($BUILD_LANE_SH -c "$CC_PATH" -x "$CXX_PATH" \
    -F "$flags_profile" -K runtime -L 1)"
fi
case "$build_key" in
  ''|*[!A-Za-z0-9_.+-]*)
    echo "Error: build lane keys contain unsupported path characters" >&2
    exit 2
    ;;
esac
case "$runtime_build_key" in
  ''|*[!A-Za-z0-9_.+-]*)
    echo "Error: runtime lane key contains unsupported path characters" >&2
    exit 2
    ;;
esac
say "Quality artifact lane: $build_key"
say "Runtime artifact lane: $runtime_build_key"

# ----------------- iterate repos -----------------
repos_file="repos.txt"
[[ -f "$repos_file" ]] || { echo "Error: $repos_file not found" >&2; exit 3; }

marker_transaction_directory="$(mktemp -d "${TMPDIR:-/tmp}/p101-build-markers.XXXXXX")"
marker_queue="$marker_transaction_directory/pending.tsv"
: > "$marker_queue"
trap 'marker_transaction_finish $?' EXIT

trim() {
  local s="${1-}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

failures=0
processed=0

# Read repos.txt on fd 3 so CMake and sudo keep the real stdin.
while IFS= read -r raw <&3 || [[ -n "${raw:-}" ]]; do
  # Strip CR (CRLF files), comments, and surrounding whitespace — same
  # semantics as clone-repos.sh / link-*.sh / copy-cmake.sh.
  raw="${raw%$'\r'}"
  raw="${raw%%#*}"
  line="$(trim "${raw}")"
  [[ -z "$line" ]] && continue

  IFS='|' read -r repo_url dir repo_type <<< "$line"
  repo_url="$(trim "${repo_url:-}")"
  dir="$(trim "${dir:-}")"
  repo_type="$(trim "${repo_type:-}")"

  if [[ -z "$repo_url" || -z "$dir" || -z "$repo_type" ]]; then
    say "  -> FAIL: malformed line: $raw"
    failures=$((failures + 1))
    continue
  fi
  processed=$((processed + 1))

  hr
  say "Working on ${dir} (${repo_type})"

  if [[ ! -d "$dir" ]]; then
    say "  -> FAIL: directory not found: $dir"
    failures=$((failures + 1))
    continue
  fi

  if [[ "$repo_type" == "c-bootstrap" ]]; then
    say "  -> Bootstrap repository is present but not active; skipping."
    continue
  fi
  if [[ "$repo_type" == "c-reference" ]]; then
    say "  -> Source-reference repository has no executable build contract; skipping."
    continue
  fi

  pushd "$dir" >/dev/null
  marker_snapshot_repository "$PWD"
  repository_source_identity="$(git_source_identity)"
  repository_cache_label="$(printf '%s' "$dir" | sed 's#[^A-Za-z0-9_.+-]#_#g')"
  repository_cache_label="${repository_cache_label}__$(printf '%s' "$dir" | git hash-object --stdin)"
  quality_build_dir=""
  runtime_build_dir=""
  quality_cache_state=disabled
  runtime_cache_state=disabled
  runtime_install_supported=1
  installable_repository=false
  [[ "$dir" == ../libraries/* ]] && installable_repository=true

  # Resolve the artifact identity before configuring. Parallel compiler-pair
  # workers must never depend on the shared .last-build-dir marker: another
  # worker may legitimately update that convenience marker at any time.
  case "$repo_type" in
    c)
      if [[ ! -f ./CMakeLists.txt ]]; then
        say "  -> FAIL: no CMakeLists.txt in ${dir}."
        failures=$((failures + 1))
        popd >/dev/null
        continue
      else
        quality_build_dir="$(content_addressed_build_directory "build-${build_key}")"
        if activate_repository_build_cache "$repository_cache_label" "$quality_build_dir"; then
          quality_cache_state="$P101_LANE_CACHE_STATE"
        else
          status=$?
          popd >/dev/null
          exit "$status"
        fi
      fi
      ;;
    cxx)
      if [[ ! -f ./CMakeLists.txt ]]; then
        say "  -> FAIL: no CMakeLists.txt in ${dir}."
        failures=$((failures + 1))
        popd >/dev/null
        continue
      fi
      quality_build_dir="$(content_addressed_build_directory "build-${build_key}")"
      if activate_repository_build_cache "$repository_cache_label" "$quality_build_dir"; then
        quality_cache_state="$P101_LANE_CACHE_STATE"
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
      ;;
    python)
      say "Python tool: no CMake/compiler configuration required."
      ;;
    *)
      say "  -> FAIL: unknown repo type '${repo_type}'."
      failures=$((failures + 1))
      popd >/dev/null
      continue
      ;;
  esac

  if ! $finalize_only; then
    if [[ "$repo_type" == "c" || "$repo_type" == "cxx" ]]; then
      say "Configuring ${dir} in ${quality_build_dir}"
      if run_repo_phase "configure ${dir}" \
          configure_cmake_repository "$repo_type" "$quality_build_dir" quality "$build_key"; then
        write_lane_receipt "$quality_build_dir" "$build_key" quality "${quality_cache_state:-disabled}"
        marker_restore_current_repository
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
    fi

    if [[ -f ./CMakeLists.txt ]]; then
      say "Building: ${dir}"
      build_command=(build_cmake_repository "$quality_build_dir")
      if run_repo_phase "build ${dir} with $([[ "$repo_type" == "cxx" ]] && printf '%s' "$CXX_PATH" || printf '%s' "$CC_PATH")" "${build_command[@]}"; then
        if [[ -n "$quality_build_dir" ]]; then
          publish_exact_lane_alias "$quality_build_dir" "$build_key"
          marker_queue_value .last-build-dir "$quality_build_dir"
        fi
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
    else
      say "  -> FAIL: no executable build.sh found."
      failures=$((failures + 1))
    fi
  fi

  # Quality builds may carry compiler-private sanitizer, coverage, or profiling
  # runtimes. Installing one can break a consumer from another compiler lane
  # (notably Apple Clang programs loading Homebrew-Clang ASan libraries).
  #
  # Reconfigure only installable C/C++ repositories as a lightweight,
  # instrumentation-free runtime build. P101_RUNTIME_ONLY retains real targets
  # and install rules while omitting the analyzer pipeline already exercised
  # by the quality build above.
  if ! $finalize_only &&
     $installable_repository &&
     [[ "$skip_install" == false || "$defer_install" == true ]] &&
     [[ -f ./CMakeLists.txt ]] &&
     [[ "$repo_type" == "c" || "$repo_type" == "cxx" ]]; then
    runtime_build_dir="$(content_addressed_build_directory "build-${runtime_build_key}")"
      if activate_repository_build_cache "$repository_cache_label" "$runtime_build_dir"; then
        runtime_cache_state="$P101_LANE_CACHE_STATE"
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
      say "Configuring instrumentation-free runtime artifact: ${dir}"
      if run_repo_phase "configure runtime artifact ${dir}" \
          configure_runtime_cmake_repository "$repo_type" "$runtime_build_dir" "$runtime_build_key"; then
        write_lane_receipt "$runtime_build_dir" "$runtime_build_key" runtime "${runtime_cache_state:-disabled}"
        marker_restore_current_repository
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi

      say "Building instrumentation-free runtime artifact: ${dir}"
      runtime_build_command=(build_cmake_repository "$runtime_build_dir")
      if run_repo_phase "build runtime artifact ${dir}" \
          "${runtime_build_command[@]}"; then
        publish_exact_lane_alias "$runtime_build_dir" "$runtime_build_key"
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi

      marker_queue_value .last-runtime-build-dir "$runtime_build_dir"
  fi

  # After concurrent workers finish, update-all calls --finalize-only once for
  # its deterministic host pair. Publish only that pair's markers and select
  # its instrumentation-free runtime artifact for installation.
  if $finalize_only && [[ "$repo_type" == "c" || "$repo_type" == "cxx" ]]; then
    if [[ ! -f "$quality_build_dir/CMakeCache.txt" ]] ||
       ! lane_receipt_matches "$quality_build_dir" "$build_key" quality; then
      say "  -> FAIL: host artifact is missing: ${dir}/${quality_build_dir}"
      failures=$((failures + 1))
      runtime_install_supported=0
    else
      publish_exact_lane_alias "$quality_build_dir" "$build_key"
      marker_queue_value .last-build-dir "$quality_build_dir"
      runtime_candidate="$(content_addressed_build_directory "build-${runtime_build_key}")"
      if [[ -f "$runtime_candidate/CMakeCache.txt" ]] &&
         lane_receipt_matches "$runtime_candidate" "$runtime_build_key" runtime; then
        runtime_build_dir="$runtime_candidate"
      elif $installable_repository && ! $skip_install && [[ -f ./CMakeLists.txt ]]; then
        say "  -> FAIL: host runtime artifact is missing: ${dir}/${runtime_candidate}"
        failures=$((failures + 1))
        runtime_install_supported=0
      else
        runtime_install_supported=0
      fi
      if [[ -n "$runtime_build_dir" ]]; then
        publish_exact_lane_alias "$runtime_build_dir" "$runtime_build_key"
        marker_queue_value .last-runtime-build-dir "$runtime_build_dir"
      else
        marker_queue_absent .last-runtime-build-dir
      fi
    fi
  fi

  # Install directly from the selected CMake runtime artifact.
  if $defer_install; then
    say "Deferring install until compiler matrix completion: ${dir}"
  elif $skip_install; then
    say "Skipping install: ${dir}"
  elif [[ "$runtime_install_supported" -eq 0 ]]; then
    say "Skipping install without an instrumentation-free runtime artifact: ${dir}"
  elif $installable_repository && [[ -f ./CMakeLists.txt ]]; then
    say "Installing: ${dir}"
    if run_repo_phase "install ${dir}" \
        install_cmake_repository "${runtime_build_dir:-$quality_build_dir}"; then
      :
    else
      status=$?
      popd >/dev/null
      exit "$status"
    fi
  fi

  popd >/dev/null
done 3< "$repos_file"

hr
if [[ "$processed" -eq 0 ]]; then
  say "FAIL: repos.txt did not contain any repositories."
  exit 1
fi
if [[ "$failures" -gt 0 ]]; then
  say "Repository build failed: ${failures} configuration problem(s)."
  exit 1
fi
if [[ "$defer_build_markers" == 1 ]]; then
  marker_restore_all
else
  marker_publish_queue
fi
marker_transaction_committed=true
say "All ${processed} repositories processed successfully."
