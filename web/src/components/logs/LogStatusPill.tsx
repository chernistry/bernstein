// Connection status indicator shown in the panel header.
// 8 phases mapped to a colour + label + optional pulse dot.

import { cn } from '@/lib/utils';

import type { CompleteStatus, LogPhase } from './types';

interface Props {
  phase: LogPhase;
  completeStatus: CompleteStatus;
  className?: string;
}

interface PillSpec {
  label: string;
  cls: string;
  dot: 'pulse' | 'solid' | 'spin' | 'none';
}

function specFor(phase: LogPhase, completeStatus: CompleteStatus): PillSpec {
  switch (phase) {
    case 'live':
      return {
        label: 'live',
        cls: 'bg-accent/10 text-accent border-accent/30',
        dot: 'pulse',
      };
    case 'connecting':
      return {
        label: 'connecting',
        cls: 'bg-card text-meta-foreground border-border-subtle',
        dot: 'spin',
      };
    case 'reconnecting':
      return {
        label: 'reconnecting',
        cls: 'bg-warning/10 text-warning border-warning/30',
        dot: 'spin',
      };
    case 'paused':
      return {
        label: 'paused',
        cls: 'bg-secondary text-foreground border-border-subtle',
        dot: 'solid',
      };
    case 'failed':
      return {
        label: 'failed',
        cls: 'bg-destructive/10 text-destructive border-destructive/30',
        dot: 'solid',
      };
    case 'complete': {
      if (completeStatus === 'done') {
        return {
          label: 'done',
          cls: 'bg-success/10 text-success border-success/30',
          dot: 'solid',
        };
      }
      if (completeStatus === 'failed') {
        return {
          label: 'failed',
          cls: 'bg-destructive/10 text-destructive border-destructive/30',
          dot: 'solid',
        };
      }
      if (completeStatus === 'cancelled') {
        return {
          label: 'cancelled',
          cls: 'bg-muted text-muted-foreground border-border-subtle',
          dot: 'solid',
        };
      }
      return {
        label: 'complete',
        cls: 'bg-secondary text-foreground border-border-subtle',
        dot: 'solid',
      };
    }
    default:
      return { label: phase, cls: '', dot: 'none' };
  }
}

export function LogStatusPill({ phase, completeStatus, className }: Props) {
  const spec = specFor(phase, completeStatus);
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.12em]',
        spec.cls,
        className,
      )}
      aria-label={`Log stream ${spec.label}`}
      role="status"
      aria-live="polite"
    >
      {spec.dot === 'pulse' && (
        <span className="size-1.5 animate-pulse rounded-full bg-current" />
      )}
      {spec.dot === 'solid' && <span className="size-1.5 rounded-full bg-current" />}
      {spec.dot === 'spin' && (
        <span className="size-1.5 animate-spin rounded-full border border-current border-r-transparent" />
      )}
      {spec.label}
    </span>
  );
}
