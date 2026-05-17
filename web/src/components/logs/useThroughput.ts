// Rolling lines/sec readout — exposes a smoothed throughput value the
// status bar can render without coupling the visualisation to the buffer
// shape used by `useTaskLogStream`.

import { useEffect, useRef, useState } from 'react';

const WINDOW_MS = 10_000;
const SAMPLE_INTERVAL_MS = 500;

/**
 * Returns a smoothed rate (events/second) for a monotonically-increasing
 * counter. The returned value is updated at most every 500ms and reflects
 * activity over the last `WINDOW_MS` milliseconds.
 */
export function useThroughput(totalCount: number): number {
  const [rate, setRate] = useState(0);
  const samplesRef = useRef<Array<{ t: number; v: number }>>([]);

  // Push a sample on every render where the count changed. We intentionally
  // do not use a setInterval because there's no point updating the rate when
  // no new lines are arriving.
  useEffect(() => {
    const now = Date.now();
    const samples = samplesRef.current;
    samples.push({ t: now, v: totalCount });
    // Trim out-of-window samples.
    while (samples.length > 0 && now - samples[0].t > WINDOW_MS) {
      samples.shift();
    }
  }, [totalCount]);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      const samples = samplesRef.current;
      const now = Date.now();
      while (samples.length > 0 && now - samples[0].t > WINDOW_MS) {
        samples.shift();
      }
      if (samples.length < 2) {
        setRate(0);
      } else {
        const first = samples[0];
        const last = samples[samples.length - 1];
        const dt = (last.t - first.t) / 1000;
        const dv = last.v - first.v;
        setRate(dt > 0 ? dv / dt : 0);
      }
    };
    const id = window.setInterval(tick, SAMPLE_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  return rate;
}
