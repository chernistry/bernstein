// Trace tab - a real timeline of orchestration events for the selected task.
//
// Reads the per-task JSONL trace via /dashboard/tasks/{id}/trace, flattens it
// into individual `TraceTimelineEvent` records on the server, and renders them
// as a vertical timeline with kind-chip filters and free-text search.
//
// Polling is driven by the `useTaskTrace` hook - it keeps refetching every
// ~7s while any captured trace still has a null `end_ts`. Once every trace
// is terminal we stop polling and the live indicator disappears.

import { useEffect, useMemo, useState } from 'react';

import { ErrorState, LoadingState, Pill, StatusDot } from '@/lib/states';
import { cn } from '@/lib/utils';

import { formatSpanSeconds } from './format';
import { TraceEventCard } from './TraceEventCard';
import { TraceFilters } from './TraceFilters';
import type { TraceKind, TraceTimelineEvent } from './types';
import { useTaskTrace } from './useTaskTrace';

export interface TaskTracePanelProps {
  taskId: string;
  /** Parent passes `false` when the Trace tab isn't visible so we stop polling. */
  active?: boolean;
}

export function TaskTracePanel({ taskId, active = true }: TaskTracePanelProps) {
  const trace = useTaskTrace(taskId, active);
  const [query, setQuery] = useState('');
  const [activeKinds, setActiveKinds] = useState<Set<string>>(() => new Set());

  // Live clock for relative-time labels - ticks every second while polling is
  // active. Stopped once the trace is closed so we don't burn cycles on a
  // background tab waiting forever.
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    if (!trace.hasOpenTrace && trace.phase === 'ready') return;
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => {
      window.clearInterval(id);
    };
  }, [active, trace.hasOpenTrace, trace.phase]);

  const availableKinds = useMemo<TraceKind[]>(() => {
    const set = new Set<string>();
    for (const ev of trace.events) set.add(String(ev.kind));
    return Array.from(set);
  }, [trace.events]);

  const filteredEvents = useMemo(() => filterEvents(trace.events, activeKinds, query), [
    trace.events,
    activeKinds,
    query,
  ]);

  const totalDuration = useMemo(
    () => formatSpanSeconds(trace.firstTs, trace.lastTs),
    [trace.firstTs, trace.lastTs],
  );

  const toggleKind = (kind: string) => {
    setActiveKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const resetKinds = () => setActiveKinds(new Set());

  // ── Empty / loading / error states ─────────────────────────────────────────
  if (trace.phase === 'idle' || (trace.phase === 'loading' && trace.events.length === 0)) {
    return (
      <div className="rounded-md border border-border-subtle bg-card p-4">
        <LoadingState label="Loading trace" rows={4} />
      </div>
    );
  }
  if (trace.phase === 'error' && trace.events.length === 0) {
    return (
      <ErrorState
        title="Couldn't load trace"
        message={trace.error ?? 'Unknown error fetching the trace events.'}
        retry={trace.refresh}
      />
    );
  }

  const showLive = trace.hasOpenTrace;
  const isStreaming = showLive;
  const empty = trace.events.length === 0;

  return (
    <div
      className={cn(
        'relative flex h-[min(60vh,540px)] min-h-[300px] flex-col overflow-hidden rounded-md border border-border-subtle bg-card',
      )}
      role={isStreaming ? 'log' : undefined}
      aria-live={isStreaming ? 'polite' : undefined}
      aria-busy={trace.phase === 'loading' ? true : undefined}
    >
      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
            Trace
          </span>
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
            {trace.events.length} {trace.events.length === 1 ? 'event' : 'events'}
            {filteredEvents.length !== trace.events.length && (
              <span className="text-foreground"> · {filteredEvents.length} shown</span>
            )}
          </span>
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
            · {totalDuration}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {trace.phase === 'error' && (
            <button
              type="button"
              onClick={trace.refresh}
              className="rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-px font-mono text-[10px] text-destructive hover:bg-destructive/20"
            >
              retry
            </button>
          )}
          {showLive ? (
            <Pill kind="success" className="gap-1.5">
              <StatusDot kind="running" />
              live
            </Pill>
          ) : (
            <Pill kind="ghost">closed</Pill>
          )}
        </div>
      </div>

      {/* ── Filters ───────────────────────────────────────────────────────── */}
      <TraceFilters
        availableKinds={availableKinds}
        activeKinds={activeKinds}
        query={query}
        onToggleKind={toggleKind}
        onResetKinds={resetKinds}
        onQueryChange={setQuery}
      />

      {/* ── Timeline ──────────────────────────────────────────────────────── */}
      <div className="relative flex-1 overflow-auto px-3 py-3">
        {empty ? (
          <div className="rounded-md border border-dashed border-border-subtle bg-surface-raised/40 px-4 py-6 text-center text-[12.5px] text-muted-foreground">
            No trace events recorded yet.
          </div>
        ) : filteredEvents.length === 0 ? (
          <div className="rounded-md border border-dashed border-border-subtle bg-surface-raised/40 px-4 py-6 text-center text-[12.5px] text-muted-foreground">
            No events match the current filters.
          </div>
        ) : (
          // Left rail visualised by a 1px column behind the cards - the dots
          // come from each card's absolutely-positioned ring.
          <div className="relative">
            <div
              aria-hidden="true"
              className="absolute bottom-0 left-[91px] top-0 w-px bg-border-subtle"
            />
            <div className="flex flex-col gap-2">
              {filteredEvents.map((ev) => (
                <TraceEventCard key={ev.id} event={ev} query={query} now={now} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function filterEvents(
  events: TraceTimelineEvent[],
  activeKinds: Set<string>,
  query: string,
): TraceTimelineEvent[] {
  const q = query.trim().toLowerCase();
  const kindFilter = activeKinds.size > 0;
  if (!kindFilter && !q) return events;
  return events.filter((ev) => {
    if (kindFilter && !activeKinds.has(String(ev.kind))) return false;
    if (!q) return true;
    if (ev.summary.toLowerCase().includes(q)) return true;
    if (ev.actor.toLowerCase().includes(q)) return true;
    if (String(ev.kind).toLowerCase().includes(q)) return true;
    // Stringify the payload for opportunistic text search.
    try {
      return JSON.stringify(ev.payload).toLowerCase().includes(q);
    } catch {
      return false;
    }
  });
}
