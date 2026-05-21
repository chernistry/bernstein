// Multi-select bucket filter chips. Each chip toggles a bucket in/out of the
// active set; an empty set means "show all" - that's the implicit default.

import { cn } from '@/lib/utils';

import { BUCKET_LABEL } from './buckets';
import type { GateBucket } from './types';

interface Props {
  active: Set<GateBucket>;
  counts: Record<GateBucket, number>;
  onToggle: (bucket: GateBucket) => void;
  onReset: () => void;
  sortDir: 'asc' | 'desc';
  onToggleSortDir: () => void;
  className?: string;
}

interface ChipSpec {
  bucket: GateBucket;
  // Per-bucket on/off classes - kept inline so each chip reads at a glance
  // without trawling another lookup table.
  onCls: string;
  offCls: string;
}

const CHIPS: ChipSpec[] = [
  {
    bucket: 'failing',
    onCls: 'bg-destructive/15 text-destructive border-destructive/40',
    offCls: 'border-border-subtle text-muted-foreground hover:text-destructive hover:border-destructive/30',
  },
  {
    bucket: 'pending',
    onCls: 'bg-warning/15 text-warning border-warning/40',
    offCls: 'border-border-subtle text-muted-foreground hover:text-warning hover:border-warning/30',
  },
  {
    bucket: 'passing',
    onCls: 'bg-success/15 text-success border-success/40',
    offCls: 'border-border-subtle text-muted-foreground hover:text-success hover:border-success/30',
  },
  {
    bucket: 'skipped',
    onCls: 'bg-secondary text-foreground border-border-subtle',
    offCls: 'border-border-subtle text-muted-foreground hover:text-foreground',
  },
];

export function GateFilters({
  active,
  counts,
  onToggle,
  onReset,
  sortDir,
  onToggleSortDir,
  className,
}: Props) {
  const anyActive = active.size > 0;
  return (
    <div
      className={cn('flex flex-wrap items-center gap-1.5', className)}
      role="group"
      aria-label="Filter gates by status"
    >
      {CHIPS.map((spec) => {
        const isOn = active.has(spec.bucket);
        const n = counts[spec.bucket];
        return (
          <button
            key={spec.bucket}
            type="button"
            onClick={() => onToggle(spec.bucket)}
            aria-pressed={isOn}
            aria-label={`Toggle ${BUCKET_LABEL[spec.bucket]} filter (${n})`}
            className={cn(
              'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-[0.1em] tabular-nums transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
              isOn ? spec.onCls : spec.offCls,
              n === 0 && 'opacity-50',
            )}
          >
            <span>{BUCKET_LABEL[spec.bucket]}</span>
            <span className="text-[10px] opacity-80">{n}</span>
          </button>
        );
      })}
      {anyActive && (
        <button
          type="button"
          onClick={onReset}
          className={cn(
            'rounded-full border border-border-subtle px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-muted-foreground hover:text-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          )}
          aria-label="Clear status filters"
        >
          clear
        </button>
      )}
      <span className="ml-auto" />
      <button
        type="button"
        onClick={onToggleSortDir}
        className={cn(
          'rounded-full border border-border-subtle px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-muted-foreground hover:text-foreground',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        )}
        aria-label={`Sort order: ${sortDir === 'asc' ? 'failing first' : 'passing first'}`}
        title={sortDir === 'asc' ? 'Failing first' : 'Passing first'}
      >
        sort {sortDir === 'asc' ? '↑' : '↓'}
      </button>
    </div>
  );
}
