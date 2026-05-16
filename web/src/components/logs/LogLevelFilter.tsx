// Multi-select log-level filter — clicking a level chip toggles its inclusion
// in the visible set. The "any" pseudo-chip resets the filter to "show all".

import { cn } from '@/lib/utils';

import type { LogLevel } from './types';
import { LOG_LEVELS_ORDER } from './types';

interface Props {
  active: ReadonlySet<LogLevel>;
  /** Lines without a parsed level — controlled by a separate "untyped" pill. */
  includeUntyped: boolean;
  onToggleLevel: (l: LogLevel) => void;
  onToggleUntyped: () => void;
  onReset: () => void;
}

const TONE: Record<LogLevel, { idle: string; active: string }> = {
  error: {
    idle: 'border-border-subtle text-meta-foreground hover:text-destructive',
    active: 'border-destructive/40 bg-destructive/10 text-destructive',
  },
  warn: {
    idle: 'border-border-subtle text-meta-foreground hover:text-warning',
    active: 'border-warning/40 bg-warning/10 text-warning',
  },
  info: {
    idle: 'border-border-subtle text-meta-foreground hover:text-accent',
    active: 'border-accent/40 bg-accent/10 text-accent',
  },
  debug: {
    idle: 'border-border-subtle text-meta-foreground hover:text-foreground',
    active: 'border-border-strong bg-card text-foreground',
  },
  trace: {
    idle: 'border-border-subtle text-meta-foreground hover:text-foreground',
    active: 'border-border-strong bg-card text-foreground',
  },
};

export function LogLevelFilter({
  active,
  includeUntyped,
  onToggleLevel,
  onToggleUntyped,
  onReset,
}: Props) {
  const anyOn = active.size === 0 && includeUntyped;

  return (
    <div className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.08em]">
      <button
        type="button"
        onClick={onReset}
        aria-pressed={anyOn}
        title="Show all levels"
        className={cn(
          'rounded-sm border px-1.5 py-[3px] transition-colors',
          anyOn
            ? 'border-foreground/30 bg-foreground/5 text-foreground'
            : 'border-border-subtle text-meta-foreground hover:bg-secondary hover:text-foreground',
        )}
      >
        any
      </button>
      {LOG_LEVELS_ORDER.map((level) => {
        const isActive = active.has(level);
        const tone = TONE[level];
        return (
          <button
            key={level}
            type="button"
            onClick={() => onToggleLevel(level)}
            aria-pressed={isActive}
            title={`Toggle ${level} lines`}
            className={cn(
              'rounded-sm border px-1.5 py-[3px] transition-colors',
              isActive ? tone.active : tone.idle,
            )}
          >
            {level}
          </button>
        );
      })}
      <button
        type="button"
        onClick={onToggleUntyped}
        aria-pressed={includeUntyped}
        title="Toggle lines with no detected level"
        className={cn(
          'rounded-sm border px-1.5 py-[3px] transition-colors',
          includeUntyped
            ? 'border-border-strong bg-card text-foreground'
            : 'border-border-subtle text-meta-foreground hover:bg-secondary hover:text-foreground',
        )}
      >
        plain
      </button>
    </div>
  );
}
