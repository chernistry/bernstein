#!/usr/bin/env bash
# Advisory PR push-lock for parallel-agent waves.
#
# Prevents two agents from pushing to the same PR head ref
# concurrently. Cooperative; not enforced by GitHub.
#
# Usage:
#   pr_push_lock.sh acquire <pr-number> <agent-id> [ttl-seconds]
#   pr_push_lock.sh release <pr-number> <agent-id>
#   pr_push_lock.sh status  <pr-number>
#
# Exit codes:
#   0 - lock acquired (acquire), released (release), or free (status)
#   1 - lock held by another agent (acquire failed after retries)
#   2 - usage error
#
# Advisory only: no filesystem locks, no kernel arbitration. Each
# cooperating agent must consult this script before pushing to a PR
# head ref. Non-cooperating tools (git itself, gh CLI direct invocation)
# are not bound and rely on --force-with-lease for data safety.

set -u
set -o pipefail

LOCK_FILE="${PR_PUSH_LOCK_FILE:-.sdd/runtime/pr_push_lock.jsonl}"
DEFAULT_TTL_SEC="${PR_PUSH_LOCK_DEFAULT_TTL_SEC:-600}"
RETRY_COUNT="${PR_PUSH_LOCK_RETRY_COUNT:-5}"
RETRY_SLEEP_SEC="${PR_PUSH_LOCK_RETRY_SLEEP_SEC:-30}"

now_iso() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

now_epoch() {
  date -u +%s
}

iso_to_epoch() {
  local v="$1"
  date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$v" +%s 2>/dev/null \
    || date -u -d "$v" +%s 2>/dev/null \
    || echo 0
}

usage() {
  cat <<'EOF' >&2
Usage:
  pr_push_lock.sh acquire <pr-number> <agent-id> [ttl-seconds]
  pr_push_lock.sh release <pr-number> <agent-id>
  pr_push_lock.sh status  <pr-number>
EOF
  exit 2
}

ensure_lock_dir() {
  local dir
  dir=$(dirname "$LOCK_FILE")
  [ -d "$dir" ] || mkdir -p "$dir"
  [ -f "$LOCK_FILE" ] || : > "$LOCK_FILE"
}

# Print the most recent unreleased, unexpired record for a PR, or empty.
# Output: "<agent>\t<expires_iso>" or empty if no holder.
#
# Implementation: portable bash/grep/sed (no gawk extensions). Reads the
# lock file in reverse, tracks released agents, returns the first active
# acquire record that is not subsequently released.
current_holder() {
  local pr="$1"
  [ -f "$LOCK_FILE" ] || return 0
  local pr_pattern="\"pr\"[[:space:]]*:[[:space:]]*${pr}([^0-9]|$)"
  local -a released_agents=()

  # Read newest first via tac fallback (BSD has no tac).
  local lines
  if command -v tac >/dev/null 2>&1; then
    lines=$(tac "$LOCK_FILE")
  else
    lines=$(tail -r "$LOCK_FILE" 2>/dev/null || awk '{a[NR]=$0} END{for(i=NR;i>=1;i--) print a[i]}' "$LOCK_FILE")
  fi

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    # Match this PR only.
    if ! echo "$line" | grep -qE "$pr_pattern"; then
      continue
    fi
    local agent
    agent=$(echo "$line" | sed -n 's/.*"agent"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    [ -z "$agent" ] && continue
    # If this is a release record, remember the agent and skip.
    if echo "$line" | grep -q '"released_at"'; then
      released_agents+=("$agent")
      continue
    fi
    # Active acquire record. Check if a later release matches this agent.
    local already_released=0
    local r
    for r in "${released_agents[@]+"${released_agents[@]}"}"; do
      if [ "$r" = "$agent" ]; then
        already_released=1
        break
      fi
    done
    [ "$already_released" = "1" ] && continue
    local expires_iso
    expires_iso=$(echo "$line" | sed -n 's/.*"expires_at"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    printf '%s\t%s\n' "$agent" "$expires_iso"
    return 0
  done <<< "$lines"
}

is_expired() {
  local expires_iso="$1"
  [ -z "$expires_iso" ] && return 0
  local exp_e
  exp_e=$(iso_to_epoch "$expires_iso")
  [ "$exp_e" -eq 0 ] && return 0
  local now_e
  now_e=$(now_epoch)
  [ "$now_e" -ge "$exp_e" ]
}

acquire() {
  local pr="$1"
  local agent="$2"
  local ttl="${3:-$DEFAULT_TTL_SEC}"
  ensure_lock_dir
  local attempt
  for ((attempt=1; attempt<=RETRY_COUNT; attempt++)); do
    local holder
    holder=$(current_holder "$pr")
    if [ -z "$holder" ]; then
      break
    fi
    local cur_agent cur_exp
    cur_agent=$(echo "$holder" | cut -f1)
    cur_exp=$(echo "$holder" | cut -f2)
    if [ "$cur_agent" = "$agent" ]; then
      # We already hold it; refresh.
      break
    fi
    if is_expired "$cur_exp"; then
      break
    fi
    echo "pr=$pr held by $cur_agent until $cur_exp (attempt $attempt/$RETRY_COUNT)" >&2
    if [ "$attempt" -lt "$RETRY_COUNT" ]; then
      sleep "$RETRY_SLEEP_SEC"
    fi
  done
  # Re-check after retries. Only block if the lock is held by a
  # DIFFERENT agent AND has not yet expired. Expired locks are treated
  # as free here (the natural expiry path).
  local final_holder
  final_holder=$(current_holder "$pr")
  if [ -n "$final_holder" ]; then
    local final_agent final_exp
    final_agent=$(echo "$final_holder" | cut -f1)
    final_exp=$(echo "$final_holder" | cut -f2)
    if [ "$final_agent" != "$agent" ] && ! is_expired "$final_exp"; then
      echo "skipping pr=$pr reason=lock-held-by=$final_agent" >&2
      return 1
    fi
  fi
  local started expires
  started=$(now_iso)
  local started_e
  started_e=$(now_epoch)
  local expires_e=$((started_e + ttl))
  expires=$(date -u -r "$expires_e" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
    || date -u -d "@$expires_e" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
    || echo "")
  printf '{"pr":%s,"agent":"%s","started_at":"%s","expires_at":"%s"}\n' \
    "$pr" "$agent" "$started" "$expires" >> "$LOCK_FILE"
  echo "acquired pr=$pr agent=$agent expires=$expires"
  return 0
}

release() {
  local pr="$1"
  local agent="$2"
  ensure_lock_dir
  local released
  released=$(now_iso)
  printf '{"pr":%s,"agent":"%s","released_at":"%s"}\n' \
    "$pr" "$agent" "$released" >> "$LOCK_FILE"
  echo "released pr=$pr agent=$agent at=$released"
  return 0
}

status() {
  local pr="$1"
  local holder
  holder=$(current_holder "$pr")
  if [ -z "$holder" ]; then
    echo "pr=$pr lock=free"
    return 0
  fi
  local cur_agent cur_exp
  cur_agent=$(echo "$holder" | cut -f1)
  cur_exp=$(echo "$holder" | cut -f2)
  if is_expired "$cur_exp"; then
    echo "pr=$pr lock=expired prev_agent=$cur_agent prev_expires=$cur_exp"
  else
    echo "pr=$pr lock=held agent=$cur_agent expires=$cur_exp"
  fi
  return 0
}

[ "$#" -ge 1 ] || usage
op="$1"
shift
case "$op" in
  acquire)
    [ "$#" -ge 2 ] || usage
    acquire "$@"
    ;;
  release)
    [ "$#" -ge 2 ] || usage
    release "$@"
    ;;
  status)
    [ "$#" -ge 1 ] || usage
    status "$@"
    ;;
  *)
    usage
    ;;
esac
