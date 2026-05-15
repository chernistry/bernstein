// useEventStream — SSE wrapper with auto-reconnect and per-event-type listeners.
import { useEffect, useRef } from 'react';

type EventHandler = (data: unknown) => void;

export interface UseEventStreamOptions {
  /** Map of event-type → handler. Use 'message' for unnamed default events. */
  on: Record<string, EventHandler>;
  /** Disable when false (e.g. waiting on auth). Default: true. */
  enabled?: boolean;
  /** Backoff schedule in ms; cycles. */
  backoffMs?: number[];
}

export function useEventStream(url: string, opts: UseEventStreamOptions): void {
  const enabled = opts.enabled !== false;
  const handlersRef = useRef(opts.on);
  handlersRef.current = opts.on;

  useEffect(() => {
    if (!enabled) return;
    const backoff = opts.backoffMs ?? [1000, 2000, 5000, 15000];
    let retry = 0;
    let es: EventSource | null = null;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      es = new EventSource(url);

      const dispatch = (type: string) => (e: MessageEvent) => {
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
      for (const t of Object.keys(handlersRef.current)) {
        if (t !== 'message') es.addEventListener(t, dispatch(t));
      }

      es.onerror = () => {
        es?.close();
        es = null;
        if (cancelled) return;
        const wait = backoff[Math.min(retry, backoff.length - 1)];
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
  }, [url, enabled, opts.backoffMs]);
}
