#!/usr/bin/env bash
# Shared helpers for managing .env files.
# Source from deploy scripts: source "$(dirname "$0")/lib/env-utils.sh"

# Update or append a single key=value pair in an .env file, preserving all
# other lines. Safer than `cat >` which wipes unrelated keys.
#
# Usage: update_env <env_file> <key> <value>
update_env() {
  local env_file="$1" key="$2" value="$3"
  if [[ -f "$env_file" ]] && grep -q "^${key}=" "$env_file"; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$env_file" && rm -f "${env_file}.bak"
  else
    echo "${key}=${value}" >> "$env_file"
  fi
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
