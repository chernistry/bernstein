// useEventStream - SSE wrapper with auto-reconnect and per-event-type listeners.
import { useEffect, useRef } from 'react';

type EventHandler = (data: unknown) => void;

const DEFAULT_BACKOFF_MS: readonly number[] = [1000, 2000, 5000, 15000];
/**
 * Hard cap on consecutive reconnect attempts before giving up. Prevents
 * infinite spin against a 404/500 endpoint. Surfaced via `onError`.
 */
const DEFAULT_MAX_RETRIES = 8;

export interface UseEventStreamOptions {
  /** Map of event-type → handler. Use 'message' for unnamed default events. */
  on: Record<string, EventHandler>;
  /** Disable when false (e.g. waiting on auth). Default: true. */
  enabled?: boolean;
  /** Backoff schedule in ms; cycles. Defaults to [1s, 2s, 5s, 15s]. */
  backoffMs?: readonly number[];
  /** Max consecutive reconnect attempts before giving up. Default: 8. */
  maxRetries?: number;
  /** Invoked once retries are exhausted; receives the count of attempts made. */
  onError?: (attempts: number) => void;
}

/**
 * Subscribes to an SSE endpoint with auto-reconnect, capped retries, and
 * per-event-type dispatch. Handlers map and onError callback are read via
 * refs, so callers may pass inline objects without forcing reconnects.
 *
 * Reconnect / handler-mount ordering note: `EventSource` does not deliver any
 * events synchronously during construction, so registering listeners on the
 * line after `new EventSource(url)` is race-free - the first event lands on a
 * future task tick after the browser opens the connection.
 */
export function useEventStream(url: string, opts: UseEventStreamOptions): void {
  const enabled = opts.enabled !== false;
  // Stash callable parts in refs so prop identity changes (inline objects /
  // arrow functions) don't tear down the connection.
  const handlersRef = useRef(opts.on);
  handlersRef.current = opts.on;
  const onErrorRef = useRef(opts.onError);
  onErrorRef.current = opts.onError;
  const backoffRef = useRef<readonly number[]>(opts.backoffMs ?? DEFAULT_BACKOFF_MS);
  backoffRef.current = opts.backoffMs ?? DEFAULT_BACKOFF_MS;
  const maxRetriesRef = useRef<number>(opts.maxRetries ?? DEFAULT_MAX_RETRIES);
  maxRetriesRef.current = opts.maxRetries ?? DEFAULT_MAX_RETRIES;

  useEffect(() => {
    if (!enabled || !url) return;
    let retry = 0;
    let es: EventSource | null = null;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let givenUp = false;

    const connect = () => {
      if (cancelled || givenUp) return;
      es = new EventSource(url);

      const dispatch = (type: string) => (e: MessageEvent) => {
        // Only treat a *successful* dispatch as a healthy connection; reset
        // retry count so a later transient error gets the full backoff again.
        retry = 0;
        const handler = handlersRef.current[type];
        if (!handler) return;
        try {
          handler(JSON.parse(e.data));
        } catch {
          handler(e.data);
        }
      };

      es.onmessage = dispatch('message');
      // Snapshot handler keys at connect time. Adding a new key after mount
      // requires the caller to remount (changing url or enabled), which is the
      // intended escape hatch - `EventSource.addEventListener` is sticky.
      for (const t of Object.keys(handlersRef.current)) {
        if (t !== 'message') es.addEventListener(t, dispatch(t));
      }

      es.onerror = () => {
        es?.close();
        es = null;
        if (cancelled) return;
        const cap = maxRetriesRef.current;
        if (retry >= cap) {
          givenUp = true;
          // Notify caller after the current microtask so the hook can return
          // before any user-supplied state setters run.
          const attempts = retry;
          queueMicrotask(() => onErrorRef.current?.(attempts));
          return;
        }
        const schedule = backoffRef.current.length > 0 ? backoffRef.current : DEFAULT_BACKOFF_MS;
        const wait = schedule[Math.min(retry, schedule.length - 1)];
        retry += 1;
        timer = setTimeout(connect, wait);
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      es?.close();
    };
    // Intentionally only depend on the two values that should force a fresh
    // connection. backoff/maxRetries/onError/handlers flow through refs above.
  }, [url, enabled]);
}
