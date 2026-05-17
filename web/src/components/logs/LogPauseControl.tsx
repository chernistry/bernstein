// Pause / resume toggle. When paused, incoming lines accrue out-of-view and
// the pill morphs into a "+N new lines" call to action.

import { Pause, Play } from 'lucide-react';

import { cn } from '@/lib/utils';

interface Props {
  paused: boolean;
  pendingCount: number;
  onToggle: () => void;
  onFlush: () => void;
  className?: string;
}

export function LogPauseControl({
  paused,
  pendingCount,
  onToggle,
  onFlush,
  className,
}: Props) {
  if (paused) {
    return (
      <div className={cn('inline-flex items-center gap-1', className)}>
        <button
          type="button"
          onClick={onToggle}
          aria-pressed
          className="inline-flex items-center gap-1 rounded-md border border-warning/40 bg-warning/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-warning"
          title="Resume live tail (Space)"
        >
          <Play className="size-3" />
          paused
        </button>
        {pendingCount > 0 && (
          <button
            type="button"
            onClick={onFlush}
            className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-accent transition-colors hover:bg-accent/20"
            title="Flush buffered lines into the view"
          >
            <span className="tabular-nums normal-case">+{pendingCount.toLocaleString()}</span>{' '}
            new
          </button>
        )}
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={false}
      className={cn(
        'inline-flex items-center gap-1 rounded-md border border-border-subtle bg-card px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-meta-foreground transition-colors hover:bg-secondary hover:text-foreground',
        className,
      )}
      title="Pause live tail (Space)"
    >
      <Pause className="size-3" />
      pause
    </button>
  );
}
