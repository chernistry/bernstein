#!/usr/bin/env bash
# GitHub API rate-limit guard for long-running loops.
#
# Wraps `gh api rate_limit` and emits a verdict suitable for use in a
# loop's per-iteration preflight. The watchdog and other long-running
# agent scripts source/call this guard to back off when burn rate is
# unsafe, rather than waiting for a 403 mid-iteration.
#
# Usage:
#   source scripts/gh_rate_limit_guard.sh
#   if ! gh_rate_limit_ok 500; then
#     # remaining < 500: back off
#     sleep 1800
#     continue
#   fi
#
# Or stand-alone:
#   scripts/gh_rate_limit_guard.sh check 500
#     -> exit 0 (remaining >= threshold)
#     -> exit 1 (remaining < threshold; also prints "remaining=N" to stderr)
#     -> exit 2 (API call failed or jq missing)
#
# Conservative default threshold is 500 / 5000 (10%). At 30-min cadence
# with a 5-PR wave, watchdog burns ~30 calls per iteration; threshold
# leaves ~16 iterations of headroom before exhaustion.

set -u

DEFAULT_THRESHOLD=500

# Returns 0 if remaining-core >= threshold, 1 if below, 2 on error.
# Prints "remaining=<N> threshold=<T>" to stderr on non-OK paths.
gh_rate_limit_ok() {
  local threshold="${1:-$DEFAULT_THRESHOLD}"
  command -v gh >/dev/null 2>&1 || return 2
  command -v jq >/dev/null 2>&1 || return 2
  local remaining
  remaining=$(gh api rate_limit --jq '.resources.core.remaining' 2>/dev/null) || return 2
  case "$remaining" in
    ''|*[!0-9]*) return 2 ;;
  esac
  if [ "$remaining" -lt "$threshold" ]; then
    local reset
    reset=$(gh api rate_limit --jq '.resources.core.reset' 2>/dev/null || echo "?")
    echo "rate_limit_low remaining=$remaining threshold=$threshold reset_at_epoch=$reset" >&2
    return 1
  fi
  return 0
}

# CLI mode: same exit codes as the function.
if [ "${BASH_SOURCE[0]:-}" = "${0}" ] && [ "${1:-}" = "check" ]; then
  shift
  gh_rate_limit_ok "${1:-$DEFAULT_THRESHOLD}"
  exit $?
fi
