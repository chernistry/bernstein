// Per-bucket count strip displayed above the gate list. Doubles as a
// status summary so the operator can absorb pass/fail breakdown at a glance
// without scanning the rows.

import { Pill } from '@/lib/states';
import { cn } from '@/lib/utils';

import { BUCKET_VERB } from './buckets';
import type { GateBucket } from './types';

interface Props {
  counts: Record<GateBucket, number>;
  className?: string;
}

interface Entry {
  bucket: GateBucket;
  kind: 'success' | 'danger' | 'warning' | 'default';
  symbol: string;
}

// Visual ordering of the pill strip - failing first to draw the eye, skipped
// last because it's the least actionable bucket.
const ORDER: Entry[] = [
  { bucket: 'failing', kind: 'danger', symbol: '✗' },
  { bucket: 'pending', kind: 'warning', symbol: '⏳' },
  { bucket: 'passing', kind: 'success', symbol: '✓' },
  { bucket: 'skipped', kind: 'default', symbol: '–' },
];

export function GateCountsHeader({ counts, className }: Props) {
  const total = ORDER.reduce((acc, e) => acc + counts[e.bucket], 0);
  // Nothing useful to show when there are zero results - the panel-level
  // empty state takes over instead.
  if (total === 0) return null;
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {ORDER.map((entry) => {
        const n = counts[entry.bucket];
        if (n === 0) return null;
        return (
          <Pill
            key={entry.bucket}
            kind={entry.kind}
            className="px-2 py-0.5"
            aria-label={`${n} gates ${BUCKET_VERB[entry.bucket]}`}
          >
            <span className="text-[10px]" aria-hidden="true">
              {entry.symbol}
            </span>
            <span className="tabular-nums">{n}</span>
            <span className="font-mono uppercase tracking-[0.08em] text-[10px] opacity-80">
              {BUCKET_VERB[entry.bucket]}
            </span>
          </Pill>
        );
      })}
    </div>
  );
}
