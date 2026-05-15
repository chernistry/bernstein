// Agents — grid of agent cards + drawer with token meter and live log tail.
// Source: design_handoff_bernstein_phase1/design-source/screens/screen-agents.jsx + README §6.02 / §8.

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, useRef, useEffect, useMemo } from 'react';
import { X } from 'lucide-react';
import { apiGet, apiPost } from '@/lib/api';
import { useEventStream } from '@/lib/sse';
import { formatUSD, formatTokens, formatDuration } from '@/lib/format';
import { prefersReducedMotion } from '@/lib/motion';
import { EmptyState, LoadingState, ErrorState, StatusDot, Pill, SectionLabel } from '@/lib/states';
import { cn } from '@/lib/utils';

type AgentState = 'spawning' | 'running' | 'stalled' | 'merging' | 'dead' | 'idle';

interface Agent {
  session_id: string;
  name: string;
  role: string;
  status: AgentState;
  current_task?: string | null;
  duration_ms?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | null;
  tokens_prompt?: number | null;
  tokens_context?: number | null;
  tokens_tools?: number | null;
  tokens_total?: number | null;
  tokens_cap?: number | null;
}

type LogLevel = 'INFO' | 'PLAN' | 'PASS' | 'WARN' | 'WAIT' | 'LIVE';

interface LogLine {
  ts: string;
  level: LogLevel;
  text: string;
  /** True when this line came from the live SSE stream (vs replayed history). */
  live?: boolean;
}

interface ToolCall {
  ts: string;
  name: string;
  status: 'ok' | 'warn' | 'fail' | 'pending';
}

interface ComparisonResponse {
  left: Agent;
  right: Agent;
  series: Array<{ t: string; left_tokens: number; right_tokens: number; left_cost: number; right_cost: number }>;
}

const ANSI_RE = /\x1b\[[0-9;]*m/g;
const LOG_BUFFER_MAX = 500;

const STATE_PILL_KIND: Record<AgentState, 'default' | 'success' | 'warning' | 'accent' | 'danger'> = {
  spawning: 'default',
  running: 'success',
  stalled: 'warning',
  merging: 'accent',
  dead: 'danger',
  idle: 'default',
};

const STATE_DOT_KIND: Record<AgentState, 'running' | 'queued' | 'stalled' | 'failed' | 'merging' | 'idle'> = {
  spawning: 'queued',
  running: 'running',
  stalled: 'stalled',
  merging: 'merging',
  dead: 'failed',
  idle: 'idle',
};

const STATE_LABEL: Record<AgentState, string> = {
  spawning: 'spawning',
  running: 'running',
  stalled: 'stalled',
  merging: 'merging',
  dead: 'dead',
  idle: 'idle',
};

const LEVEL_CLASS: Record<LogLevel, string> = {
  INFO: 'text-muted-foreground',
  PLAN: 'text-accent',
  PASS: 'text-success',
  WARN: 'text-warning',
  WAIT: 'text-warning',
  LIVE: 'text-muted-foreground',
};

const LEVEL_KEYWORDS: LogLevel[] = ['PLAN', 'PASS', 'WARN', 'WAIT', 'LIVE', 'INFO'];

function inferLevel(line: string): LogLevel {
  for (const lvl of LEVEL_KEYWORDS) if (line.includes(lvl)) return lvl;
  return 'INFO';
}

function avatarLabel(name: string): string {
  const n = name.toLowerCase();
  if (n.startsWith('claude')) return 'AN';
  if (n.startsWith('codex')) return 'OX';
  if (n.startsWith('gemini')) return 'GE';
  if (n.startsWith('aider')) return 'AI';
  const letters = name.replace(/[^a-zA-Z]/g, '').slice(0, 2).toUpperCase();
  return letters.length === 2 ? letters : '··';
}

function durationColor(state: AgentState, ms: number | null | undefined): string {
  if (ms == null) return 'text-foreground';
  if (state === 'stalled') return 'text-warning';
  const min = ms / 60_000;
  if (min < 10) return 'text-success';
  if (min < 30) return 'text-warning';
  return 'text-destructive';
}

function parseLogPayload(payload: unknown): LogLine | null {
  // Accept either {ts, level, text}, {ts, line}, or a raw string.
  if (typeof payload === 'string') {
    const cleaned = payload.replace(ANSI_RE, '').trim();
    if (!cleaned) return null;
    return { ts: new Date().toISOString().slice(11, 23), level: inferLevel(cleaned), text: cleaned, live: true };
  }
  if (!payload || typeof payload !== 'object') return null;
  const o = payload as Record<string, unknown>;
  const rawText = (typeof o.text === 'string' && o.text)
    || (typeof o.line === 'string' && o.line)
    || (typeof o.message === 'string' && o.message)
    || '';
  if (!rawText) return null;
  const text = rawText.replace(ANSI_RE, '').trim();
  const ts = typeof o.ts === 'string'
    ? o.ts
    : typeof o.timestamp === 'string'
      ? o.timestamp
      : new Date().toISOString().slice(11, 23);
  const level = (typeof o.level === 'string' && (LEVEL_KEYWORDS as string[]).includes(o.level.toUpperCase()))
    ? (o.level.toUpperCase() as LogLevel)
    : inferLevel(text);
  return { ts, level, text, live: true };
}

const HISTORY_FALLBACK: LogLine[] = [
  { ts: '16:42:01.214', level: 'INFO', text: 'tool_call · read_file("core/websocket/mux.py")' },
  { ts: '16:42:01.881', level: 'INFO', text: 'tool_result · 416 lines' },
  { ts: '16:42:02.103', level: 'PLAN', text: 'step 3/5 · migrate handler approve_resolve' },
  { ts: '16:42:03.448', level: 'INFO', text: 'tool_call · apply_patch("core/websocket/mux.py")' },
  { ts: '16:42:04.190', level: 'WARN', text: 'patch hunk #2 fuzz=2 — applied with offset' },
  { ts: '16:42:05.020', level: 'INFO', text: 'tool_call · pytest -k mux_per_session' },
  { ts: '16:42:07.301', level: 'PASS', text: '12 passed · 0 failed · 1.74s' },
  { ts: '16:42:08.012', level: 'INFO', text: 'approval_required · tool=apply_patch path=core/websocket/handler_kill.py' },
  { ts: '16:42:08.013', level: 'WAIT', text: 'waiting for approval id=apr_4f9a (queue depth 7)' },
];

export default function Agents() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [roleFilter, setRoleFilter] = useState<string>('All roles');
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [killingId, setKillingId] = useState<string | null>(null);
  const reduceMotion = useMemo(() => prefersReducedMotion(), []);

  const agentsQuery = useQuery<Agent[]>({
    queryKey: ['agents'],
    queryFn: () => apiGet<Agent[]>('/agents'),
    refetchInterval: 30_000,
  });

  // Fleet event-stream → invalidate agents on agent_update.
  useEventStream('/api/v1/events', {
    on: {
      agent_update: () => {
        queryClient.invalidateQueries({ queryKey: ['agents'] });
      },
    },
  });

  const agents = agentsQuery.data ?? [];
  const visibleAgents = roleFilter === 'All roles'
    ? agents
    : agents.filter((a) => a.role === roleFilter);

  // Auto-select first agent once data lands.
  useEffect(() => {
    if (!selectedId && visibleAgents.length > 0) setSelectedId(visibleAgents[0].session_id);
  }, [selectedId, visibleAgents]);

  const selected = agents.find((a) => a.session_id === selectedId) ?? null;

  const liveAgents = agents.filter((a) => a.status === 'running').length;
  const burnPerHour = agents.reduce((acc, a) => {
    const dur = a.duration_ms ?? 0;
    if (dur <= 0 || a.cost_usd == null) return acc;
    return acc + (a.cost_usd * 3_600_000) / dur;
  }, 0);

  const roles = useMemo(() => {
    const set = new Set<string>();
    agents.forEach((a) => set.add(a.role));
    return ['All roles', ...Array.from(set).sort()];
  }, [agents]);

  const onKill = async (sessionId: string) => {
    setKillingId(sessionId);
    try {
      await apiPost(`/agents/${sessionId}/kill`);
      await queryClient.invalidateQueries({ queryKey: ['agents'] });
    } finally {
      setKillingId(null);
    }
  };

  // Header content.
  const header = (
    <div className="mb-3.5 flex items-baseline justify-between gap-4">
      <div>
        <h1 className="text-h2 text-foreground">Agents</h1>
        <div className="mt-0.5 text-[12px] text-muted-foreground">
          <span className="font-mono tabular-nums">{agents.length}</span> sessions ·{' '}
          <span className="font-mono tabular-nums">{liveAgents}</span> live · burn{' '}
          <span className="font-mono tabular-nums font-medium text-foreground">
            {formatUSD(burnPerHour)}
          </span>
          {' / hr'}
        </div>
      </div>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setComparisonOpen((v) => !v)}
          className="rounded-md border border-border bg-card px-2.5 py-1.5 text-[11.5px] text-foreground hover:bg-secondary"
        >
          {comparisonOpen ? 'Close comparison' : 'View comparison'}
        </button>
      </div>
    </div>
  );

  // Role filter chips (only shown when we have data).
  const roleChips = roles.length > 1 && (
    <div className="mb-3.5 flex flex-wrap gap-1.5">
      {roles.map((r) => {
        const active = r === roleFilter;
        return (
          <button
            key={r}
            type="button"
            onClick={() => setRoleFilter(r)}
            className={cn(
              'rounded-full border px-2.5 py-[3px] text-[11.5px] transition-colors',
              active
                ? 'border-foreground bg-foreground text-background'
                : 'border-border bg-card text-muted-foreground hover:text-foreground',
            )}
          >
            {r}
          </button>
        );
      })}
    </div>
  );

  // ---- Body content based on query state ----------------------------------
  let bodyContent: React.ReactNode;

  if (agentsQuery.isLoading) {
    bodyContent = <LoadingState rows={6} />;
  } else if (agentsQuery.isError) {
    bodyContent = (
      <ErrorState
        message={agentsQuery.error instanceof Error ? agentsQuery.error.message : 'Failed to load agents.'}
        retry={() => agentsQuery.refetch()}
      />
    );
  } else if (agents.length === 0) {
    bodyContent = <EmptyState title="No agents — `bernstein run` to spawn" />;
  } else {
    bodyContent = (
      <div className="grid grid-cols-1 gap-2.5 xl:grid-cols-2">
        {visibleAgents.map((a) => (
          <AgentCard
            key={a.session_id}
            agent={a}
            selected={a.session_id === selectedId}
            onSelect={() => setSelectedId(a.session_id)}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="grid h-full grid-cols-[1fr_420px] overflow-hidden">
      {/* LEFT — header + grid */}
      <div className="overflow-auto px-[22px] py-[18px]">
        {header}
        {roleChips}
        {bodyContent}

        {comparisonOpen && (
          <ComparisonOverlay
            primary={selected}
            agents={agents}
            onClose={() => setComparisonOpen(false)}
          />
        )}
      </div>

      {/* RIGHT — selected agent drawer */}
      <AgentDrawer
        agent={selected}
        reduceMotion={reduceMotion}
        killing={killingId === selected?.session_id}
        onKill={selected ? () => onKill(selected.session_id) : undefined}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}

// ============================================================================
// Agent card (left grid)
// ============================================================================

interface AgentCardProps {
  agent: Agent;
  selected: boolean;
  onSelect: () => void;
}

function AgentCard({ agent, selected, onSelect }: AgentCardProps) {
  const pillKind = STATE_PILL_KIND[agent.status];
  const dotKind = STATE_DOT_KIND[agent.status];
  const idle = agent.status === 'idle';

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'relative flex w-full flex-col rounded-md border p-3.5 text-left transition-colors',
        selected
          ? 'border-border-strong bg-surface-raised outline outline-1 -outline-offset-1 outline-accent'
          : 'border-border bg-card hover:border-border-strong',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="grid size-[26px] place-items-center rounded-md border border-border bg-muted font-mono text-[10px] tracking-[0.05em] text-muted-foreground">
            {avatarLabel(agent.name)}
          </span>
          <div className="min-w-0">
            <div className="truncate text-[13px] font-medium text-foreground">{agent.name}</div>
            <div className="mt-px font-mono text-[10.5px] text-meta-foreground">
              {agent.session_id} · {agent.role}
            </div>
          </div>
        </div>
        <Pill kind={pillKind}>
          <StatusDot kind={dotKind} />
          {STATE_LABEL[agent.status]}
        </Pill>
      </div>

      <div
        className={cn(
          'mt-2.5 min-h-8 text-[12px] leading-snug',
          idle || !agent.current_task ? 'text-meta-foreground' : 'text-foreground',
        )}
      >
        {idle || !agent.current_task ? 'no current task — awaiting dispatch' : agent.current_task}
      </div>

      <div className="mt-3 grid grid-cols-4 border-t border-border-subtle pt-2.5">
        <Metric label="duration" value={formatDuration(agent.duration_ms)} valueClass={durationColor(agent.status, agent.duration_ms)} first />
        <Metric label="tok in" value={formatTokens(agent.tokens_in)} />
        <Metric label="tok out" value={formatTokens(agent.tokens_out)} />
        <Metric
          label="cost"
          value={formatUSD(agent.cost_usd)}
          valueClass="font-semibold text-foreground"
        />
      </div>
    </button>
  );
}

interface MetricProps {
  label: string;
  value: string;
  valueClass?: string;
  first?: boolean;
}

function Metric({ label, value, valueClass, first }: MetricProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-0.5',
        first ? 'pl-0' : 'border-l border-border-subtle pl-2.5',
      )}
    >
      <span className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-meta-foreground">
        {label}
      </span>
      <span className={cn('font-mono text-[13px] font-medium tabular-nums text-foreground', valueClass)}>
        {value}
      </span>
    </div>
  );
}

// ============================================================================
// Drawer — header / token meter / live log / tool calls
// ============================================================================

interface AgentDrawerProps {
  agent: Agent | null;
  reduceMotion: boolean;
  killing: boolean;
  onKill?: () => void;
  onClose: () => void;
}

function AgentDrawer({ agent, reduceMotion, killing, onKill, onClose }: AgentDrawerProps) {
  // Local log buffer — capped at LOG_BUFFER_MAX, fed by SSE.
  const [liveLog, setLiveLog] = useState<LogLine[]>([]);
  const [recentTools, setRecentTools] = useState<ToolCall[]>([]);
  const logScrollRef = useRef<HTMLDivElement>(null);
  const sessionId = agent?.session_id ?? null;

  // Reset buffer when switching sessions.
  useEffect(() => {
    setLiveLog([]);
    setRecentTools([]);
  }, [sessionId]);

  useEventStream(sessionId ? `/api/v1/agents/${sessionId}/stream` : '', {
    enabled: Boolean(sessionId),
    on: {
      log: (data) => {
        const line = parseLogPayload(data);
        if (!line) return;
        setLiveLog((prev) => {
          const next = [...prev, line];
          return next.length > LOG_BUFFER_MAX ? next.slice(next.length - LOG_BUFFER_MAX) : next;
        });
      },
      tool_call: (data) => {
        if (!data || typeof data !== 'object') return;
        const o = data as Record<string, unknown>;
        const name = typeof o.name === 'string' ? o.name : typeof o.tool === 'string' ? o.tool : null;
        if (!name) return;
        const ts = typeof o.ts === 'string' ? o.ts : new Date().toISOString().slice(11, 19);
        const status: ToolCall['status'] =
          o.status === 'ok' || o.status === 'warn' || o.status === 'fail' || o.status === 'pending'
            ? o.status
            : 'ok';
        const call: ToolCall = { ts, name, status };
        setRecentTools((prev) => [call, ...prev].slice(0, 5));
      },
    },
  });

  // Auto-scroll to tail on new line.
  useEffect(() => {
    const el = logScrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [liveLog.length]);

  if (!agent) {
    return (
      <aside className="flex flex-col overflow-hidden border-l border-border bg-secondary">
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="text-[12px] text-meta-foreground">Select an agent to inspect.</div>
        </div>
      </aside>
    );
  }

  const dotKind = STATE_DOT_KIND[agent.status];
  const pillKind = STATE_PILL_KIND[agent.status];
  const statusLabel = agent.status === 'running' ? 'live' : STATE_LABEL[agent.status];

  // Token meter values with sensible fallbacks for incomplete payloads.
  const prompt = agent.tokens_prompt ?? 0;
  const context = agent.tokens_context ?? 0;
  const tools = agent.tokens_tools ?? 0;
  const total = agent.tokens_total ?? prompt + context + tools;
  const cap = agent.tokens_cap ?? 0;
  const sumForBar = Math.max(prompt + context + tools, 1);
  const promptPct = (prompt / sumForBar) * 100;
  const contextPct = (context / sumForBar) * 100;
  const toolsPct = (tools / sumForBar) * 100;
  const contextOfBudgetPct = cap > 0 ? Math.min(100, Math.round((total / cap) * 100)) : null;

  const history = liveLog.length === 0 ? HISTORY_FALLBACK : [];
  const tail = liveLog;

  return (
    <aside className="flex flex-col overflow-hidden border-l border-border bg-secondary">
      {/* Header */}
      <div className="border-b border-border px-4 pb-2.5 pt-3.5">
        <div className="flex items-center justify-between">
          <SectionLabel>SESSION · {agent.session_id}</SectionLabel>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-0.5 text-meta-foreground hover:text-foreground"
            aria-label="Close drawer"
          >
            <X className="size-3" strokeWidth={1.5} />
          </button>
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          <span className="text-[14px] font-medium text-foreground">{agent.name}</span>
          <Pill kind={pillKind}>
            <StatusDot kind={dotKind} />
            {statusLabel}
          </Pill>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            className="rounded-md border border-border bg-card px-2.5 py-1.5 text-[11.5px] text-foreground hover:bg-secondary"
          >
            Open log full
          </button>
          <button
            type="button"
            onClick={onKill}
            disabled={killing || agent.status === 'dead' || agent.status === 'idle'}
            className={cn(
              'ml-auto rounded-md border border-destructive bg-transparent px-2.5 py-1.5 text-[11.5px] text-destructive hover:bg-destructive/10',
              'disabled:cursor-not-allowed disabled:opacity-50',
            )}
          >
            {killing ? 'Killing…' : 'Kill session'}
          </button>
        </div>
      </div>

      {/* Token meter */}
      <div className="border-b border-border px-4 py-3">
        <SectionLabel>TOKENS THIS RUN</SectionLabel>
        <div className="mt-1 flex items-baseline gap-2.5">
          <span className="font-mono text-[22px] font-medium tabular-nums text-foreground">
            {total.toLocaleString('en-US')}
          </span>
          {cap > 0 && (
            <span className="font-mono text-[11px] tabular-nums text-meta-foreground">
              / {cap.toLocaleString('en-US')} cap
            </span>
          )}
        </div>
        <div className="mt-2 flex h-1.5 overflow-hidden rounded-sm bg-border-subtle">
          <div className="bg-accent" style={{ width: `${promptPct}%` }} />
          <div className="bg-accent/60" style={{ width: `${contextPct}%` }} />
          <div className="bg-warning" style={{ width: `${toolsPct}%` }} />
        </div>
        <div className="mt-1.5 font-mono text-[10.5px] tabular-nums text-meta-foreground">
          {(agent.tokens_in ?? 0).toLocaleString('en-US')} in /{' '}
          {(agent.tokens_out ?? 0).toLocaleString('en-US')} out
          {contextOfBudgetPct != null && ` · ${contextOfBudgetPct}% context`}
        </div>
        <div className="mt-1 flex justify-between font-mono text-[10.5px] tabular-nums text-meta-foreground">
          <span>prompt {prompt.toLocaleString('en-US')}</span>
          <span>context {context.toLocaleString('en-US')}</span>
          <span>tools {tools.toLocaleString('en-US')}</span>
        </div>
      </div>

      {/* Live log */}
      <div
        ref={logScrollRef}
        className="min-h-0 flex-1 overflow-auto border border-border-subtle bg-background px-3.5 py-2.5 font-mono text-log text-foreground"
      >
        {history.length > 0 && (
          <>
            {history.map((l, i) => (
              <LogRow key={`h-${i}`} line={l} />
            ))}
            <SeparatorRow />
          </>
        )}
        {tail.map((l, i) => (
          <LogRow key={`t-${i}-${l.ts}`} line={l} />
        ))}
        {tail.length === 0 && history.length === 0 && (
          <div className="text-meta-foreground">stream open · awaiting events</div>
        )}
        {/* Static caret — no idle motion, honor reduce-motion. */}
        <div className="mt-0.5 flex items-baseline gap-2.5">
          <span className="min-w-[92px] text-[10.5px] text-meta-foreground">
            {new Date().toISOString().slice(11, 19)}.
          </span>
          {!reduceMotion && (
            <span aria-hidden className="inline-block h-3 w-[7px] bg-accent" />
          )}
        </div>
      </div>

      {/* Recent tool calls strip */}
      <div className="border-t border-border bg-secondary px-4 py-2.5">
        <SectionLabel>RECENT TOOL CALLS</SectionLabel>
        <ul className="mt-1.5 space-y-0.5">
          {recentTools.length === 0 ? (
            <li className="font-mono text-[10.5px] text-meta-foreground">—</li>
          ) : (
            recentTools.map((t, i) => (
              <li
                key={`${t.ts}-${i}`}
                className="flex items-baseline gap-2 font-mono text-[10.5px] text-meta-foreground"
              >
                <StatusDot
                  kind={
                    t.status === 'ok'
                      ? 'running'
                      : t.status === 'warn'
                        ? 'stalled'
                        : t.status === 'fail'
                          ? 'failed'
                          : 'queued'
                  }
                />
                <span className="tabular-nums">{t.ts}</span>
                <span className="truncate text-foreground">{t.name}</span>
              </li>
            ))
          )}
        </ul>
      </div>
    </aside>
  );
}

function LogRow({ line }: { line: LogLine }) {
  return (
    <div className="flex items-baseline gap-2.5 py-px">
      <span className="min-w-[92px] text-[10.5px] text-meta-foreground tabular-nums">{line.ts}</span>
      <span className={cn('min-w-[36px] text-[10.5px]', LEVEL_CLASS[line.level])}>{line.level}</span>
      <span className={cn('break-all', LEVEL_CLASS[line.level])}>{line.text}</span>
    </div>
  );
}

function SeparatorRow() {
  return (
    <div className="py-2 text-meta-foreground">
      ↳ historical / live separator —————————
    </div>
  );
}

// ============================================================================
// Compare-two overlay — side-by-side drawers sharing a time axis.
// ============================================================================

interface ComparisonOverlayProps {
  primary: Agent | null;
  agents: Agent[];
  onClose: () => void;
}

function ComparisonOverlay({ primary, agents, onClose }: ComparisonOverlayProps) {
  const [otherId, setOtherId] = useState<string | null>(() => {
    const fallback = agents.find((a) => a.session_id !== primary?.session_id);
    return fallback?.session_id ?? null;
  });

  const comparisonQuery = useQuery<ComparisonResponse>({
    queryKey: ['agents', 'comparison', primary?.session_id, otherId],
    queryFn: () =>
      apiGet<ComparisonResponse>(
        `/agents/comparison?left=${primary?.session_id ?? ''}&right=${otherId ?? ''}`,
      ),
    enabled: Boolean(primary?.session_id && otherId),
  });

  const other = agents.find((a) => a.session_id === otherId) ?? null;

  return (
    <details
      open
      className="mt-4 rounded-md border border-border bg-card"
    >
      <summary className="flex cursor-pointer items-center justify-between px-4 py-2 text-[12px] text-foreground">
        <span className="font-mono uppercase tracking-[0.1em] text-meta-foreground">COMPARISON</span>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            onClose();
          }}
          className="rounded p-0.5 text-meta-foreground hover:text-foreground"
          aria-label="Close comparison"
        >
          <X className="size-3" strokeWidth={1.5} />
        </button>
      </summary>
      <div className="border-t border-border-subtle p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground">
          <span>Compare</span>
          <span className="rounded-md border border-border bg-surface-raised px-2 py-1 font-mono text-[10.5px] text-foreground">
            {primary?.name ?? '—'}
          </span>
          <span>vs.</span>
          <select
            value={otherId ?? ''}
            onChange={(e) => setOtherId(e.target.value || null)}
            className="rounded-md border border-border bg-card px-2 py-1 text-[11.5px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="">Select agent…</option>
            {agents
              .filter((a) => a.session_id !== primary?.session_id)
              .map((a) => (
                <option key={a.session_id} value={a.session_id}>
                  {a.name} · {a.session_id}
                </option>
              ))}
          </select>
        </div>

        {comparisonQuery.isLoading && <LoadingState rows={3} />}
        {comparisonQuery.isError && (
          <ErrorState
            message="Failed to load comparison."
            retry={() => comparisonQuery.refetch()}
          />
        )}
        {!comparisonQuery.isLoading && !comparisonQuery.isError && (
          <div className="grid grid-cols-2 gap-3">
            <ComparisonPane agent={primary} side="left" />
            <ComparisonPane agent={other} side="right" />
          </div>
        )}
      </div>
    </details>
  );
}

function ComparisonPane({ agent, side }: { agent: Agent | null; side: 'left' | 'right' }) {
  if (!agent) {
    return (
      <div className="rounded-md border border-border-subtle bg-surface-raised p-3 text-[11.5px] text-meta-foreground">
        {side === 'left' ? 'Select primary agent.' : 'Select comparison agent.'}
      </div>
    );
  }
  const pillKind = STATE_PILL_KIND[agent.status];
  const dotKind = STATE_DOT_KIND[agent.status];
  return (
    <div className="rounded-md border border-border bg-surface-raised p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-medium text-foreground">{agent.name}</div>
          <div className="font-mono text-[10.5px] text-meta-foreground">{agent.session_id}</div>
        </div>
        <Pill kind={pillKind}>
          <StatusDot kind={dotKind} />
          {STATE_LABEL[agent.status]}
        </Pill>
      </div>
      <div className="mt-2.5 grid grid-cols-2 gap-2 border-t border-border-subtle pt-2.5">
        <Metric label="duration" value={formatDuration(agent.duration_ms)} first />
        <Metric label="cost" value={formatUSD(agent.cost_usd)} valueClass="font-semibold text-foreground" />
        <Metric label="tok in" value={formatTokens(agent.tokens_in)} first />
        <Metric label="tok out" value={formatTokens(agent.tokens_out)} />
      </div>
    </div>
  );
}
