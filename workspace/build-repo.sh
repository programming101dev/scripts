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
sanitizers_given=false
forward_skip_cache=false   # if true, pass -S to install.sh (skip cache refresh)
skip_install=false
interactive=false
defer_install=false
finalize_only=false
build_key=""
runtime_build_key=""
repository_build_cache="${P101_REPOSITORY_BUILD_CACHE:-}"
defer_build_markers="${P101_DEFER_BUILD_MARKERS:-0}"

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
  -S  forward 'skip cache update' to install.sh (passes -S to install.sh)
  -I  skip install.sh after building repos
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
    s) sanitizers="$OPTARG"; sanitizers_given=true ;;
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

change_compiler_supports_sanitizers() {
  # Inspect the option parser instead of executing --help: older repository
  # scripts do not all give --help the same exit behavior.
  grep -Fq -- '-s)' ./change-compiler.sh
}

change_compiler_supports_runtime_artifact() {
  grep -Fq -- '-b)' ./change-compiler.sh &&
    grep -Fq -- '--)' ./change-compiler.sh &&
    change_compiler_supports_sanitizers
}

change_compiler_supports_build_dir() {
  grep -Fq -- '-b)' ./change-compiler.sh
}

CC_PATH="$(resolve_any "$c_compiler")"
CXX_PATH="$(resolve_any "$cxx_compiler")"
CLANG_FORMAT_PATH="$(resolve_any "$clang_format_name")"
CLANG_TIDY_PATH="$(resolve_any "$clang_tidy_name")"
CPPCHECK_PATH="$(resolve_any "$cppcheck_name")"
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
    -P "$profile_mode" -K quality)"
fi
if [[ -z "$runtime_build_key" ]]; then
  runtime_build_key="$($BUILD_LANE_SH -c "$CC_PATH" -x "$CXX_PATH" \
    -F "$flags_profile" -K runtime)"
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

build_script_args=()
if [[ -n "${P101_QUIET:-}" ]]; then
  build_script_args+=(-q)
fi

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

# Read repos.txt on fd 3 so the children (change-compiler.sh, build.sh,
# install.sh — which may legitimately read stdin, e.g. a sudo password
# prompt in install.sh) keep the real stdin.
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
  make_tree=false

  # Resolve the artifact identity before configuring. Parallel compiler-pair
  # workers must never depend on the shared .last-build-dir marker: another
  # worker may legitimately update that convenience marker at any time.
  case "$repo_type" in
    c)
      if [[ ! -x ./change-compiler.sh ]]; then
        say "  -> FAIL: no executable change-compiler.sh in ${dir}."
        failures=$((failures + 1))
        popd >/dev/null
        continue
      fi
      quality_build_dir="$(content_addressed_build_directory "build-${build_key}")"
      if change_compiler_supports_build_dir; then
        if activate_repository_build_cache "$repository_cache_label" "$quality_build_dir"; then
          quality_cache_state="$P101_LANE_CACHE_STATE"
        else
          status=$?
          popd >/dev/null
          exit "$status"
        fi
        change_args=(-c "$CC_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH" -b "$quality_build_dir")
      else
        # A Makefile tree accepts its toolchain through make's environment.
        # Do not run its source-mutating change-compiler helper in a matrix.
        make_tree=true
      fi
      ;;
    cxx)
      if [[ ! -x ./change-compiler.sh ]]; then
        say "  -> FAIL: no executable change-compiler.sh in ${dir}."
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
      change_args=(-c "$CXX_PATH" -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH" -b "$quality_build_dir")
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
    if [[ "$repo_type" == "c" || "$repo_type" == "cxx" ]] && ! $make_tree; then
      if $sanitizers_given && change_compiler_supports_sanitizers; then
        change_args+=(-s "$sanitizers")
      elif [[ -n "$sanitizers" ]]; then
        say "  -> change-compiler.sh does not accept -s; using the repository's configured sanitizer flags."
      fi
      if [[ -n "$repository_build_cache" ]]; then
        change_args+=(-R)
      fi
      change_args+=(-- "-DP101_BUILD_KEY=$build_key"
        "-DP101_COVERAGE_MODE=$([[ "$coverage_mode" -eq 1 ]] && printf ON || printf OFF)"
        "-DP101_PROFILE_MODE=$([[ "$profile_mode" -eq 1 ]] && printf ON || printf OFF)")
      say "Configuring ${dir} in ${quality_build_dir}"
      if run_repo_phase "configure ${dir}" ./change-compiler.sh "${change_args[@]}"; then
        write_lane_receipt "$quality_build_dir" "$build_key" quality "${quality_cache_state:-disabled}"
        marker_restore_current_repository
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
    fi

    if [[ -x ./build.sh ]]; then
      say "Building: ${dir}"
      if $make_tree; then
        build_command=(env P101_INCREMENTAL_BUILD=1 "CC=$CC_PATH" "CXX=$CXX_PATH" "CLANG_FORMAT=$CLANG_FORMAT_PATH" "CLANG_TIDY=$CLANG_TIDY_PATH" "CPPCHECK=$CPPCHECK_PATH" ./build.sh)
      else
        build_command=(env P101_INCREMENTAL_BUILD=1 ./build.sh -b "$quality_build_dir")
      fi
      if [[ "${#build_script_args[@]}" -gt 0 ]]; then
        build_command+=("${build_script_args[@]}")
      fi
      if run_repo_phase "build ${dir} with $([[ "$repo_type" == "cxx" ]] && printf '%s' "$CXX_PATH" || printf '%s' "$CC_PATH")" "${build_command[@]}"; then
        if [[ -n "$quality_build_dir" ]]; then
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
     [[ "$skip_install" == false || "$defer_install" == true ]] &&
     [[ -x ./install.sh ]] &&
     [[ "$repo_type" == "c" || "$repo_type" == "cxx" ]]; then
    if ! change_compiler_supports_runtime_artifact; then
      runtime_install_supported=0
      say "  -> SKIP install: change-compiler.sh cannot create a separate instrumentation-free runtime artifact."
    else
      runtime_build_dir="$(content_addressed_build_directory "build-${runtime_build_key}")"
      if activate_repository_build_cache "$repository_cache_label" "$runtime_build_dir"; then
        runtime_cache_state="$P101_LANE_CACHE_STATE"
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi
      if [[ "$repo_type" == "cxx" ]]; then
        runtime_change_args=(-c "$CXX_PATH")
      else
        runtime_change_args=(-c "$CC_PATH")
      fi
      runtime_change_args+=(
        -f "$CLANG_FORMAT_PATH"
        -t "$CLANG_TIDY_PATH"
        -k "$CPPCHECK_PATH"
        -s ""
        -b "$runtime_build_dir"
        --
        -DP101_RUNTIME_ONLY=ON
        -DP101_DISABLE_INSTRUMENTATION=ON
        -DP101_COVERAGE_MODE=OFF
        -DP101_PROFILE_MODE=OFF
        "-DP101_BUILD_KEY=$runtime_build_key"
      )
      if [[ -n "$repository_build_cache" ]]; then
        runtime_change_args=(-R "${runtime_change_args[@]}")
      fi

      say "Configuring instrumentation-free runtime artifact: ${dir}"
      if run_repo_phase "configure runtime artifact ${dir}" \
          env P101_COVERAGE=0 P101_PROFILE=0 \
          CFLAGS= CXXFLAGS= CPPFLAGS= LDFLAGS= \
          ./change-compiler.sh "${runtime_change_args[@]}"; then
        write_lane_receipt "$runtime_build_dir" "$runtime_build_key" runtime "${runtime_cache_state:-disabled}"
        marker_restore_current_repository
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi

      say "Building instrumentation-free runtime artifact: ${dir}"
      if [[ "${#build_script_args[@]}" -gt 0 ]]; then
        runtime_build_command=(env P101_INCREMENTAL_BUILD=1 ./build.sh -b "$runtime_build_dir" "${build_script_args[@]}")
      else
        runtime_build_command=(env P101_INCREMENTAL_BUILD=1 ./build.sh -b "$runtime_build_dir")
      fi
      if run_repo_phase "build runtime artifact ${dir}" \
          "${runtime_build_command[@]}"; then
        :
      else
        status=$?
        popd >/dev/null
        exit "$status"
      fi

      marker_queue_value .last-runtime-build-dir "$runtime_build_dir"
    fi
  fi

  # After concurrent workers finish, update-all calls --finalize-only once for
  # its deterministic host pair. Publish only that pair's markers and select
  # its instrumentation-free runtime artifact for installation.
  if $finalize_only && [[ "$repo_type" == "c" || "$repo_type" == "cxx" ]] && ! $make_tree; then
    if [[ ! -f "$quality_build_dir/CMakeCache.txt" ]] ||
       ! lane_receipt_matches "$quality_build_dir" "$build_key" quality; then
      say "  -> FAIL: host artifact is missing: ${dir}/${quality_build_dir}"
      failures=$((failures + 1))
      runtime_install_supported=0
    else
      marker_queue_value .last-build-dir "$quality_build_dir"
      runtime_candidate="$(content_addressed_build_directory "build-${runtime_build_key}")"
      if [[ -f "$runtime_candidate/CMakeCache.txt" ]] &&
         lane_receipt_matches "$runtime_candidate" "$runtime_build_key" runtime; then
        runtime_build_dir="$runtime_candidate"
      elif ! $skip_install && [[ -x ./install.sh ]]; then
        say "  -> FAIL: host runtime artifact is missing: ${dir}/${runtime_candidate}"
        failures=$((failures + 1))
        runtime_install_supported=0
      else
        runtime_install_supported=0
      fi
      if [[ -n "$runtime_build_dir" ]]; then
        marker_queue_value .last-runtime-build-dir "$runtime_build_dir"
      else
        marker_queue_absent .last-runtime-build-dir
      fi
    fi
  fi

  # If there’s an installer, run it (forward -s to skip cache if -S was given)
  if $defer_install; then
    say "Deferring install until compiler matrix completion: ${dir}"
  elif $skip_install; then
    say "Skipping install: ${dir}"
  elif [[ "$runtime_install_supported" -eq 0 ]]; then
    say "Skipping install without an instrumentation-free runtime artifact: ${dir}"
  elif [[ -x ./install.sh ]]; then
    if $forward_skip_cache; then
      say "Installing (skip cache update): ${dir}"
      install_command=(./install.sh -S)
    else
      say "Installing: ${dir}"
      install_command=(./install.sh)
    fi
    install_command+=(-b "${runtime_build_dir:-$quality_build_dir}")
    if run_repo_phase "install ${dir}" "${install_command[@]}"; then
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
