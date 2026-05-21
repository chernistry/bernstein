// Display formatters - all output is meant to be wrapped in font-mono tabular-nums.

/**
 * Format a USD amount.
 *
 * Negatives render as `-$12.34` (accounting style) instead of `$-12.34`
 * so columns stay aligned and read correctly to the user.
 *
 * Returns `'-'` for null/undefined/NaN/±Infinity.
 */
export function formatUSD(n: number | null | undefined, opts: { decimals?: number } = {}): string {
  if (n == null || !Number.isFinite(n)) return '-';
  const d = opts.decimals ?? (Math.abs(n) >= 100 ? 0 : 2);
  const abs = Math.abs(n).toFixed(d);
  return n < 0 ? `-$${abs}` : `$${abs}`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '-';
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs < 1000) return String(n);
  if (abs < 1_000_000) return `${sign}${(abs / 1000).toFixed(1)}k`;
  return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
}

export function formatCount(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '-';
  return n.toLocaleString('en-US');
}

/**
 * Compact duration display: 04:12, 1:32:18, 12s, 350ms.
 *
 * `formatDuration(0)` returns `'0s'` - zero duration is a meaningful display
 * value (just-completed task, queued step), not an "unknown" placeholder.
 *
 * Pass milliseconds OR (number, unit) where unit is 's'.
 */
export function formatDuration(ms: number | null | undefined, fromUnit: 'ms' | 's' = 'ms'): string {
  if (ms == null || !Number.isFinite(ms)) return '-';
  const totalMs = fromUnit === 's' ? ms * 1000 : ms;
  // Negatives are nonsensical for "elapsed" - clamp to 0s rather than display "-300ms".
  if (totalMs <= 0) return '0s';
  if (totalMs < 1000) return `${Math.round(totalMs)}ms`;
  const totalSec = Math.floor(totalMs / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  if (m > 0) return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${s}s`;
}

/**
 * Render an ISO timestamp as a relative phrase.
 * - Future dates clamp to `'just now'` so a slightly skewed clock doesn't
 *   render as `'-3s ago'`.
 */
export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '-';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '-';
  const deltaSec = Math.floor((now.getTime() - t) / 1000);
  if (deltaSec < 0) return 'just now';
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86_400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86_400)}d ago`;
}

/** Truncate a hash or path for display: a1b2c3d4… */
export function truncateHash(s: string | null | undefined, head = 7): string {
  if (!s) return '-';
  if (head <= 0) return '…';
  if (s.length <= head + 1) return s;
  return `${s.slice(0, head)}…`;
}
