// Per-task dependency panel for the Tasks drawer "Deps" tab.
//
// Layout - two flat columns side-by-side:
//   Upstream  (this task depends on …)        Downstream (… depends on this)
//
// Backend source: GET /tasks/{taskId}/graph-neighbors → { upstream, downstream }.
// Each neighbour exposes id/title/status/role.  The panel sorts pills by
// status (running > stalled/blocked > queued > done > failed), then by title,
// so the operator sees the in-flight work first.
//
// Polling: 10s while mounted; halts automatically once neighbours stop
// changing (we still respect React Query's window-focus refetch).  No SSE -
// task_update events already invalidate everything under ["tasks"], which the
// query key participates in via taskId.

import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { apiGet, ApiError } from '@/lib/api';
import { ErrorState, LoadingState, Pill, SectionLabel, StatusDot } from '@/lib/states';
import { cn } from '@/lib/utils';

// ── Domain types ────────────────────────────────────────────────────────────

type UiStatus = 'running' | 'queued' | 'stalled' | 'failed' | 'done';

interface Neighbor {
  id: string;
  title: string | null;
  status: string;
  role: string | null;
}

interface GraphNeighborsResponse {
  task_id: string;
  depth: number;
  upstream: Neighbor[];
  downstream: Neighbor[];
}

// Mirror Tasks.tsx::toUiStatus - kept local so the panel does not depend on
// the screen-level helpers.  Unknown strings fall to 'queued' as the neutral
// fallback.
function toUiStatus(raw: string | null | undefined): UiStatus {
  switch (raw) {
    case 'running':
    case 'in_progress':
    case 'claimed':
      return 'running';
    case 'planned':
    case 'open':
    case 'queued':
    case 'waiting_for_subtasks':
    case 'pending_approval':
      return 'queued';
    case 'stalled':
    case 'blocked':
    case 'orphaned':
      return 'stalled';
    case 'failed':
    case 'cancelled':
      return 'failed';
    case 'done':
    case 'closed':
      return 'done';
    default:
      return 'queued';
  }
}

// Sort order: running first, then stalled, queued, done, failed - what the
// operator wants visible at the top of a chain.
const STATUS_RANK: Record<UiStatus, number> = {
  running: 0,
  stalled: 1,
  queued: 2,
  done: 3,
  failed: 4,
};

function sortNeighbors(list: Neighbor[]): Neighbor[] {
  return [...list].sort((a, b) => {
    const ra = STATUS_RANK[toUiStatus(a.status)];
    const rb = STATUS_RANK[toUiStatus(b.status)];
    if (ra !== rb) return ra - rb;
    return (a.title ?? a.id).localeCompare(b.title ?? b.id);
  });
}

// Terminal statuses skip polling - there is no point refetching neighbours of
// a closed task every 10s.
function isTerminal(raw: string | null | undefined): boolean {
  const s = toUiStatus(raw);
  return s === 'done' || s === 'failed';
}

// ── Props ───────────────────────────────────────────────────────────────────

export interface TaskDepsPanelProps {
  taskId: string;
  /** True when the Deps tab is the active drawer tab. */
  active?: boolean;
  /**
   * Optional callback so a parent can swap the selected task to a neighbour.
   * When omitted, neighbour pills still render but are inert.  Tasks.tsx can
   * wire this independently - keeping it optional avoids touching the parent
   * in this changeset.
   */
  onOpenTask?: (id: string) => void;
  /**
   * Hint about the focal task's status so polling can pause for terminal
   * tasks.  When unknown, we poll regardless.
   */
  focalStatus?: string | null;
  className?: string;
}

// ── Panel ───────────────────────────────────────────────────────────────────

export function TaskDepsPanel({
  taskId,
  active = true,
  onOpenTask,
  focalStatus,
  className,
}: TaskDepsPanelProps) {
  const terminal = isTerminal(focalStatus);
  const refetchInterval = active && !terminal ? 10_000 : false;

  const q = useQuery({
    queryKey: ['tasks', 'graph-neighbors', taskId],
    queryFn: () => apiGet<GraphNeighborsResponse>(
      `/tasks/${encodeURIComponent(taskId)}/graph-neighbors`,
    ),
    enabled: active && !!taskId,
    refetchInterval,
    staleTime: 5_000,
  });

  const upstream = useMemo(() => sortNeighbors(q.data?.upstream ?? []), [q.data]);
  const downstream = useMemo(() => sortNeighbors(q.data?.downstream ?? []), [q.data]);

  const handleOpen = useCallback(
    (id: string) => {
      if (onOpenTask && id) onOpenTask(id);
    },
    [onOpenTask],
  );

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-md border border-border-subtle bg-card/60 p-3',
        className,
      )}
    >
      <Header
        upstreamCount={upstream.length}
        downstreamCount={downstream.length}
        loading={q.isLoading && !q.data}
        polling={refetchInterval !== false}
      />

      {q.isError && !q.data ? (
        <ErrorState
          title="Failed to load deps"
          message={
            q.error instanceof ApiError
              ? q.error.message
              : 'Could not fetch dependency graph for this task.'
          }
          retry={() => q.refetch()}
        />
      ) : q.isLoading && !q.data ? (
        <LoadingPair />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Column
            label="Upstream"
            sub="this depends on"
            items={upstream}
            empty="No upstream dependencies."
            onOpen={onOpenTask ? handleOpen : undefined}
          />
          <Column
            label="Downstream"
            sub="depends on this"
            items={downstream}
            empty="No downstream dependents."
            onOpen={onOpenTask ? handleOpen : undefined}
          />
        </div>
      )}
    </div>
  );
}

// ── Header ──────────────────────────────────────────────────────────────────

function Header({
  upstreamCount,
  downstreamCount,
  loading,
  polling,
}: {
  upstreamCount: number;
  downstreamCount: number;
  loading: boolean;
  polling: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
          Deps
        </span>
        <span className="text-[11.5px] text-muted-foreground">
          <span className="font-mono tabular-nums text-foreground">
            {loading ? '-' : upstreamCount}
          </span>{' '}
          upstream ·{' '}
          <span className="font-mono tabular-nums text-foreground">
            {loading ? '-' : downstreamCount}
          </span>{' '}
          downstream
        </span>
      </div>
      <span
        className={cn(
          'font-mono text-[10px] uppercase tracking-[0.12em]',
          polling ? 'text-meta-foreground' : 'text-meta-foreground/50',
        )}
        title={polling ? 'Auto-refresh every 10s' : 'Auto-refresh paused (terminal task)'}
      >
        {polling ? 'live · 10s' : 'paused'}
      </span>
    </div>
  );
}

// ── Two-column skeleton (loading state) ─────────────────────────────────────

function LoadingPair() {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <div className="rounded-md border border-border-subtle bg-card/40 p-2.5">
        <SectionLabel className="mb-2 !text-[10.5px]">Upstream</SectionLabel>
        <LoadingState rows={3} />
      </div>
      <div className="rounded-md border border-border-subtle bg-card/40 p-2.5">
        <SectionLabel className="mb-2 !text-[10.5px]">Downstream</SectionLabel>
        <LoadingState rows={3} />
      </div>
    </div>
  );
}

// ── Column ──────────────────────────────────────────────────────────────────

function Column({
  label,
  sub,
  items,
  empty,
  onOpen,
}: {
  label: string;
  sub: string;
  items: Neighbor[];
  empty: string;
  onOpen?: (id: string) => void;
}) {
  return (
    <div className="rounded-md border border-border-subtle bg-card/40 p-2.5">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <SectionLabel className="!text-[10.5px]">{label}</SectionLabel>
        <span className="font-mono text-[10px] text-meta-foreground">{sub}</span>
      </div>
      {items.length === 0 ? (
        <div className="rounded-sm border border-dashed border-border-subtle bg-card/30 px-3 py-3 text-center text-[11.5px] text-muted-foreground">
          {empty}
        </div>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {items.map((item) => (
            <li key={item.id}>
              <NeighborPill item={item} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Pill ────────────────────────────────────────────────────────────────────

function NeighborPill({
  item,
  onOpen,
}: {
  item: Neighbor;
  onOpen?: (id: string) => void;
}) {
  const ui = toUiStatus(item.status);
  const interactive = !!onOpen;
  const idPrefix = item.id.length > 8 ? `${item.id.slice(0, 8)}…` : item.id;
  const title = item.title ?? '(unknown task)';

  const inner = (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <StatusDot kind={ui} />
      <span
        className="shrink-0 font-mono text-[11px] tabular-nums text-meta-foreground"
        title={item.id}
      >
        {idPrefix}
      </span>
      <span
        className={cn(
          'min-w-0 flex-1 truncate text-[12.5px]',
          item.title ? 'text-foreground' : 'italic text-muted-foreground',
        )}
        title={title}
      >
        {title}
      </span>
      {item.role && (
        <Pill kind="ghost" className="shrink-0">
          {item.role}
        </Pill>
      )}
    </div>
  );

  const baseClasses =
    'flex w-full items-center gap-2 rounded-sm border border-border-subtle bg-card px-2.5 py-2 text-left transition-colors';

  if (!interactive) {
    return <div className={baseClasses}>{inner}</div>;
  }

  return (
    <button
      type="button"
      onClick={() => onOpen?.(item.id)}
      className={cn(
        baseClasses,
        'cursor-pointer hover:border-accent/40 hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
      )}
      aria-label={`Open task ${item.id}`}
    >
      {inner}
    </button>
  );
}
