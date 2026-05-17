// Follow toggle + jump-to-bottom affordance.
//
// When the operator scrolls up to read, the panel switches the follow toggle
// off automatically and surfaces a "Jump to bottom" pill so they can return
// without reaching for the scrollbar.

import { ArrowDown } from 'lucide-react';

import { cn } from '@/lib/utils';

interface Props {
  /** Whether new lines auto-scroll into view. */
  following: boolean;
  /** Lines that arrived while the user was scrolled away. */
  unreadCount: number;
  onJumpBottom: () => void;
  onToggleFollow: () => void;
  className?: string;
}

export function LogFollowControl({
  following,
  unreadCount,
  onJumpBottom,
  onToggleFollow,
  className,
}: Props) {
  if (!following) {
    return (
      <button
        type="button"
        onClick={onJumpBottom}
        className={cn(
          'inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 font-mono text-[10px] text-accent transition-colors hover:bg-accent/20',
          className,
        )}
        title="Jump to bottom (G)"
        aria-label={
          unreadCount > 0
            ? `Jump to bottom, ${unreadCount} new line${unreadCount === 1 ? '' : 's'}`
            : 'Jump to bottom'
        }
      >
        <ArrowDown className="size-3" />
        {unreadCount > 0 ? (
          <span>
            <span className="tabular-nums">{unreadCount.toLocaleString()}</span>{' '}
            <span className="uppercase tracking-[0.08em]">new</span>
          </span>
        ) : (
          <span className="uppercase tracking-[0.08em]">jump</span>
        )}
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onToggleFollow}
      aria-pressed={following}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border border-border-subtle bg-card px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-meta-foreground transition-colors hover:bg-secondary hover:text-foreground',
        className,
      )}
      title="Auto-follow on / off"
    >
      <span className="size-1.5 animate-pulse rounded-full bg-accent" />
      follow
    </button>
  );
}
