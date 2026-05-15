// Approvals screen — pending tool-call queue + Why? + Diff + Action bar.
// Source of truth: design_handoff_bernstein_phase1/design-source/screens/screen-approvals.jsx
// + README §6.03 (Approvals specs) and §8 (states contract).

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, AlertTriangle, Check, X } from 'lucide-react';
import { apiGet, apiPost } from '@/lib/api';
import { useEventStream } from '@/lib/sse';
import { formatDuration } from '@/lib/format';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Pill,
  SectionLabel,
  StatusDot,
} from '@/lib/states';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type RiskClass = 'low' | 'moderate' | 'elevated' | 'high';

interface ApprovalReason {
  text: string;
  severity?: 'info' | 'warning' | 'danger';
}

/** Wire shape from `GET /approvals/queue`. */
interface QueuedApproval {
  id: string;
  session_id: string;
  agent_role: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  created_at: number;
  ttl_seconds: number;
}

interface QueuedApprovalsResponse {
  pending: QueuedApproval[];
}

type Decision = 'allow' | 'reject' | 'always';

// ---------------------------------------------------------------------------
// Pure helpers — derive design-only fields from generic tool_args.
// ---------------------------------------------------------------------------

function pickString(args: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    const v = args[k];
    if (typeof v === 'string' && v.length > 0) return v;
  }
  return '';
}

function pickNumber(args: Record<string, unknown>, ...keys: string[]): number | null {
  for (const k of keys) {
    const v = args[k];
    if (typeof v === 'number' && !Number.isNaN(v)) return v;
  }
  return null;
}

function pickStringList(args: Record<string, unknown>, ...keys: string[]): string[] {
  for (const k of keys) {
    const v = args[k];
    if (Array.isArray(v) && v.every((x) => typeof x === 'string')) {
      return v as string[];
    }
  }
  return [];
}

function classifyRisk(score: number | null): RiskClass {
  if (score == null) return 'moderate';
  if (score >= 0.75) return 'high';
  if (score >= 0.45) return 'elevated';
  if (score >= 0.25) return 'moderate';
  return 'low';
}

const RISK_PILL_KIND: Record<RiskClass, 'success' | 'warning' | 'danger'> = {
  low: 'success',
  moderate: 'warning',
  elevated: 'warning',
  high: 'danger',
};

const RISK_LABEL: Record<RiskClass, string> = {
  low: 'low',
  moderate: 'moderate',
  elevated: 'elevated',
  high: 'high',
};

const REASON_DOT_KIND: Record<NonNullable<ApprovalReason['severity']>, 'failed' | 'stalled' | 'idle'> = {
  danger: 'failed',
  warning: 'stalled',
  info: 'idle',
};

/** Wait time in seconds, derived from epoch-second `created_at`. */
function waitSeconds(createdAt: number, now: number): number {
  return Math.max(0, Math.floor(now / 1000 - createdAt));
}

/** Derive the human "target" line (path / command / URL). */
function approvalTarget(a: QueuedApproval): string {
  return (
    pickString(a.tool_args, 'target', 'path', 'file', 'command', 'url', 'cmd') ||
    Object.keys(a.tool_args).slice(0, 3).join(' · ') ||
    '—'
  );
}

/** Derive task id label, e.g. T-0419 from `task_id` or `task`. */
function approvalTaskId(a: QueuedApproval): string {
  return pickString(a.tool_args, 'task_id', 'task') || a.session_id.slice(0, 8);
}

/** Risk score 0..1; defaults to 0.5 if backend hasn't enriched the payload. */
function approvalRisk(a: QueuedApproval): number {
  const r = pickNumber(a.tool_args, 'risk', 'risk_score', 'score');
  if (r == null) return 0.5;
  return Math.max(0, Math.min(1, r));
}

function approvalReasons(a: QueuedApproval): ApprovalReason[] {
  const raw = pickStringList(a.tool_args, 'reasons', 'why');
  if (raw.length > 0) return raw.map((text) => ({ text, severity: 'warning' }));
  // Fall back to a single explanatory line so the Why? card never reads empty.
  return [
    {
      text: `Tool "${a.tool_name}" requested by ${a.agent_role} requires explicit approval.`,
      severity: 'info',
    },
  ];
}

function approvalDiff(a: QueuedApproval): string {
  const diff = pickString(a.tool_args, 'diff', 'patch');
  if (diff) return diff;
  // Fall back to a JSON view of the args so operators can still inspect.
  try {
    return JSON.stringify(a.tool_args, null, 2);
  } catch {
    return '';
  }
}

function diffCounts(diff: string): { plus: number; minus: number } {
  let plus = 0;
  let minus = 0;
  for (const line of diff.split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) plus += 1;
    else if (line.startsWith('-') && !line.startsWith('---')) minus += 1;
  }
  return { plus, minus };
}

function diffFilename(a: QueuedApproval): string {
  return pickString(a.tool_args, 'file', 'path', 'target', 'filename') || a.tool_name;
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export default function Approvals() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Re-render once a second so wait timers tick without refetching.
  const [now, setNow] = useState<number>(() => Date.now());

  const queueQ = useQuery<QueuedApprovalsResponse>({
    queryKey: ['approvals', 'queue'],
    queryFn: () => apiGet<QueuedApprovalsResponse>('/approvals/queue'),
    refetchInterval: 10_000,
  });

  const pending: QueuedApproval[] = useMemo(
    () => queueQ.data?.pending ?? [],
    [queueQ.data?.pending],
  );

  // Auto-select the first row whenever the queue changes and the current
  // selection has fallen out of the list.
  useEffect(() => {
    if (pending.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (!selectedId || !pending.some((a) => a.id === selectedId)) {
      setSelectedId(pending[0].id);
    }
  }, [pending, selectedId]);

  // Tick the wait clock once a second while items are pending.
  useEffect(() => {
    if (pending.length === 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [pending.length]);

  // SSE — refresh the queue cache on `approval_pending` notifications.
  useEventStream('/api/v1/events', {
    on: {
      approval_pending: () => {
        queryClient.invalidateQueries({ queryKey: ['approvals', 'queue'] });
        queryClient.invalidateQueries({ queryKey: ['approvals'] });
      },
    },
  });

  const showToast = (msg: string) => {
    setToast(msg);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2000);
  };

  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    },
    [],
  );

  const resolveMutation = useMutation({
    mutationFn: ({
      approvalId,
      decision,
      reason,
    }: {
      approvalId: string;
      decision: Decision;
      reason?: string;
    }) =>
      apiPost<{ status: string; id: string; decision: string }>(
        `/approvals/${encodeURIComponent(approvalId)}/resolve`,
        reason ? { decision, reason } : { decision },
      ),
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ['approvals', 'queue'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      const verb =
        vars.decision === 'allow'
          ? 'Approved'
          : vars.decision === 'reject'
            ? 'Denied'
            : 'Saved always-rule for';
      showToast(`${verb} ${vars.approvalId}`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : 'Could not resolve approval';
      showToast(msg);
    },
  });

  const moveSelection = (delta: 1 | -1) => {
    if (pending.length === 0) return;
    const idx = pending.findIndex((a) => a.id === selectedId);
    const next = idx === -1 ? 0 : (idx + delta + pending.length) % pending.length;
    setSelectedId(pending[next].id);
  };

  // Keyboard shortcuts: A approve · D deny · ↑/↓ navigate.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't fight form inputs.
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable) {
          return;
        }
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        moveSelection(1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        moveSelection(-1);
      } else if (e.key === 'a' || e.key === 'A') {
        if (!selectedId || resolveMutation.isPending) return;
        e.preventDefault();
        resolveMutation.mutate({ approvalId: selectedId, decision: 'allow' });
      } else if (e.key === 'd' || e.key === 'D') {
        if (!selectedId || resolveMutation.isPending) return;
        e.preventDefault();
        resolveMutation.mutate({ approvalId: selectedId, decision: 'reject' });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, pending, resolveMutation.isPending]);

  // -------------------------------------------------------------------------
  // Top-level state branches
  // -------------------------------------------------------------------------

  if (queueQ.isLoading) {
    return (
      <div className="grid h-full grid-cols-[440px_1fr] overflow-hidden">
        <div className="flex flex-col border-r border-border bg-secondary p-4">
          <SectionLabel className="mb-3">Approvals queue</SectionLabel>
          <LoadingState rows={5} />
        </div>
        <div className="p-6">
          <LoadingState rows={4} label="Loading selected approval" />
        </div>
      </div>
    );
  }

  if (queueQ.isError) {
    return (
      <div className="p-6">
        <ErrorState
          title="Could not load approvals"
          message={
            queueQ.error instanceof Error
              ? queueQ.error.message
              : 'Approvals queue request failed.'
          }
          retry={() => queueQ.refetch()}
        />
      </div>
    );
  }

  const oldestWait =
    pending.length > 0
      ? Math.max(...pending.map((a) => waitSeconds(a.created_at, now)))
      : 0;

  const selected = pending.find((a) => a.id === selectedId) ?? null;

  return (
    <div className="grid h-full grid-cols-[440px_1fr] overflow-hidden">
      {/* LEFT — queue list */}
      <aside className="flex flex-col overflow-hidden border-r border-border bg-secondary">
        <header className="px-[18px] pb-3 pt-[18px]">
          <h2 className="text-h2 text-foreground">Approvals queue</h2>
          <div className="mt-1 text-[12px] text-muted-foreground">
            <span className="font-mono tabular-nums">{pending.length}</span> pending
            {pending.length > 0 && (
              <>
                {' · oldest '}
                <span className="font-mono tabular-nums">
                  {formatDuration(oldestWait, 's')}
                </span>
              </>
            )}
          </div>
        </header>
        <div className="flex-1 overflow-auto">
          {pending.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="No pending approvals"
                description="quiet on the conducting podium"
              />
            </div>
          ) : (
            <ul role="listbox" aria-label="Pending approvals" className="flex flex-col">
              {pending.map((approval) => (
                <QueueRow
                  key={approval.id}
                  approval={approval}
                  selected={approval.id === selectedId}
                  waitS={waitSeconds(approval.created_at, now)}
                  onClick={() => setSelectedId(approval.id)}
                />
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* RIGHT — selected approval */}
      <section className="flex flex-col gap-3.5 overflow-auto px-[22px] py-[18px]">
        {selected ? (
          <SelectedApproval
            approval={selected}
            now={now}
            pending={resolveMutation.isPending}
            onDecision={(decision, reason) =>
              resolveMutation.mutate({ approvalId: selected.id, decision, reason })
            }
          />
        ) : (
          <EmptyState
            title="No pending approvals"
            description="quiet on the conducting podium"
          />
        )}
      </section>

      {/* Inline toast — bottom-right, auto-clears after 2s. */}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-10 right-6 z-50"
      >
        {toast && (
          <div className="pointer-events-auto rounded-md border border-border bg-card px-3 py-2 text-body shadow-md">
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Queue row
// ---------------------------------------------------------------------------

interface QueueRowProps {
  approval: QueuedApproval;
  selected: boolean;
  waitS: number;
  onClick: () => void;
}

function QueueRow({ approval, selected, waitS, onClick }: QueueRowProps) {
  const risk = approvalRisk(approval);
  const rclass = classifyRisk(risk);
  const target = approvalTarget(approval);
  const taskId = approvalTaskId(approval);

  return (
    <li>
      <button
        type="button"
        role="option"
        aria-selected={selected}
        onClick={onClick}
        className={cn(
          'group flex w-full items-start justify-between gap-2.5 border-b border-border-subtle px-4 py-3 text-left transition-colors',
          selected
            ? 'border-l-2 border-l-accent bg-secondary'
            : 'border-l-2 border-l-transparent hover:bg-card/40',
        )}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="rounded-sm border border-border-subtle bg-surface-raised px-1.5 py-px font-mono text-[11px] font-medium text-foreground">
              {approval.tool_name}
            </span>
            <Pill kind="ghost">{approval.agent_role}</Pill>
          </div>
          <div className="mt-1.5 truncate font-mono text-[11.5px] text-foreground">
            {target}
          </div>
          <div className="mt-1.5 flex items-center gap-2.5">
            <span className="font-mono text-[10.5px] text-meta-foreground">
              {approval.session_id}
            </span>
            <span className="size-[3px] rounded-full bg-meta-foreground" />
            <span className="font-mono text-[10.5px] text-meta-foreground">
              {taskId}
            </span>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <Pill kind={RISK_PILL_KIND[rclass]}>
            <AlertTriangle className="size-2.5" strokeWidth={1.75} />
            <span className="tabular-nums">{risk.toFixed(2)}</span>
          </Pill>
          <div className="mt-1.5 font-mono text-[10.5px] tabular-nums text-meta-foreground">
            {formatDuration(waitS, 's')}
          </div>
        </div>
      </button>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Selected approval (Why? + Diff + Action bar)
// ---------------------------------------------------------------------------

interface SelectedApprovalProps {
  approval: QueuedApproval;
  now: number;
  pending: boolean;
  onDecision: (decision: Decision, reason?: string) => void;
}

function SelectedApproval({
  approval,
  now,
  pending,
  onDecision,
}: SelectedApprovalProps) {
  const risk = approvalRisk(approval);
  const rclass = classifyRisk(risk);
  const reasons = approvalReasons(approval);
  const diff = approvalDiff(approval);
  const { plus, minus } = diffCounts(diff);
  const filename = diffFilename(approval);
  const taskId = approvalTaskId(approval);
  const target = approvalTarget(approval);
  const sloS = approval.ttl_seconds;
  const waitS = waitSeconds(approval.created_at, now);

  return (
    <>
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="font-mono text-[11px] uppercase tracking-[0.1em] text-meta-foreground">
            APPROVAL · {approval.id}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2.5 text-h2 text-foreground">
            <span>{approval.tool_name}</span>
            <span className="text-[14px] font-normal text-meta-foreground">
              requested by
            </span>
            <span className="font-mono text-[14px] font-medium">
              {approval.session_id}
            </span>
          </div>
          <div className="mt-1.5 font-mono text-[12px] text-muted-foreground">
            <span className="truncate">{target}</span>
            {' · task '}
            <span className="text-foreground">{taskId}</span>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <Pill kind={RISK_PILL_KIND[rclass]}>
            <AlertTriangle className="size-3" strokeWidth={1.75} />
            risk · <span className="tabular-nums">{risk.toFixed(2)}</span>{' '}
            {RISK_LABEL[rclass]}
          </Pill>
          <div className="mt-1.5 font-mono text-[11px] tabular-nums text-meta-foreground">
            waiting {formatDuration(waitS, 's')} · SLO {formatDuration(sloS, 's')}
          </div>
        </div>
      </div>

      {/* Why? card */}
      <div className="rounded-md border border-border bg-card p-3.5">
        <div className="mb-2 flex items-center gap-2">
          <ShieldCheck className="size-3.5 text-foreground" strokeWidth={1.5} />
          <span className="text-body-md text-foreground">Why this needs review</span>
          <span className="flex-1" />
          <span className="font-mono text-[10.5px] text-meta-foreground">
            {reasons.length} {reasons.length === 1 ? 'reason' : 'reasons'} · 1 mitigation
          </span>
        </div>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {reasons.map((r, i) => (
            <li
              key={i}
              className="flex items-start gap-2 text-[12.5px] text-foreground"
            >
              <span className="pt-1.5">
                <StatusDot kind={REASON_DOT_KIND[r.severity ?? 'info']} />
              </span>
              <span>{r.text}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Diff card */}
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-surface-raised px-3.5 py-2.5">
          <div className="font-mono text-[11.5px] text-foreground">
            {filename}
            <span className="ml-2.5 tabular-nums text-meta-foreground">
              <span className="text-success">+{plus}</span>{' '}
              <span className="text-destructive">−{minus}</span>
            </span>
          </div>
        </div>
        <pre
          className="m-0 max-h-[240px] overflow-auto bg-background px-3.5 py-2.5 font-mono text-log text-foreground"
          aria-label="Approval diff"
        >
          {diff
            ? diff.split('\n').map((line, i) => (
                <span
                  key={i}
                  className={cn(
                    'block',
                    line.startsWith('+') &&
                      !line.startsWith('+++') &&
                      'bg-success/20 text-success',
                    line.startsWith('-') &&
                      !line.startsWith('---') &&
                      'bg-destructive/15 text-destructive',
                  )}
                >
                  {line || ' '}
                </span>
              ))
            : <span className="text-meta-foreground">no diff</span>}
        </pre>
      </div>

      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card p-3">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={pending}
            onClick={() => onDecision('allow')}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-body-md text-primary-foreground transition-colors',
              'hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
              'disabled:opacity-60',
            )}
          >
            <Check className="size-3.5" strokeWidth={2} />
            Approve once
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => onDecision('reject')}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md border border-destructive bg-transparent px-3 py-1.5 text-body-md text-destructive transition-colors',
              'hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive focus-visible:ring-offset-2 focus-visible:ring-offset-background',
              'disabled:opacity-60',
            )}
          >
            <X className="size-3.5" strokeWidth={2} />
            Deny
          </button>
        </div>
        <div className="mx-1 h-5 w-px bg-border-subtle" />
        <div className="flex flex-1 flex-wrap items-center gap-1.5">
          <GhostAction
            disabled={pending}
            onClick={() => onDecision('always', `tool:${approval.tool_name}`)}
          >
            Always allow · this tool
          </GhostAction>
          <GhostAction
            disabled={pending}
            onClick={() => onDecision('always', `agent:${approval.agent_role}`)}
          >
            Always allow · this agent
          </GhostAction>
          <GhostAction
            disabled={pending}
            onClick={() => onDecision('reject', 'pattern')}
          >
            Always deny · pattern
          </GhostAction>
        </div>
        <div className="font-mono text-[10.5px] text-meta-foreground">
          <kbd className="mr-1 rounded-sm border border-border-subtle px-1.5 py-px">
            A
          </kbd>
          = approve ·{' '}
          <kbd className="mx-1 rounded-sm border border-border-subtle px-1.5 py-px">
            D
          </kbd>
          = deny
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Local primitives
// ---------------------------------------------------------------------------

function GhostAction({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'rounded-md border border-transparent bg-transparent px-2.5 py-1 text-[12px] text-muted-foreground transition-colors',
        'hover:border-border hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:opacity-60',
      )}
    >
      {children}
    </button>
  );
}
