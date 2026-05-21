// Time formatting helpers private to the Gates panel. Kept here so the panel
// stays self-contained - pulling a global formatter for two callsites isn't
// worth the cross-cutting concern.

export function parseIso(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "5s ago", "2m ago", "3h ago", "Apr 2" - caps at the day boundary. */
export function formatRelative(value: string | null | undefined, now: number = Date.now()): string {
  const d = parseIso(value);
  if (!d) return '-';
  const diffMs = Math.max(0, now - d.getTime());
  const s = Math.round(diffMs / 1000);
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  // Older than a day - show a calendar date in local timezone.
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** Absolute timestamp suitable for a tooltip ("2024-04-02 14:32:18"). */
export function formatAbsolute(value: string | null | undefined): string {
  const d = parseIso(value);
  if (!d) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}
