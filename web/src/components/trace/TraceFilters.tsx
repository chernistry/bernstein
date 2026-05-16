// Filter chip row + free-text search input for the Trace tab.
//
// Kept dumb on purpose: this component owns no state; the parent panel passes
// the active selection and reset handler. That makes the filter logic testable
// in isolation and keeps the panel composition obvious.

import { useCallback } from 'react';

import { cn } from '@/lib/utils';

import { kindLabel } from './format';
import type { TraceKind } from './types';
import { TRACE_KIND_ORDER } from './types';

interface Props {
  /** All kinds that exist in the current trace (drives chip visibility). */
  availableKinds: TraceKind[];
  /** Subset of `availableKinds` the user has activated. Empty = show all. */
  activeKinds: Set<string>;
  query: string;
  onToggleKind: (kind: string) => void;
  onResetKinds: () => void;
  onQueryChange: (value: string) => void;
}

export function TraceFilters({
  availableKinds,
  activeKinds,
  query,
  onToggleKind,
  onResetKinds,
  onQueryChange,
}: Props) {
  // Stable ordering: the canonical TUI vocabulary first, then any new kinds
  // the backend emits sorted lexically. Deduplicated so multiple traces
  // contributing the same kind only show one chip.
  const orderedKinds = orderKinds(availableKinds);

  const handleQueryInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => onQueryChange(e.target.value),
    [onQueryChange],
  );

  const handleClearQuery = useCallback(() => onQueryChange(''), [onQueryChange]);

  return (
    <div className="flex flex-col gap-2 border-b border-border-subtle px-3 py-2">
      <div className="flex items-center gap-2">
        <label className="sr-only" htmlFor="trace-search">
          Search trace events
        </label>
        <div className="relative flex-1">
          <input
            id="trace-search"
            type="search"
            value={query}
            onChange={handleQueryInput}
            placeholder="Search summary, actor, payload…"
            className={cn(
              'h-7 w-full rounded-md border border-border-subtle bg-card px-2 pr-7 font-mono text-[11.5px]',
              'placeholder:text-meta-foreground',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
            )}
          />
          {query && (
            <button
              type="button"
              onClick={handleClearQuery}
              aria-label="Clear search"
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded-sm px-1 font-mono text-[11px] text-muted-foreground hover:text-foreground"
            >
              ×
            </button>
          )}
        </div>
        {activeKinds.size > 0 && (
          <button
            type="button"
            onClick={onResetKinds}
            className="rounded-sm border border-border-subtle bg-card px-2 py-px font-mono text-[10.5px] text-muted-foreground hover:bg-surface-raised hover:text-foreground"
          >
            Reset filters
          </button>
        )}
      </div>
      {orderedKinds.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by event kind">
          {orderedKinds.map((kind) => {
            const active = activeKinds.has(kind);
            return (
              <button
                key={kind}
                type="button"
                onClick={() => onToggleKind(kind)}
                aria-pressed={active}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border px-2 py-px font-mono text-[10.5px] uppercase tracking-[0.1em]',
                  active
                    ? 'border-accent bg-accent/15 text-accent'
                    : 'border-border-subtle bg-card text-muted-foreground hover:bg-surface-raised',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                )}
              >
                {kindLabel(kind)}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function orderKinds(kinds: TraceKind[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const canonical of TRACE_KIND_ORDER) {
    if (kinds.includes(canonical) && !seen.has(canonical)) {
      seen.add(canonical);
      ordered.push(canonical);
    }
  }
  const extras = kinds.filter((k) => !seen.has(String(k))).map(String);
  extras.sort();
  return [...ordered, ...extras];
}
