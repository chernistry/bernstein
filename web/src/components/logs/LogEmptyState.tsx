// Renders the in-list placeholder when the buffer is empty. The copy varies
// by lifecycle phase so the operator always knows what state we're in.

import { Loader2, Plug, Sparkles } from 'lucide-react';

import { cn } from '@/lib/utils';

import type { LogPhase } from './types';

interface Props {
  phase: LogPhase;
  className?: string;
}

interface Copy {
  title: string;
  body: string;
  icon: 'sparkles' | 'spinner' | 'plug';
  tone: 'muted' | 'destructive' | 'success';
}

function copyFor(phase: LogPhase): Copy {
  switch (phase) {
    case 'failed':
      return {
        title: 'Cannot reach the log stream',
        body: 'The orchestrator did not respond after multiple reconnect attempts.',
        icon: 'plug',
        tone: 'destructive',
      };
    case 'complete':
      return {
        title: 'No log output recorded',
        body: 'This task finished before producing log output (or the agent log file is gone).',
        icon: 'sparkles',
        tone: 'muted',
      };
    case 'paused':
      return {
        title: 'Paused',
        body: 'Resume the feed to see new lines as they arrive.',
        icon: 'spinner',
        tone: 'muted',
      };
    case 'reconnecting':
      return {
        title: 'Reconnecting…',
        body: 'Restoring the log stream after a transient disconnect.',
        icon: 'spinner',
        tone: 'muted',
      };
    case 'connecting':
    case 'live':
    default:
      return {
        title: 'Waiting for log output…',
        body: 'The agent has not written anything yet. New lines will appear here in real time.',
        icon: 'spinner',
        tone: 'muted',
      };
  }
}

export function LogEmptyState({ phase, className }: Props) {
  const copy = copyFor(phase);
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 px-6 py-12 text-center',
        className,
      )}
      role="status"
    >
      <div
        className={cn(
          'flex size-9 items-center justify-center rounded-full border',
          copy.tone === 'destructive' && 'border-destructive/30 bg-destructive/5 text-destructive',
          copy.tone === 'success' && 'border-success/30 bg-success/5 text-success',
          copy.tone === 'muted' && 'border-border-subtle bg-card text-meta-foreground',
        )}
      >
        {copy.icon === 'spinner' && <Loader2 className="size-4 animate-spin" />}
        {copy.icon === 'plug' && <Plug className="size-4" />}
        {copy.icon === 'sparkles' && <Sparkles className="size-4" />}
      </div>
      <div className="text-[13px] font-medium text-foreground">{copy.title}</div>
      <div className="max-w-xs text-[11.5px] leading-relaxed text-muted-foreground">
        {copy.body}
      </div>
    </div>
  );
}
