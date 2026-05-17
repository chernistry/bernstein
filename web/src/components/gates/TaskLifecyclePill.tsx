// Compact pill rendering the task's lifecycle status as echoed by the gates
// endpoint. Mirrors LogStatusPill's vocabulary so the two panels stay visually
// aligned in the same drawer.

import { cn } from '@/lib/utils';

interface Props {
  status: string | null | undefined;
  className?: string;
}

interface Spec {
  label: string;
  cls: string;
  dot: 'pulse' | 'solid' | 'spin' | 'none';
}

function specFor(status: string | null | undefined): Spec {
  switch (status) {
    case 'running':
    case 'in_progress':
    case 'claimed':
      return { label: status === 'claimed' ? 'claimed' : 'running', cls: 'bg-accent/10 text-accent border-accent/30', dot: 'pulse' };
    case 'open':
    case 'planned':
    case 'queued':
    case 'waiting_for_subtasks':
      return { label: 'queued', cls: 'bg-card text-meta-foreground border-border-subtle', dot: 'spin' };
    case 'pending_approval':
      return { label: 'pending', cls: 'bg-warning/10 text-warning border-warning/30', dot: 'solid' };
    case 'stalled':
    case 'blocked':
    case 'orphaned':
      return { label: status ?? 'stalled', cls: 'bg-warning/10 text-warning border-warning/30', dot: 'solid' };
    case 'done':
    case 'closed':
      return { label: 'done', cls: 'bg-success/10 text-success border-success/30', dot: 'solid' };
    case 'failed':
      return { label: 'failed', cls: 'bg-destructive/10 text-destructive border-destructive/30', dot: 'solid' };
    case 'cancelled':
      return { label: 'cancelled', cls: 'bg-muted text-muted-foreground border-border-subtle', dot: 'solid' };
    default:
      return { label: status ?? 'unknown', cls: 'bg-card text-meta-foreground border-border-subtle', dot: 'none' };
  }
}

export function TaskLifecyclePill({ status, className }: Props) {
  if (!status) return null;
  const spec = specFor(status);
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.12em]',
        spec.cls,
        className,
      )}
      role="status"
      aria-label={`Task ${spec.label}`}
    >
      {spec.dot === 'pulse' && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      {spec.dot === 'solid' && <span className="size-1.5 rounded-full bg-current" />}
      {spec.dot === 'spin' && (
        <span className="size-1.5 animate-spin rounded-full border border-current border-r-transparent" />
      )}
      {spec.label}
    </span>
  );
}
