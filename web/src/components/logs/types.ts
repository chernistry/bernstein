// Shared types for the Task Logs panel.
//
// Backend SSE event vocabulary lives in
// `src/bernstein/core/routes/task_detail.py`:
//
//   event: log       - data is one line of agent stdout/stderr (plain text).
//   event: ping      - keepalive while idle (data is JSON `{ ts }`).
//   event: complete  - terminal status (data is JSON `{ status: done|failed|cancelled }`).
//   event: close     - server closed after `_MAX_IDLE_TICKS` of no new data.
//
// Everything below is the front-end's normalised representation.

export type LogLevel = 'error' | 'warn' | 'info' | 'debug' | 'trace';

/** A single line in the buffer, decorated with parsed metadata. */
export interface LogLine {
  /** Stable identity for keyed rendering + selection. Monotonic per stream. */
  id: number;
  /** Server wall-clock millis when the line landed in the browser. */
  receivedAt: number;
  /** Verbatim line as the backend emitted it (may contain ANSI escapes). */
  raw: string;
  /** Line content with ANSI escapes stripped (used for matching + display). */
  plain: string;
  /** Parsed level if the line begins with a recognised prefix. */
  level: LogLevel | null;
  /** Parsed timestamp (ms epoch) if the line begins with one. */
  timestamp: number | null;
  /** Whether the line is recognised as the start of a stack trace block. */
  stackTrace: boolean;
}

export type LogPhase =
  | 'connecting' // EventSource is opening
  | 'live' // Connection is alive and we have content (or pings)
  | 'paused' // User paused the feed; new lines are buffered out-of-view
  | 'reconnecting' // Transient disconnect, backoff in flight
  | 'complete' // Server emitted `complete` or `close`
  | 'failed'; // Retries exhausted

/** Terminal status reported by the `complete` event. */
export type CompleteStatus = 'done' | 'failed' | 'cancelled' | null;

/** Throughput sample used to drive the rolling lines/sec readout. */
export interface ThroughputSample {
  bucketStartMs: number;
  count: number;
}

export interface SearchMatch {
  lineId: number;
  start: number;
  end: number;
}

export interface SearchState {
  query: string;
  regex: boolean;
  caseSensitive: boolean;
  matches: SearchMatch[];
  /** Index into `matches` of the currently-focused hit. */
  activeIndex: number;
}

export const LOG_LEVELS_ORDER: readonly LogLevel[] = [
  'error',
  'warn',
  'info',
  'debug',
  'trace',
] as const;

/** Hard cap on the live buffer - older lines are evicted FIFO. */
export const LOG_BUFFER_CAP = 10_000;

/** Max number of lines to virtualise-render at a time (overscan). */
export const LOG_VIRTUAL_OVERSCAN = 8;

/** Approx pixel height of a single rendered log line at our font size. */
export const LOG_LINE_HEIGHT = 18;
