// Trace-specific formatters. Live alongside the panel so the timeline cards
// and header share a single source of truth for kind labels, colour palettes,
// and the various relative/absolute time renderings.

import type { TraceKind, TraceOutcome } from './types';

const KIND_LABELS: Record<string, string> = {
  trace_meta: 'spawn',
  spawn: 'spawn',
  orient: 'orient',
  plan: 'plan',
  edit: 'edit',
  verify: 'verify',
  complete: 'complete',
  fail: 'fail',
  compact: 'compact',
};

/** Human-friendly label for a kind chip. Unknown kinds render as-is. */
export function kindLabel(kind: TraceKind): string {
  return KIND_LABELS[kind] ?? String(kind);
}

interface KindStyle {
  // Card border + dot colour.
  rail: string;
  // Pill / label colours used inside the card.
  pill: string;
}

const OUTCOME_STYLE: Record<TraceOutcome, KindStyle> = {
  success: {
    rail: 'bg-success ring-success/25',
    pill: 'bg-success/15 text-success border-success/40',
  },
  failed: {
    rail: 'bg-destructive ring-destructive/30',
    pill: 'bg-destructive/15 text-destructive border-destructive/40',
  },
  unknown: {
    rail: 'bg-meta-foreground ring-meta-foreground/30',
    pill: 'bg-surface-raised text-muted-foreground border-border-subtle',
  },
  neutral: {
    rail: 'bg-meta-foreground ring-meta-foreground/30',
    pill: 'bg-surface-raised text-muted-foreground border-border-subtle',
  },
};

const KIND_STYLE: Partial<Record<string, KindStyle>> = {
  trace_meta: {
    rail: 'bg-accent ring-accent/25',
    pill: 'bg-accent/15 text-accent border-accent/40',
  },
  edit: {
    rail: 'bg-warning ring-warning/25',
    pill: 'bg-warning/15 text-warning border-warning/40',
  },
  compact: {
    rail: 'bg-accent ring-accent/25',
    pill: 'bg-accent/15 text-accent border-accent/40',
  },
  fail: {
    rail: 'bg-destructive ring-destructive/30',
    pill: 'bg-destructive/15 text-destructive border-destructive/40',
  },
  complete: {
    rail: 'bg-success ring-success/25',
    pill: 'bg-success/15 text-success border-success/40',
  },
};

/**
 * Resolve the colour palette for a card based on its outcome and kind. Outcome
 * wins for terminal events (complete/fail), kind wins for routine events
 * (edit/compact) - this matches how the TUI renders the same vocabulary.
 */
export function cardStyle(kind: TraceKind, outcome: TraceOutcome): KindStyle {
  if (outcome === 'failed') return OUTCOME_STYLE.failed;
  if (outcome === 'success') return OUTCOME_STYLE.success;
  const byKind = KIND_STYLE[String(kind)];
  if (byKind) return byKind;
  return OUTCOME_STYLE.neutral;
}

/** Render a unix-seconds timestamp as a wall-clock time string. */
export function formatWallClock(ts: number): string {
  if (!Number.isFinite(ts) || ts <= 0) return '-';
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

/** ISO timestamp shown in the hover tooltip - useful when scrubbing logs. */
export function formatIso(ts: number): string {
  if (!Number.isFinite(ts) || ts <= 0) return '-';
  return new Date(ts * 1000).toISOString();
}

/** Compact relative phrase: 3s, 12m, 4h, 2d. Mirrors `lib/format.ts`. */
export function formatRelativeSeconds(ts: number, now: number = Date.now() / 1000): string {
  if (!Number.isFinite(ts) || ts <= 0) return '-';
  const delta = Math.max(0, Math.floor(now - ts));
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86_400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86_400)}d ago`;
}

/** Span between two unix-second timestamps, friendly-printed. */
export function formatSpanSeconds(start: number | null, end: number | null): string {
  if (start == null || end == null) return '-';
  if (!Number.isFinite(start) || !Number.isFinite(end)) return '-';
  const span = Math.max(0, end - start);
  if (span < 1) return '<1s';
  const totalSec = Math.floor(span);
  if (totalSec < 60) return `${totalSec}s`;
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}

/** Pretty-print arbitrary JSON for the expandable payload viewer. */
export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
