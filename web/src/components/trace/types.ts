// Type contract shared between the trace API client and the timeline UI.
//
// Mirrors `TraceTimelineResponse` / `TraceTimelineEvent` from
// `src/bernstein/core/routes/task_trace.py`. Keep this in sync if the backend
// schema evolves.

/** Outcome bucket used for colour coding the timeline cards. */
export type TraceOutcome = 'success' | 'failed' | 'unknown' | 'neutral';

/** Step kind vocabulary mirrored from `bernstein.core.observability.traces`. */
export type TraceKind =
  | 'spawn'
  | 'orient'
  | 'plan'
  | 'edit'
  | 'verify'
  | 'complete'
  | 'fail'
  | 'compact'
  | 'trace_meta'
  // Forward-compat catch-all so a server emitting a new kind doesn't break the FE.
  | (string & {});

export interface TraceTimelineEvent {
  id: string;
  ts: number;
  kind: TraceKind;
  actor: string;
  summary: string;
  outcome: TraceOutcome;
  trace_id: string;
  session_id: string;
  payload: Record<string, unknown>;
}

export interface TraceTimelineResponse {
  task_id: string;
  events: TraceTimelineEvent[];
  total: number;
  cursor: number | null;
  first_ts: number | null;
  last_ts: number | null;
  has_open_trace: boolean;
}

/**
 * Stable ordering for the filter-chip row. Anything not in this list is
 * appended in lexical order so the FE keeps working when the backend emits
 * a new kind we haven't taught the UI about yet.
 */
export const TRACE_KIND_ORDER: TraceKind[] = [
  'trace_meta',
  'spawn',
  'orient',
  'plan',
  'edit',
  'verify',
  'compact',
  'complete',
  'fail',
];

/** How long (ms) we wait between polls while the trace is still open. */
export const TRACE_POLL_INTERVAL_MS = 7_000;

/** Cap on the events we fetch in one page from the backend. */
export const TRACE_PAGE_LIMIT = 500;
