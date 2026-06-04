#!/usr/bin/env bash
# Shared helpers for managing .env files.
# Source from deploy scripts: source "$(dirname "$0")/lib/env-utils.sh"

# Update or append a single key=value pair in an .env file, preserving all
# other lines. Uses awk to avoid sed delimiter injection (values containing
# |, &, /, etc. are safe).
#
# Usage: update_env <env_file> <key> <value>
update_env() {
  local env_file="$1" key="$2" value="$3"
  if [[ -f "$env_file" ]] && grep -q "^${key}=" "$env_file"; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} 1' "$env_file" > "${env_file}.tmp" \
      && mv "${env_file}.tmp" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

# Safely load an .env file into the shell without executing arbitrary code.
# Only lines matching KEY=VALUE (no spaces in key, no shell expansion) are
# exported. Comments and blank lines are skipped.
#
# Usage: safe_source_env <env_file>
safe_source_env() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    key="${key%%[[:space:]]}"
    value="${value#[[:space:]]}"
    export "$key=$value"
  done < "$env_file"
}

# Read an .env file into the current shell as variables prefixed with OLD_.
# Useful for scripts that want to preserve pre-existing values across a re-run.
#
# Usage: load_env_as_old <env_file>
load_env_as_old() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    declare -g "OLD_${key}=${value}"
  done < "$env_file"
}
