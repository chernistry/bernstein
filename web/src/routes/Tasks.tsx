// Tasks screen — Variant A "Decision-Grade Quiet Command".
// Source of truth: design_handoff_bernstein_phase1/design-source/screens/screen-tasks.jsx
// + README §6.01 (Tasks specs) + §8 (states contract).

import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { MoreHorizontal, Play, Command as CommandIcon, Search, X } from 'lucide-react';
import { apiGet, apiPost, ApiError } from '@/lib/api';
import { useEventStream } from '@/lib/sse';
import { TaskLogsPanel } from '@/components/logs';
import {
  formatUSD,
  formatDuration,
  formatTokens,
  formatRelative,
  formatCount,
} from '@/lib/format';
import { duration, ease } from '@/lib/motion';
import {
  EmptyState,
  LoadingState,
  ErrorState,
  StatusDot,
  Pill,
  SectionLabel,
} from '@/lib/states';
import { cn } from '@/lib/utils';

// ── Domain types ────────────────────────────────────────────────────────────
// UI status vocabulary — the visual states the table/drawer render.
type TaskStatus = 'running' | 'queued' | 'stalled' | 'failed' | 'done';

// Backend status vocabulary (see core/tasks/models.py::TaskStatus).
type BackendStatus =
  | 'planned'
  | 'open'
  | 'claimed'
  | 'in_progress'
  | 'done'
  | 'closed'
  | 'failed'
  | 'blocked'
  | 'waiting_for_subtasks'
  | 'cancelled'
  | 'orphaned'
  | 'pending_approval';

interface TaskRow {
  id: string;
  title: string;
  /** Server-side raw status string. */
  status: BackendStatus | string;
  role: string;
  /** Backend uses ``assigned_agent``; older shape used ``agent``. Either may be present. */
  assigned_agent?: string | null;
  agent?: string | null;
  /** Duration in milliseconds. May be omitted; we derive from claimed_at when needed. */
  duration_ms?: number | null;
  /** 0–100 progress percent. May be missing; show "—" when so. */
  progress?: number | null;
  /** Total tokens consumed so far. */
  tokens?: number | null;
  /** Working git branch. May live on the row or inside ``metadata``. */
  branch?: string | null;
  /** Cost in USD. May live on the row or inside ``metadata.cost_usd``. */
  cost_usd?: number | null;
  /** Free-form metadata bag from the orchestrator. */
  metadata?: Record<string, unknown> | null;
  updated_at?: string | null;
  /** Unix epoch seconds when the task was claimed. */
  claimed_at?: number | null;
  created_at?: number | null;
}

interface TasksListResponse {
  items: TaskRow[];
  total?: number;
  page?: number;
  page_size?: number;
  counts?: Partial<Record<TaskStatus | BackendStatus | 'all' | 'done_24h', number>>;
}

interface PlanStep {
  status: TaskStatus;
  text: string;
}

interface TaskDetail extends TaskRow {
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_cap_usd?: number | null;
  diff_added?: number | null;
  diff_removed?: number | null;
  approvals_total?: number | null;
  approvals_done?: number | null;
  approvals_pending?: number | null;
  plan?: PlanStep[];
}

// Map every backend status onto a UI bucket. Unknown strings fall to 'queued'
// (the safest neutral state).
function toUiStatus(s: string | null | undefined): TaskStatus {
  switch (s) {
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

// Cost may live on the row or inside ``metadata.cost_usd`` depending on
// orchestrator version. Coerce strings/numbers, ignore garbage.
function readCostUsd(row: TaskRow): number | null {
  if (typeof row.cost_usd === 'number' && Number.isFinite(row.cost_usd)) return row.cost_usd;
  const md = row.metadata;
  if (md && typeof md === 'object') {
    const v = (md as Record<string, unknown>).cost_usd;
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    if (typeof v === 'string') {
      const n = Number.parseFloat(v);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

function readBranch(row: TaskRow): string | null {
  if (typeof row.branch === 'string' && row.branch) return row.branch;
  const md = row.metadata;
  if (md && typeof md === 'object') {
    const v = (md as Record<string, unknown>).branch;
    if (typeof v === 'string' && v) return v;
  }
  return null;
}

function readAgent(row: TaskRow): string | null {
  if (typeof row.agent === 'string' && row.agent) return row.agent;
  if (typeof row.assigned_agent === 'string' && row.assigned_agent) return row.assigned_agent;
  return null;
}

function readTokens(row: TaskRow): number | null {
  if (typeof row.tokens === 'number' && Number.isFinite(row.tokens)) return row.tokens;
  const md = row.metadata;
  if (md && typeof md === 'object') {
    const v = (md as Record<string, unknown>).tokens;
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  return null;
}

function readDurationMs(row: TaskRow): number | null {
  if (typeof row.duration_ms === 'number' && Number.isFinite(row.duration_ms)) return row.duration_ms;
  const claimed = row.claimed_at;
  if (typeof claimed === 'number' && Number.isFinite(claimed)) {
    const nowMs = Date.now();
    const claimedMs = claimed * 1000;
    const delta = nowMs - claimedMs;
    return delta >= 0 ? delta : null;
  }
  return null;
}

// ── Filter chips ────────────────────────────────────────────────────────────

type ChipKey = 'all' | 'running' | 'queued' | 'stalled' | 'done_24h' | 'failed';

// statusParam goes straight to the backend ``?status=`` filter. The closest
// backend status to "running" is ``in_progress`` (Tasks the orchestrator has
// actively claimed); ``open`` is the queue.
//
// NB: the original ``Done · 24h`` label promised a 24h time-window filter
// that the server doesn't expose. Dropping the suffix to keep the chip
// honest until the time-window endpoint lands.
const CHIPS: { key: ChipKey; label: string; statusParam?: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'running', label: 'Running', statusParam: 'in_progress' },
  { key: 'queued', label: 'Queued', statusParam: 'open' },
  { key: 'stalled', label: 'Stalled', statusParam: 'blocked' },
  { key: 'done_24h', label: 'Done', statusParam: 'done' },
  { key: 'failed', label: 'Failed', statusParam: 'failed' },
];

// ── Detail tabs ─────────────────────────────────────────────────────────────

const DETAIL_TABS = ['Summary', 'Diff', 'Gates', 'Logs', 'Deps', 'Trace'] as const;
type DetailTab = (typeof DETAIL_TABS)[number];

// ── Operator-syntax token highlighter ───────────────────────────────────────
// Highlights `agent:`, `status:`, `role:` keys in the `accent` colour.
// Pure presentation — does NOT mutate the input.

const TOKEN_RE = /(agent:|status:|role:)/gi;

function HighlightedQuery({ value }: { value: string }) {
  if (!value) {
    // Subtle ghost hint of the syntax — purely visual, the input itself is empty.
    // Parent already renders the literal ``filter:`` label, so do not duplicate it here.
    return (
      <span className="text-meta-foreground/60">
        agent:claude status:running role:backend
      </span>
    );
  }
  const parts: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(value)) !== null) {
    if (m.index > last) {
      parts.push(
        <span key={`t-${last}`} className="text-foreground">
          {value.slice(last, m.index)}
        </span>,
      );
    }
    parts.push(
      <span key={`k-${m.index}`} className="text-accent">
        {m[0]}
      </span>,
    );
    last = m.index + m[0].length;
  }
  if (last < value.length) {
    parts.push(
      <span key={`t-${last}`} className="text-foreground">
        {value.slice(last)}
      </span>,
    );
  }
  return <>{parts}</>;
}

// ── Search-string parser → filter params ────────────────────────────────────
// Pulls out `agent:` and `role:` tokens; the rest is a free-text contains-match.

interface ParsedQuery {
  agent: string | null;
  role: string | null;
  text: string;
}

function parseQuery(q: string): ParsedQuery {
  let agent: string | null = null;
  let role: string | null = null;
  const free: string[] = [];
  for (const tok of q.split(/\s+/).filter(Boolean)) {
    const lower = tok.toLowerCase();
    if (lower.startsWith('agent:')) {
      // Trailing-colon (`agent:`) is a partial token while typing — keep the
      // existing free-text behaviour off, but do not assign agent='' either.
      const rest = tok.slice('agent:'.length);
      if (rest) agent = rest;
    } else if (lower.startsWith('role:')) {
      const rest = tok.slice('role:'.length);
      if (rest) role = rest;
    } else if (lower.startsWith('status:')) {
      // status is driven by chip selection, not the query bar
      continue;
    } else {
      free.push(tok);
    }
  }
  return { agent, role, text: free.join(' ').trim() };
}

// ── Endpoint helpers ────────────────────────────────────────────────────────

function buildListPath(opts: {
  status?: string;
  agent?: string | null;
  role?: string | null;
  text?: string;
  page: number;
}): string {
  const p = new URLSearchParams();
  if (opts.status) p.set('status', opts.status);
  if (opts.agent) p.set('agent', opts.agent);
  if (opts.role) p.set('role', opts.role);
  if (opts.text) p.set('q', opts.text);
  p.set('page', String(opts.page));
  return `/tasks?${p.toString()}`;
}

// ── Main component ─────────────────────────────────────────────────────────

export default function Tasks() {
  const qc = useQueryClient();

  const [queryStr, setQueryStr] = useState<string>('');
  const [activeChip, setActiveChip] = useState<ChipKey>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [page] = useState<number>(1);
  const [activeTab, setActiveTab] = useState<DetailTab>('Summary');

  const parsed = useMemo(() => parseQuery(queryStr), [queryStr]);
  const chip = CHIPS.find((c) => c.key === activeChip) ?? CHIPS[0];

  const listPath = buildListPath({
    status: chip.statusParam,
    agent: parsed.agent,
    role: parsed.role,
    text: parsed.text,
    page,
  });

  const listQ = useQuery({
    queryKey: ['tasks', 'list', listPath],
    queryFn: async (): Promise<TasksListResponse> => {
      // Backend returns either a wrapped {items, total, ...} OR a raw list
      // (legacy /tasks endpoint shape). Normalize here so the rest of the
      // component can assume the wrapped shape.
      const raw = await apiGet<TasksListResponse | TaskRow[]>(listPath);
      if (Array.isArray(raw)) {
        return { items: raw, total: raw.length, page: 1, page_size: raw.length };
      }
      return raw;
    },
  });

  const detailQ = useQuery({
    queryKey: ['tasks', 'detail', selectedId],
    queryFn: () => apiGet<TaskDetail>(`/tasks/${encodeURIComponent(selectedId ?? '')}`),
    enabled: !!selectedId,
  });

  // Live updates → invalidate the list (and detail if relevant).
  useEventStream('/api/v1/events', {
    on: {
      task_update: (raw) => {
        qc.invalidateQueries({ queryKey: ['tasks'] });
        const id = (raw as { id?: string } | null)?.id;
        if (id && id === selectedId) {
          qc.invalidateQueries({ queryKey: ['tasks', 'detail', id] });
        }
      },
      task_progress: (raw) => {
        qc.invalidateQueries({ queryKey: ['tasks', 'list', listPath] });
        const id = (raw as { id?: string } | null)?.id;
        if (id && id === selectedId) {
          qc.invalidateQueries({ queryKey: ['tasks', 'detail', id] });
        }
      },
    },
  });

  // Mutations (per-task).
  // NB: /cancel requires a JSON body (TaskCancelRequest{reason}); the legacy
  // empty-POST returned 422. Keep ``reason`` short and honest.
  // NB: there is no `/tasks/{id}/retry` or `/tasks/{id}/kill` endpoint —
  // ``force-claim`` is the closest "re-run" semantic (resets the row back to
  // open with priority 0); kill maps to ``/tasks/{id}/cancel`` until a
  // session-level kill lands.
  const cancelMut = useMutation({
    mutationFn: (id: string) =>
      apiPost<unknown>(`/tasks/${encodeURIComponent(id)}/cancel`, { reason: 'cancelled from gui' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
  });
  const rerunMut = useMutation({
    mutationFn: (id: string) =>
      apiPost<unknown>(`/tasks/${encodeURIComponent(id)}/force-claim`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
  });
  const prioritizeMut = useMutation({
    mutationFn: (id: string) =>
      apiPost<unknown>(`/tasks/${encodeURIComponent(id)}/prioritize`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
  });
  const killMut = useMutation({
    mutationFn: (id: string) =>
      apiPost<unknown>(`/tasks/${encodeURIComponent(id)}/cancel`, { reason: 'killed from gui' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
  });

  // Derived
  const items = listQ.data?.items ?? [];
  const counts = listQ.data?.counts ?? {};
  // Counts may be reported under either UI keys (running/stalled) or backend
  // keys (in_progress/blocked). Read both, prefer the explicit one.
  const totalCount = counts.all ?? listQ.data?.total ?? items.length;
  const runningCount =
    counts.running ??
    counts.in_progress ??
    items.filter((t) => toUiStatus(t.status) === 'running').length;
  const stalledCount =
    counts.stalled ??
    counts.blocked ??
    items.filter((t) => toUiStatus(t.status) === 'stalled').length;
  const lastSync = listQ.dataUpdatedAt ? new Date(listQ.dataUpdatedAt).toISOString() : null;

  const selected = items.find((t) => t.id === selectedId) ?? null;

  // Selection lifecycle:
  // (a) auto-select first row when nothing is selected and the list arrives
  // (b) clear selection if the previously-selected row no longer exists
  //     (e.g. SSE invalidation removed it). Otherwise the detail query keeps
  //     thrashing on a 404 and the drawer renders stale fallback data.
  useEffect(() => {
    if (!listQ.data) return;
    if (selectedId === null) {
      if (items.length > 0) setSelectedId(items[0].id);
      return;
    }
    if (!items.some((t) => t.id === selectedId)) {
      setSelectedId(null);
    }
  }, [selectedId, items, listQ.data]);

  const refetchList = () => {
    listQ.refetch();
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[1fr_380px]">
      {/* ── LEFT: query + table ─────────────────────────────────────────── */}
      <section className="flex min-w-0 flex-col overflow-hidden px-[22px] py-[18px]">
        <Header
          totalCount={totalCount}
          runningCount={runningCount}
          stalledCount={stalledCount}
          lastSync={lastSync}
          loading={listQ.isLoading}
        />

        <SearchBar value={queryStr} onChange={setQueryStr} />

        <ChipsRow active={activeChip} counts={counts} onSelect={setActiveChip} />

        <div className="mt-[14px] min-h-0 flex-1 overflow-hidden rounded-md border border-border bg-card">
          {listQ.isLoading && !listQ.data ? (
            <div className="p-4">
              <LoadingState rows={8} />
            </div>
          ) : listQ.isError ? (
            <div className="p-4">
              <ErrorState
                message={
                  listQ.error instanceof ApiError
                    ? listQ.error.message
                    : 'Could not load tasks.'
                }
                retry={refetchList}
              />
            </div>
          ) : items.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="No tasks yet"
                description="Spin up the first run to populate this list."
                action={{
                  label: 'Run new task',
                  onClick: () => {
                    /* CTA handled by parent shell command palette */
                  },
                }}
              />
            </div>
          ) : (
            <TasksTable
              items={items}
              selectedId={selectedId}
              onSelect={(id) => setSelectedId((cur) => (cur === id ? null : id))}
            />
          )}
        </div>
      </section>

      {/* ── RIGHT: detail drawer ────────────────────────────────────────── */}
      <aside
        className="flex flex-col overflow-hidden border-t border-border bg-secondary lg:border-l lg:border-t-0"
        style={
          {
            animation: `drawer-in ${duration.panel * 1000}ms cubic-bezier(${ease.out.join(',')})`,
          } as CSSProperties
        }
      >
        {selectedId === null ? (
          <DrawerEmpty />
        ) : detailQ.isLoading && !detailQ.data ? (
          <DrawerLoading id={selectedId} fallback={selected} onClose={() => setSelectedId(null)} />
        ) : detailQ.isError && !detailQ.data ? (
          <DrawerError
            id={selectedId}
            message={
              detailQ.error instanceof ApiError
                ? detailQ.error.message
                : 'Could not load task detail.'
            }
            retry={() => detailQ.refetch()}
            onClose={() => setSelectedId(null)}
          />
        ) : (
          <DetailDrawer
            task={(detailQ.data ?? selected) as TaskDetail | TaskRow}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            onClose={() => setSelectedId(null)}
            onCancel={() => selectedId && cancelMut.mutate(selectedId)}
            onRerun={() => selectedId && rerunMut.mutate(selectedId)}
            onPrioritize={() => selectedId && prioritizeMut.mutate(selectedId)}
            onKill={() => selectedId && killMut.mutate(selectedId)}
            isCancelling={cancelMut.isPending}
            isRerunning={rerunMut.isPending}
            isPrioritizing={prioritizeMut.isPending}
            isKilling={killMut.isPending}
          />
        )}
      </aside>
    </div>
  );
}

// ── Header ─────────────────────────────────────────────────────────────────

function Header({
  totalCount,
  runningCount,
  stalledCount,
  lastSync,
  loading,
}: {
  totalCount: number;
  runningCount: number;
  stalledCount: number;
  lastSync: string | null;
  loading: boolean;
}) {
  return (
    <div className="mb-[14px] flex items-baseline justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-h2 text-foreground">Tasks</h1>
        <div className="mt-[3px] text-[12px] text-muted-foreground">
          <span className="font-mono tabular-nums">
            {loading ? '—' : formatCount(totalCount)}
          </span>{' '}
          tasks ·{' '}
          <span className="font-mono tabular-nums">
            {loading ? '—' : formatCount(runningCount)}
          </span>{' '}
          running ·{' '}
          <span className="font-mono tabular-nums">
            {loading ? '—' : formatCount(stalledCount)}
          </span>{' '}
          stalled · last sync{' '}
          <span className="font-mono">
            {lastSync ? formatRelative(lastSync) : 'just now'}
          </span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-[12px] font-medium text-foreground transition-colors hover:bg-secondary"
        >
          <Play className="size-3" strokeWidth={1.5} />
          Run new task
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-foreground bg-foreground px-2.5 py-1.5 text-[12px] font-medium text-background transition-colors hover:bg-foreground/90"
        >
          <CommandIcon className="size-3" strokeWidth={1.5} />
          New from prompt
        </button>
      </div>
    </div>
  );
}

// ── Search bar ─────────────────────────────────────────────────────────────

function SearchBar({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="relative flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-2 font-mono text-[12.5px]">
      <Search className="size-3 shrink-0 text-meta-foreground" strokeWidth={1.5} />
      <span className="shrink-0 text-meta-foreground">filter:</span>
      <div className="relative flex-1">
        {/* Highlight overlay (visual layer) */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 truncate whitespace-pre text-foreground"
        >
          <HighlightedQuery value={value} />
        </div>
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="relative w-full bg-transparent text-transparent caret-foreground outline-none [&::selection]:bg-accent/30 [&::selection]:text-foreground"
          spellCheck={false}
          aria-label="Operator-syntax filter"
        />
      </div>
      <span className="ml-auto shrink-0 rounded-sm border border-border-subtle px-1.5 py-px font-mono text-[10px] text-meta-foreground">
        ↵
      </span>
    </div>
  );
}

// ── Chips row ──────────────────────────────────────────────────────────────

// Map a ChipKey to the backend ``counts`` keys it would prefer to read, in
// fallback order. The backend may emit either UI-flavoured keys (running) or
// raw backend statuses (in_progress, blocked, …).
const CHIP_COUNT_KEYS: Record<ChipKey, readonly string[]> = {
  all: ['all'],
  running: ['running', 'in_progress'],
  queued: ['queued', 'open'],
  stalled: ['stalled', 'blocked'],
  done_24h: ['done_24h', 'done'],
  failed: ['failed'],
};

function ChipsRow({
  active,
  counts,
  onSelect,
}: {
  active: ChipKey;
  counts: Partial<Record<TaskStatus | BackendStatus | 'all' | 'done_24h', number>>;
  onSelect: (k: ChipKey) => void;
}) {
  return (
    <div className="mt-[10px] flex flex-wrap items-center gap-1.5">
      {CHIPS.map((c) => {
        const isActive = c.key === active;
        const lookups = CHIP_COUNT_KEYS[c.key];
        const n = lookups
          .map((k) => counts[k as keyof typeof counts])
          .find((v): v is number => typeof v === 'number');
        return (
          <button
            type="button"
            key={c.key}
            onClick={() => onSelect(c.key)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[11.5px] transition-colors',
              isActive
                ? 'bg-foreground text-background border-foreground'
                : 'border-border bg-card text-muted-foreground hover:text-foreground',
            )}
            aria-pressed={isActive}
          >
            {c.label}
            <span
              className={cn(
                'font-mono tabular-nums text-[10.5px]',
                isActive ? 'opacity-80' : 'text-meta-foreground',
              )}
            >
              {n != null ? formatCount(n) : '—'}
            </span>
          </button>
        );
      })}
      <span className="flex-1" />
      <SectionLabel className="ml-auto !text-[10.5px]">sort: updated ↓</SectionLabel>
    </div>
  );
}

// ── Tasks table ────────────────────────────────────────────────────────────

function TasksTable({
  items,
  selectedId,
  onSelect,
}: {
  items: TaskRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="h-full overflow-auto">
      <table className="w-full table-fixed border-collapse">
        <colgroup>
          <col className="w-[16px]" />
          <col className="w-[86px]" />
          <col />
          <col className="w-[140px]" />
          <col className="w-[110px]" />
          <col className="w-[70px]" />
          <col className="w-[110px]" />
          <col className="w-[80px]" />
          <col className="w-[36px]" />
        </colgroup>
        <thead className="bg-muted/60">
          <tr className="text-left">
            <Th />
            <Th>ID</Th>
            <Th>Title</Th>
            <Th>Agent</Th>
            <Th>Role</Th>
            <Th align="right">Dur</Th>
            <Th>Progress</Th>
            <Th align="right">Cost</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {items.map((tk) => {
            const sel = tk.id === selectedId;
            const ui = toUiStatus(tk.status);
            const agent = readAgent(tk);
            const branch = readBranch(tk);
            const cost = readCostUsd(tk);
            const dur = readDurationMs(tk);
            const progress = typeof tk.progress === 'number' && Number.isFinite(tk.progress)
              ? tk.progress
              : null;
            return (
              <tr
                key={tk.id}
                className={cn(
                  'group cursor-pointer border-b border-border-subtle transition-colors last:border-b-0',
                  sel
                    ? 'bg-secondary [box-shadow:inset_2px_0_0_hsl(var(--accent))]'
                    : 'hover:bg-muted/40',
                )}
                onClick={() => onSelect(tk.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect(tk.id);
                  }
                }}
                tabIndex={0}
                aria-selected={sel}
              >
                <Td className="pl-[14px]">
                  <StatusDot kind={ui} />
                </Td>
                <Td className="font-mono text-[11.5px] text-muted-foreground">
                  <span className="block truncate" title={tk.id}>{tk.id}</span>
                </Td>
                <Td className="min-w-0">
                  <div
                    className={cn(
                      'truncate text-[12.5px]',
                      sel ? 'font-medium text-foreground' : 'text-foreground',
                    )}
                    title={tk.title}
                  >
                    {tk.title}
                  </div>
                  {branch && (
                    <div className="mt-0.5 truncate font-mono text-[10.5px] text-meta-foreground" title={branch}>
                      ↳ {branch}
                    </div>
                  )}
                </Td>
                <Td className="font-mono text-[11.5px] text-muted-foreground">
                  <span className="block truncate" title={agent ?? undefined}>{agent ?? '—'}</span>
                </Td>
                <Td>
                  <Pill kind="ghost">{tk.role}</Pill>
                </Td>
                <Td
                  align="right"
                  className={cn(
                    'font-mono tabular-nums text-[11.5px]',
                    ui === 'stalled' ? 'text-warning' : 'text-foreground',
                  )}
                >
                  {ui === 'queued' ? '—' : formatDuration(dur)}
                </Td>
                <Td>
                  {ui === 'queued' || progress === null ? (
                    <span className="font-mono text-[11px] text-meta-foreground">—</span>
                  ) : (
                    <ProgressCell status={ui} value={progress} />
                  )}
                </Td>
                <Td align="right" className="font-mono tabular-nums text-[11.5px]">
                  {formatUSD(cost)}
                </Td>
                <Td align="right" className="pr-3 text-meta-foreground">
                  <button
                    type="button"
                    className="grid size-6 place-items-center rounded-sm text-meta-foreground transition-colors hover:bg-muted hover:text-foreground"
                    aria-label="Row actions"
                    onClick={(e) => {
                      e.stopPropagation();
                      // Selecting the row opens the drawer where every per-task
                      // action (cancel/rerun/kill) lives. Keep the kebab affordance
                      // visible but route to the same target until a popover lands.
                      onSelect(tk.id);
                    }}
                  >
                    <MoreHorizontal className="size-3.5" strokeWidth={1.5} />
                  </button>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProgressCell({ status, value }: { status: TaskStatus; value: number }) {
  const barClass =
    status === 'failed'
      ? 'bg-destructive'
      : status === 'stalled'
        ? 'bg-warning'
        : status === 'done'
          ? 'bg-foreground'
          : 'bg-accent';
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1 flex-1 overflow-hidden rounded-sm bg-border-subtle">
        <div
          className={cn('h-full rounded-sm', barClass)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-7 text-right font-mono tabular-nums text-[10.5px] text-meta-foreground">
        {pct}%
      </span>
    </div>
  );
}

// ── Drawer states ──────────────────────────────────────────────────────────

function DrawerEmpty() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <SectionLabel>Task</SectionLabel>
      <div className="text-[13px] text-muted-foreground">
        Select a task to inspect its plan, gates, and trace.
      </div>
    </div>
  );
}

function DrawerLoading({
  id,
  fallback,
  onClose,
}: {
  id: string;
  fallback: TaskRow | null;
  onClose: () => void;
}) {
  return (
    <>
      <div className="border-b border-border px-[18px] pb-[10px] pt-[14px]">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-mono text-[11px] tracking-[0.1em] text-meta-foreground">
            TASK · {id}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 text-meta-foreground transition-colors hover:text-foreground"
            aria-label="Close detail"
          >
            <X className="size-3" strokeWidth={1.5} />
          </button>
        </div>
        <div className="mt-1.5 text-[14px] font-medium leading-snug text-foreground">
          {fallback?.title ?? '—'}
        </div>
      </div>
      <div className="flex-1 overflow-auto px-[18px] py-[14px]">
        <LoadingState rows={6} />
      </div>
    </>
  );
}

function DrawerError({
  id,
  message,
  retry,
  onClose,
}: {
  id: string;
  message: string;
  retry: () => void;
  onClose: () => void;
}) {
  return (
    <>
      <div className="flex items-center justify-between border-b border-border px-[18px] pb-[10px] pt-[14px]">
        <span className="font-mono text-[11px] tracking-[0.1em] text-meta-foreground">
          TASK · {id}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-meta-foreground transition-colors hover:text-foreground"
          aria-label="Close detail"
        >
          <X className="size-3" strokeWidth={1.5} />
        </button>
      </div>
      <div className="flex-1 overflow-auto px-[18px] py-[14px]">
        <ErrorState message={message} retry={retry} />
      </div>
    </>
  );
}

// ── Detail drawer (Summary) ─────────────────────────────────────────────────

function DetailDrawer({
  task,
  activeTab,
  onTabChange,
  onClose,
  onCancel,
  onRerun,
  onPrioritize,
  onKill,
  isCancelling,
  isRerunning,
  isPrioritizing,
  isKilling,
}: {
  task: TaskDetail | TaskRow;
  activeTab: DetailTab;
  onTabChange: (t: DetailTab) => void;
  onClose: () => void;
  onCancel: () => void;
  onRerun: () => void;
  onPrioritize: () => void;
  onKill: () => void;
  isCancelling: boolean;
  isRerunning: boolean;
  isPrioritizing: boolean;
  isKilling: boolean;
}) {
  const detail = task as TaskDetail;
  const ui = toUiStatus(task.status);
  const durMs = readDurationMs(task);
  const durLabel = ui === 'queued' ? 'queued' : `${ui} · ${formatDuration(durMs)}`;
  const agent = readAgent(task);

  // KPI: tokens, cost, branch, approvals — all tolerate field-shape variance.
  const tokensIn = detail.tokens_in ?? null;
  const tokensOut = detail.tokens_out ?? null;
  const tokensRow = readTokens(task);
  const tokensTotal =
    tokensRow ?? (tokensIn != null && tokensOut != null ? tokensIn + tokensOut : null);
  const costUsd = readCostUsd(task);
  const costCap = detail.cost_cap_usd ?? null;
  const branch = readBranch(task);
  const diffAdd = detail.diff_added;
  const diffDel = detail.diff_removed;
  const apTotal = detail.approvals_total;
  const apDone = detail.approvals_done;
  const apPending = detail.approvals_pending;

  const plan: PlanStep[] = detail.plan ?? [];

  return (
    <>
      {/* Header */}
      <div className="border-b border-border px-[18px] pb-[10px] pt-[14px]">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-mono text-[11px] tracking-[0.1em] text-meta-foreground">
            TASK · {task.id}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 text-meta-foreground transition-colors hover:text-foreground"
            aria-label="Close detail"
          >
            <X className="size-3" strokeWidth={1.5} />
          </button>
        </div>
        <div className="mt-1.5 text-[14px] font-medium leading-snug text-foreground">
          {task.title}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Pill kind={statusToPillKind(ui)}>
            <StatusDot kind={ui} />
            {durLabel}
          </Pill>
          {agent && <Pill>{agent}</Pill>}
          <Pill kind="ghost">{task.role}</Pill>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-0 border-b border-border px-3 text-[12px]">
        {DETAIL_TABS.map((tab) => {
          const isActive = tab === activeTab;
          return (
            <button
              type="button"
              key={tab}
              onClick={() => onTabChange(tab)}
              className={cn(
                'border-b-2 px-2.5 py-2.5 transition-colors',
                isActive
                  ? 'border-accent font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
              aria-pressed={isActive}
            >
              {tab}
            </button>
          );
        })}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-auto px-[18px] py-[14px] text-[12.5px]">
        {activeTab === 'Summary' && (
          <>
            {/* KPI 2×2 */}
            <div className="grid grid-cols-2 gap-2.5">
              <Kpi
                label="tokens"
                value={formatTokens(tokensTotal)}
                sub={
                  tokensIn != null && tokensOut != null
                    ? `${formatTokens(tokensIn)} in / ${formatTokens(tokensOut)} out`
                    : '—'
                }
              />
              <Kpi
                label="cost"
                value={formatUSD(costUsd)}
                sub={costCap != null ? `of ${formatUSD(costCap)} cap` : '—'}
              />
              <Kpi
                label="branch"
                value={branch ?? '—'}
                sub={
                  diffAdd != null && diffDel != null
                    ? `+${formatCount(diffAdd)} −${formatCount(diffDel)} lines`
                    : 'no diff yet'
                }
                valueMono
              />
              <Kpi
                label="approvals"
                value={
                  apTotal != null && apDone != null
                    ? `${formatCount(apDone)} / ${formatCount(apTotal)}`
                    : '—'
                }
                sub={
                  apPending != null && apPending > 0
                    ? `${formatCount(apPending)} pending`
                    : 'none pending'
                }
              />
            </div>

            {/* Plan */}
            <div className="mt-4">
              <SectionLabel className="mb-2">
                Plan{plan.length > 0 ? ` · ${plan.length} steps` : ''}
              </SectionLabel>
              {plan.length === 0 ? (
                <div className="rounded-sm border border-border-subtle bg-card/60 px-3 py-2 text-[12px] text-muted-foreground">
                  No plan steps reported.
                </div>
              ) : (
                <ol className="m-0 flex list-none flex-col gap-2 p-0">
                  {plan.map((step, i) => (
                    <li key={i} className="flex items-start gap-2.5 text-[12.5px]">
                      <span className="w-4 shrink-0 font-mono text-[11px] text-meta-foreground">
                        {i + 1}.
                      </span>
                      <span className="mt-1.5 shrink-0">
                        <StatusDot kind={step.status} />
                      </span>
                      <span
                        className={cn(
                          'flex-1',
                          step.status === 'queued'
                            ? 'text-muted-foreground'
                            : 'text-foreground',
                          step.status === 'done' && 'line-through',
                        )}
                      >
                        {step.text}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>

            {/* Action stack. ``Change model`` / ``Change role`` are not yet wired
                to a backend mutation; do not silently fall through to the
                prioritize endpoint (that's a different action with side-effects). */}
            <div className="mt-5 grid grid-cols-2 gap-1.5">
              <ActionButton onClick={onCancel} pending={isCancelling}>
                Cancel run
              </ActionButton>
              <ActionButton onClick={onRerun} pending={isRerunning}>
                Re-run
              </ActionButton>
              <ActionButton onClick={onPrioritize} pending={isPrioritizing}>
                Prioritize
              </ActionButton>
              <ActionButton onClick={() => undefined} pending={false} disabledReason="Not wired in this build">
                Change model
              </ActionButton>
              <ActionButton
                className="col-span-2 border-destructive text-destructive hover:bg-destructive/10"
                onClick={onKill}
                pending={isKilling}
              >
                Kill session
              </ActionButton>
            </div>
          </>
        )}

        {activeTab === 'Logs' && (
          <TaskLogsPanel taskId={String(task.id)} active />
        )}
        {activeTab !== 'Summary' && activeTab !== 'Logs' && (
          <TabPlaceholder tab={activeTab} />
        )}
      </div>
    </>
  );
}

function statusToPillKind(s: TaskStatus): 'success' | 'warning' | 'danger' | 'default' | 'ghost' {
  switch (s) {
    case 'running':
      return 'success';
    case 'stalled':
      return 'warning';
    case 'failed':
      return 'danger';
    case 'queued':
      return 'ghost';
    case 'done':
      return 'default';
  }
}

// ── Detail bits ────────────────────────────────────────────────────────────

function Kpi({
  label,
  value,
  sub,
  valueMono = false,
}: {
  label: string;
  value: string;
  sub: string;
  valueMono?: boolean;
}) {
  return (
    <div className="rounded-md border border-border-subtle bg-card p-[11px]">
      <div className="font-mono text-[10px] uppercase tracking-[0.1em] text-meta-foreground">
        {label}
      </div>
      <div
        className={cn(
          'mt-0.5 text-stat-md text-foreground tabular-nums',
          valueMono ? 'font-mono text-[14px]' : 'font-mono',
        )}
      >
        {value}
      </div>
      <div className="mt-0.5 text-[11px] text-meta-foreground">{sub}</div>
    </div>
  );
}

function ActionButton({
  children,
  onClick,
  pending,
  className,
  disabledReason,
}: {
  children: ReactNode;
  onClick: () => void;
  pending: boolean;
  className?: string;
  disabledReason?: string;
}) {
  const disabled = pending || disabledReason != null;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={disabledReason}
      aria-disabled={disabled}
      className={cn(
        'rounded-md border border-border bg-card px-2.5 py-1.5 text-[12px] font-medium text-foreground transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-60',
        className,
      )}
    >
      {children}
    </button>
  );
}

function TabPlaceholder({ tab }: { tab: DetailTab }) {
  return (
    <div className="rounded-md border border-border-subtle bg-card/60 px-4 py-6 text-center text-[12.5px] text-muted-foreground">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
        {tab}
      </div>
      <div className="mt-1.5">
        Live {tab.toLowerCase()} feed for this task is not wired in this build.
      </div>
    </div>
  );
}

// ── Table cells ────────────────────────────────────────────────────────────

function Th({
  children,
  align = 'left',
}: {
  children?: ReactNode;
  align?: 'left' | 'right';
}) {
  return (
    <th
      className={cn(
        'border-b border-border-subtle px-2 py-2 font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-meta-foreground',
        align === 'right' ? 'text-right' : 'text-left',
      )}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = 'left',
  className,
}: {
  children?: ReactNode;
  align?: 'left' | 'right';
  className?: string;
}) {
  return (
    <td
      className={cn(
        'px-2 py-2.5 align-top text-[12.5px]',
        align === 'right' ? 'text-right' : 'text-left',
        className,
      )}
    >
      {children}
    </td>
  );
}
