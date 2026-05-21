// Best-effort log line parsing - extracts level + timestamp from common
// Python / Node / agent stdout formats. Anything that doesn't match leaves
// the corresponding field null.

import { stripAnsi } from './ansi';
import type { LogLevel, LogLine } from './types';

// Examples we expect to match (case-insensitive on the level keyword):
//
//   2026-05-15 12:34:56,789 ERROR  bernstein.core: …
//   2026-05-15T12:34:56.789Z [warn] message
//   [2026-05-15 12:34:56] INFO message
//   12:34:56.789 | DEBUG | foo
//   ERROR: something blew up
//   TRACE  fine-grained
//
// We don't try to parse JSON log lines here - those are detected with a
// cheap startsWith('{') check at the renderer level.

const LEVEL_RE =
  /\b(?<level>ERROR|ERR|FATAL|CRIT|CRITICAL|WARN|WARNING|INFO|DEBUG|DBG|TRACE|TRC)\b/i;

const STACK_TRACE_RE =
  /^\s*(Traceback \(most recent call last\):|File "[^"]+", line \d+|at\s+[\w$.<>]+\s+\()/;

// Permissive timestamp matcher - covers ISO-8601 with / without milliseconds
// and the most common space-separated variant. We anchor at start so a
// timestamp embedded mid-line doesn't shift the level offset.
const TIMESTAMP_PREFIX_RE =
  /^\[?(?<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?/;

// HH:MM:SS(.ms)? variant - common for short tail-style logs.
const TIME_ONLY_PREFIX_RE = /^\[?(?<t>\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]?/;

function levelTokenToCanonical(raw: string): LogLevel | null {
  const u = raw.toUpperCase();
  if (u === 'ERROR' || u === 'ERR' || u === 'FATAL' || u === 'CRIT' || u === 'CRITICAL') {
    return 'error';
  }
  if (u === 'WARN' || u === 'WARNING') return 'warn';
  if (u === 'INFO') return 'info';
  if (u === 'DEBUG' || u === 'DBG') return 'debug';
  if (u === 'TRACE' || u === 'TRC') return 'trace';
  return null;
}

function parseTimestamp(plain: string): number | null {
  const m = TIMESTAMP_PREFIX_RE.exec(plain);
  if (m?.groups?.ts) {
    const iso = m.groups.ts.replace(',', '.').replace(' ', 'T');
    const ts = Date.parse(iso);
    if (Number.isFinite(ts)) return ts;
  }
  const m2 = TIME_ONLY_PREFIX_RE.exec(plain);
  if (m2?.groups?.t) {
    // Time-only - anchor to today (caller treats as approximate).
    const [hms, ms] = m2.groups.t.replace(',', '.').split('.');
    const [h, mi, s] = hms.split(':').map((n) => Number.parseInt(n, 10));
    const today = new Date();
    today.setHours(h, mi, s, ms ? Number.parseInt(ms.padEnd(3, '0').slice(0, 3), 10) : 0);
    return today.getTime();
  }
  return null;
}

/**
 * Normalise a raw log line into a `LogLine` record. `id` and `receivedAt`
 * are supplied by the caller so monotonic ordering is preserved across
 * multiple sources (SSE delivery + initial tail fetch).
 */
export function parseLine(raw: string, id: number, receivedAt: number): LogLine {
  const plain = stripAnsi(raw);
  const levelMatch = LEVEL_RE.exec(plain);
  const level = levelMatch ? levelTokenToCanonical(levelMatch[0]) : null;
  return {
    id,
    receivedAt,
    raw,
    plain,
    level,
    timestamp: parseTimestamp(plain),
    stackTrace: STACK_TRACE_RE.test(plain),
  };
}
