// Top-level orchestrator for the Gates tab inside the task drawer.
//
// Fetches ``/tasks/{id}/gates`` on a 6s poll while the task is live, renders
// per-gate rows sorted failing-first, and exposes status-bucket chips +
// sort-direction toggle. The visual language deliberately mirrors the Logs
// panel - same drawer height envelope, same pill vocabulary, same monospaced
// metadata strip - so operators don't context-switch when flipping tabs.

import { ScrollText, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ErrorState } from '@/lib/states';
import { cn } from '@/lib/utils';

import { compareResults, filterByBuckets, tallyBuckets } from './buckets';
import { GateCountsHeader } from './GateCountsHeader';
import { GateFilters } from './GateFilters';
import { GateRow } from './GateRow';
import { TaskLifecyclePill } from './TaskLifecyclePill';
import { formatAbsolute, formatRelative } from './time';
import type { GateBucket, GateResult } from './types';
import { useTaskGates } from './useTaskGates';

export interface TaskGatesPanelProps {
  taskId: string;
  /** Allows the parent to suspend polling when the Gates tab isn't active. */
  active?: boolean;
  className?: string;
}

const PREF_KEY = 'bernstein.gates.prefs.v1';

interface Prefs {
  sortDir: 'asc' | 'desc';
}

function loadPrefs(): Prefs {
  if (typeof window === 'undefined') return { sortDir: 'asc' };
  try {
    const raw = window.localStorage.getItem(PREF_KEY);
    if (!raw) return { sortDir: 'asc' };
    const parsed = JSON.parse(raw) as Partial<Prefs>;
    return { sortDir: parsed.sortDir === 'desc' ? 'desc' : 'asc' };
  } catch {
    return { sortDir: 'asc' };
  }
}

function savePrefs(p: Prefs): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PREF_KEY, JSON.stringify(p));
  } catch {
    /* private-mode localStorage - ignore */
  }
}

export function TaskGatesPanel({ taskId, active = true, className }: TaskGatesPanelProps) {
  const { report, initialLoading, isRefetching, isMissing, error, refetch } = useTaskGates({
    taskId,
    enabled: active,
  });

  const [activeBuckets, setActiveBuckets] = useState<Set<GateBucket>>(() => new Set());
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());
  // ``relativeNow`` ticks every 30s so "3m ago" stays honest without forcing
  // a full refetch. We keep it as state so the header re-renders predictably.
  const [relativeNow, setRelativeNow] = useState(() => Date.now());
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => setRelativeNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, [active]);

  // Reset row-expansion state when the task changes - gate names are scoped
  // per task and stale entries would otherwise leak across drawers.
  useEffect(() => {
    setExpanded(new Set());
    setActiveBuckets(new Set());
  }, [taskId]);

  // Auto-expand failing gates the first time a report lands so the operator
  // never has to click through to see why a task is red. We only do this once
  // per task to avoid fighting manual collapses on subsequent polls.
  const autoExpandedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!report || autoExpandedRef.current === taskId) return;
    const failing = report.results.filter((r) => r.blocked || r.status === 'fail' || r.status === 'timeout');
    if (failing.length === 0) {
      autoExpandedRef.current = taskId;
      return;
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const r of failing) next.add(r.name);
      return next;
    });
    autoExpandedRef.current = taskId;
  }, [report, taskId]);

  const persistPrefs = useCallback((next: Prefs) => {
    setPrefs(next);
    savePrefs(next);
  }, []);

  const toggleBucket = useCallback((bucket: GateBucket) => {
    setActiveBuckets((prev) => {
      const next = new Set(prev);
      if (next.has(bucket)) next.delete(bucket);
      else next.add(bucket);
      return next;
    });
  }, []);

  const resetBuckets = useCallback(() => setActiveBuckets(new Set()), []);

  const toggleRow = useCallback((name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const toggleSortDir = useCallback(() => {
    persistPrefs({ sortDir: prefs.sortDir === 'asc' ? 'desc' : 'asc' });
  }, [prefs, persistPrefs]);

  const results = report?.results ?? [];
  const counts = useMemo(() => tallyBuckets(results), [results]);

  const visibleGates: GateResult[] = useMemo(() => {
    const filtered = filterByBuckets(results, activeBuckets);
    const sorted = [...filtered].sort(compareResults);
    return prefs.sortDir === 'asc' ? sorted : sorted.reverse();
  }, [results, activeBuckets, prefs.sortDir]);

  const relativeStamp = formatRelative(report?.generated_at, relativeNow);
  const absoluteStamp = formatAbsolute(report?.generated_at);

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative flex h-[min(60vh,540px)] min-h-[300px] flex-col overflow-hidden rounded-md border border-border-subtle bg-card',
        className,
      )}
      role="region"
      aria-label="Quality gates"
      aria-busy={initialLoading}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
            Gates
          </span>
          <GateCountsHeader counts={counts} />
        </div>
        <div className="flex items-center gap-1.5">
          {report?.generated_at && (
            <span
              className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground tabular-nums"
              title={absoluteStamp ? `Last run ${absoluteStamp}` : undefined}
              aria-label={`Last run ${relativeStamp}`}
            >
              {relativeStamp}
            </span>
          )}
          {isRefetching && !initialLoading && (
            <span
              className="inline-block size-1.5 animate-pulse rounded-full bg-accent"
              aria-label="Refreshing"
            />
          )}
          <TaskLifecyclePill status={report?.task_status} />
        </div>
      </div>
      <div className="border-b border-border-subtle px-3 py-2">
        <GateFilters
          active={activeBuckets}
          counts={counts}
          onToggle={toggleBucket}
          onReset={resetBuckets}
          sortDir={prefs.sortDir}
          onToggleSortDir={toggleSortDir}
        />
      </div>
      <div className="relative flex-1 overflow-auto">
        {initialLoading && <GatesSkeleton />}
        {!initialLoading && error && (
          <ErrorState
            title="Failed to load gates"
            message={error.message || 'The gates endpoint did not respond.'}
            retry={() => {
              void refetch();
            }}
            className="m-3"
          />
        )}
        {!initialLoading && !error && isMissing && (
          <GatesEmptyState variant="no-report" />
        )}
        {!initialLoading && !error && !isMissing && results.length === 0 && (
          <GatesEmptyState variant="empty-results" />
        )}
        {!initialLoading &&
          !error &&
          !isMissing &&
          results.length > 0 &&
          visibleGates.length === 0 && <GatesEmptyState variant="filtered-out" />}
        {visibleGates.length > 0 && (
          <ul className="divide-y divide-border-subtle/40" role="list">
            {visibleGates.map((gate) => (
              <GateRow
                key={gate.name}
                gate={gate}
                expanded={expanded.has(gate.name)}
                onToggle={() => toggleRow(gate.name)}
              />
            ))}
          </ul>
        )}
      </div>
      {report && results.length > 0 && (
        <div className="flex items-center justify-between gap-2 border-t border-border-subtle bg-muted/20 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
          <span>
            {results.length} gate{results.length === 1 ? '' : 's'} ·{' '}
            <span className="tabular-nums">{formatDuration(report.total_duration_ms)}</span> total
            {report.cache_hits > 0 && (
              <>
                {' '}· <span className="tabular-nums">{report.cache_hits}</span> cached
              </>
            )}
          </span>
          <span>
            {report.overall_pass ? (
              <span className="text-success">overall pass</span>
            ) : (
              <span className="text-destructive">overall fail</span>
            )}
          </span>
        </div>
      )}
    </div>
  );
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0ms';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return `${m}m${rem}s`;
}

function GatesSkeleton() {
  // Five rows matches the typical pipeline length (lint / type / tests /
  // security / coverage) so the skeleton sits at the same height as the
  // real list once it arrives.
  return (
    <ul className="divide-y divide-border-subtle/40" aria-hidden="true">
      {Array.from({ length: 5 }).map((_, i) => (
        <li key={i} className="flex items-center gap-2 border-l-2 border-l-border-subtle px-3 py-2.5">
          <div className="size-3.5 animate-pulse rounded-full bg-muted" />
          <div className="flex-1 space-y-1.5">
            <div
              className="h-3 w-32 animate-pulse rounded bg-muted"
              style={{ animationDelay: `${i * 60}ms` }}
            />
            <div
              className="h-2.5 w-56 animate-pulse rounded bg-muted/60"
              style={{ animationDelay: `${i * 60 + 30}ms` }}
            />
          </div>
          <div className="h-2.5 w-10 animate-pulse rounded bg-muted" />
        </li>
      ))}
    </ul>
  );
}

interface EmptyProps {
  variant: 'no-report' | 'empty-results' | 'filtered-out';
}

function GatesEmptyState({ variant }: EmptyProps) {
  const copy = (() => {
    switch (variant) {
      case 'no-report':
        return {
          title: 'No gates have run yet',
          body:
            'Quality gates run after the agent commits changes. Once a verify step finishes, results will appear here automatically.',
          Icon: ShieldCheck,
        };
      case 'empty-results':
        return {
          title: 'No gate results recorded',
          body: 'The orchestrator wrote an empty report - this task finished without executing any gates.',
          Icon: ScrollText,
        };
      case 'filtered-out':
      default:
        return {
          title: 'No gates match the active filters',
          body: 'Clear or adjust the status chips above to see more gates.',
          Icon: ScrollText,
        };
    }
  })();
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center" role="status">
      <div className="flex size-9 items-center justify-center rounded-full border border-border-subtle bg-card text-meta-foreground">
        <copy.Icon className="size-4" aria-hidden="true" />
      </div>
      <div className="text-[13px] font-medium text-foreground">{copy.title}</div>
      <div className="max-w-xs text-[11.5px] leading-relaxed text-muted-foreground">{copy.body}</div>
    </div>
  );
}
