#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Install an immutable ISEKAI Git release into a project.

Usage:
  scripts/install.sh --source GIT_URL --ref GIT_TAG [options]

Required:
  --source GIT_URL          Git repository containing the ISEKAI release
  --ref GIT_TAG             Immutable release tag or commit

Options:
  --path PROJECT            Target project directory (default: current directory)
  --runtime RUNTIME         all, codex, claude, or kiro; repeatable (default: all)
  --adopt-foundation        Replace an existing project Foundation during install
  --init                    Create project.json after installation when absent
  --profile PROFILE_ID      Project profile used with --init; repeatable
  --maximum-agent-level L   L0 (read-only) or L1 (bounded local changes) with --init
  --python EXECUTABLE       Python 3.11+ executable (default: python3 or python)
  -h, --help                Show this help

Example:
  scripts/install.sh \
    --source https://github.com/dotwin7/isekai.git \
    --ref v0.1.0 \
    --path . \
    --runtime all \
    --init
EOF
}

fail() {
  printf 'ISEKAI bootstrap error: %s\n' "$1" >&2
  exit 1
}

require_value() {
  local option="$1"
  local count="$2"
  ((count >= 2)) || fail "$option requires a value"
}

source_url=""
release_ref=""
project_path="."
python_executable=""
adopt_foundation=0
initialize=0
runtimes=()
profiles=()
maximum_agent_level="L0"
agent_level_set=0

while (($#)); do
  case "$1" in
    --source)
      require_value "$1" "$#"
      source_url="$2"
      shift 2
      ;;
    --ref)
      require_value "$1" "$#"
      release_ref="$2"
      shift 2
      ;;
    --path)
      require_value "$1" "$#"
      project_path="$2"
      shift 2
      ;;
    --runtime)
      require_value "$1" "$#"
      case "$2" in
        all|codex|claude|kiro) runtimes+=("$2") ;;
        *) fail "unknown runtime: $2" ;;
      esac
      shift 2
      ;;
    --adopt-foundation)
      adopt_foundation=1
      shift
      ;;
    --init)
      initialize=1
      shift
      ;;
    --profile)
      require_value "$1" "$#"
      profiles+=("$2")
      shift 2
      ;;
    --maximum-agent-level)
      require_value "$1" "$#"
      case "$2" in
        L0|L1) maximum_agent_level="$2" ;;
        *) fail "unknown maximum agent level: $2" ;;
      esac
      agent_level_set=1
      shift 2
      ;;
    --python)
      require_value "$1" "$#"
      python_executable="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

[[ -n "$source_url" ]] || fail "--source is required"
[[ -n "$release_ref" ]] || fail "--ref is required"
[[ "$source_url" != -* ]] || fail "--source cannot start with '-'"
[[ ! "$source_url" =~ ^[A-Za-z][A-Za-z0-9+.-]*:: ]] \
  || fail "--source must not use a Git transport helper"
if [[ "$source_url" == *"://"* ]]; then
  [[ ! "$source_url" =~ ^[Hh][Tt][Tt][Pp][Ss]?://[^/@]*@ ]] \
    || fail "--source must not contain embedded credentials"
  [[ ! "$source_url" =~ ^[A-Za-z][A-Za-z0-9+.-]*://[^/@]*:[^/@]*@ ]] \
    || fail "--source must not contain embedded credentials"
  [[ "$source_url" != *\?* && "$source_url" != *\#* ]] \
    || fail "--source must not contain a query or fragment"
  if [[ "$source_url" =~ ^[Ff][Ii][Ll][Ee]://([^/]*) ]]; then
    file_authority="${BASH_REMATCH[1]}"
    [[ -z "$file_authority" || "$file_authority" =~ ^[Ll][Oo][Cc][Aa][Ll][Hh][Oo][Ss][Tt]$ ]] \
      || fail "--source file URL must omit its authority or use localhost"
  fi
fi
[[ "$release_ref" != -* ]] || fail "--ref cannot start with '-'"
[[ -d "$project_path" ]] || fail "project directory does not exist: $project_path"
if ((${#profiles[@]} > 0 && initialize == 0)); then
  fail "--profile requires --init"
fi
if ((agent_level_set == 1 && initialize == 0)); then
  fail "--maximum-agent-level requires --init"
fi

command -v git >/dev/null 2>&1 || fail "git is required"
if [[ ! "$release_ref" =~ ^[0-9a-fA-F]{40}$ && ! "$release_ref" =~ ^[0-9a-fA-F]{64}$ ]]; then
  git check-ref-format "refs/tags/$release_ref" >/dev/null 2>&1 \
    || fail "Git ref must be an immutable tag or full commit; revision expressions are not allowed: $release_ref"
fi
if [[ -z "$python_executable" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python_executable="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    python_executable="$(command -v python)"
  else
    fail "Python 3.11 or newer is required"
  fi
fi
[[ -x "$python_executable" ]] || command -v "$python_executable" >/dev/null 2>&1 \
  || fail "Python executable not found: $python_executable"
"$python_executable" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Python 3.11 or newer is required"

project_path="$(cd "$project_path" && pwd -P)"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/isekai-bootstrap.XXXXXX")"
cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  rm -rf -- "$temporary_root"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

checkout="$temporary_root/release"
git clone --quiet --no-checkout -- "$source_url" "$checkout"
resolved_commit=""
if [[ "$release_ref" =~ ^[0-9a-fA-F]{40}$ || "$release_ref" =~ ^[0-9a-fA-F]{64}$ ]]; then
  if ! resolved_commit="$(git -C "$checkout" rev-parse --verify "${release_ref}^{commit}" 2>/dev/null)"; then
    fail "Git commit does not exist: $release_ref"
  fi
  normalized_ref="$(printf '%s' "$release_ref" | tr '[:upper:]' '[:lower:]')"
  normalized_commit="$(printf '%s' "$resolved_commit" | tr '[:upper:]' '[:lower:]')"
  [[ "$normalized_commit" == "$normalized_ref" ]] \
    || fail "Git ref is not the requested full commit: $release_ref"
else
  if ! resolved_commit="$(git -C "$checkout" rev-parse --verify "refs/tags/${release_ref}^{commit}" 2>/dev/null)"; then
    fail "Git ref must be an immutable tag or full commit; branches and abbreviated commits are not allowed: $release_ref"
  fi
fi
git -C "$checkout" checkout --quiet --detach "$resolved_commit"

# Hand the resolved checkout to Core instead of letting it clone again. A second
# clone would re-resolve the tag, so a tag that moved in between would install a
# different commit than the one verified above.
install_args=(
  -m isekai install
  --source "$source_url"
  --ref "$release_ref"
  --path "$project_path"
  --checkout "$checkout"
)
if ((${#runtimes[@]} == 0)); then
  runtimes=(all)
fi
for runtime in "${runtimes[@]}"; do
  install_args+=(--runtime "$runtime")
done
((adopt_foundation == 0)) || install_args+=(--adopt-foundation)

PYTHONPATH="$checkout/src" "$python_executable" "${install_args[@]}"

if ((initialize == 1)); then
  if [[ -e "$project_path/project.json" ]]; then
    printf 'ISEKAI project already initialized: %s\n' "$project_path/project.json" >&2
  else
    init_args=(
      "$python_executable"
      "$project_path/.isekai/bin/isekai.py"
      init
      --path "$project_path"
      --maximum-agent-level "$maximum_agent_level"
    )
    if ((${#profiles[@]} > 0)); then
      for profile in "${profiles[@]}"; do
        init_args+=(--profile "$profile")
      done
    fi
    "${init_args[@]}"
  fi
fi

printf 'ISEKAI installation complete: %s\n' "$project_path" >&2
