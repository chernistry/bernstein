// Approvals screen - pending tool-call queue + Why? + Diff + Action bar.
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
// Pure helpers - derive design-only fields from generic tool_args.
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
    '-'
  );
}

/** Derive task id label, e.g. T-0419 from `task_id` or `task`. */
function approvalTaskId(a: QueuedApproval): string {
  return pickString(a.tool_args, 'task_id', 'task') || a.session_id.slice(0, 8);
}

/** Risk score 0..1; defaults to 0.5 if backend hasn't enriched the payload.
 *
 * Backends may emit either a 0..1 float (`risk: 0.74`) or a 0..100 integer
 * (`risk_score: 74`). Anything > 1.5 is assumed to be a percent and divided
 * by 100 before clamping. Negative values clamp to 0.
 */
function approvalRisk(a: QueuedApproval): number {
  const r = pickNumber(a.tool_args, 'risk', 'risk_score', 'score');
  if (r == null) return 0.5;
  const normalized = r > 1.5 ? r / 100 : r;
  return Math.max(0, Math.min(1, normalized));
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

/** Result of resolving the diff payload for an approval.
 *
 * `kind` distinguishes a real diff from a JSON-args fallback or an absent
 * payload entirely so the UI can render the correct affordance. The previous
 * implementation conflated all three into a single string and crashed when
 * `diff` was explicitly `null` or a non-string value.
 */
type DiffPayload =
  | { kind: 'diff'; text: string }
  | { kind: 'args'; text: string }
  | { kind: 'empty' };

function approvalDiff(a: QueuedApproval): DiffPayload {
  // Defensive: `diff` / `patch` may be null, a number, or a non-string object.
  const raw = a.tool_args?.diff ?? a.tool_args?.patch;
  if (typeof raw === 'string' && raw.length > 0) {
    return { kind: 'diff', text: raw };
  }
  // Fall back to a JSON view of the args so operators can still inspect,
  // but only when there are actually args worth showing.
  if (a.tool_args && Object.keys(a.tool_args).length > 0) {
    try {
      return { kind: 'args', text: JSON.stringify(a.tool_args, null, 2) };
    } catch {
      return { kind: 'empty' };
    }
  }
  return { kind: 'empty' };
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

/** Inline toast severity - only `error` extends the default 2s window. */
type ToastKind = 'info' | 'error';
interface ToastState {
  message: string;
  kind: ToastKind;
}

const QUEUE_KEY = ['approvals', 'queue'] as const;

export default function Approvals() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Re-render once a second so wait timers tick without refetching.
  const [now, setNow] = useState<number>(() => Date.now());
  // Track approvals optimistically removed by an in-flight mutation so the
  // queue length, badge, and keyboard navigation stay accurate during the
  // network round-trip. Cleared on settle (success or rollback on error).
  const [optimisticallyResolved, setOptimisticallyResolved] = useState<Set<string>>(
    () => new Set(),
  );

  const queueQ = useQuery<QueuedApprovalsResponse>({
    queryKey: QUEUE_KEY,
    queryFn: () => apiGet<QueuedApprovalsResponse>('/approvals/queue'),
    refetchInterval: 10_000,
  });

  // Drop optimistically-resolved rows from the displayed list so badges and
  // keyboard navigation stay in sync with the user's last action. Defensive
  // against missing/malformed `pending`; backends sometimes wrap the list.
  const pending: QueuedApproval[] = useMemo(() => {
    const raw = Array.isArray(queueQ.data?.pending) ? queueQ.data!.pending : [];
    if (optimisticallyResolved.size === 0) return raw;
    return raw.filter((a) => !optimisticallyResolved.has(a.id));
  }, [queueQ.data?.pending, optimisticallyResolved]);

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

  // SSE - refresh the queue cache on `approval_pending` notifications.
  useEventStream('/api/v1/events', {
    on: {
      approval_pending: () => {
        queryClient.invalidateQueries({ queryKey: QUEUE_KEY });
        queryClient.invalidateQueries({ queryKey: ['approvals'] });
      },
    },
  });

  // Errors deserve a longer dwell so operators can read what failed; happy-path
  // confirmations stay snappy at 2s.
  const showToast = (message: string, kind: ToastKind = 'info') => {
    setToast({ message, kind });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), kind === 'error' ? 5000 : 2000);
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
    // Optimistically drop the row so the badge count, keyboard navigation, and
    // selection auto-advance immediately. We track the id locally instead of
    // mutating the cached query to keep React Query's refetch in charge of the
    // canonical list - the optimistic id is rolled back on error.
    onMutate: ({ approvalId }) => {
      setOptimisticallyResolved((prev) => {
        const next = new Set(prev);
        next.add(approvalId);
        return next;
      });
      return { approvalId };
    },
    onSuccess: (_data, vars) => {
      // Force a refetch so we converge on the server's canonical list and the
      // optimistic id naturally drops out below.
      queryClient.invalidateQueries({ queryKey: QUEUE_KEY });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      const verb =
        vars.decision === 'allow'
          ? 'Approved'
          : vars.decision === 'reject'
            ? 'Denied'
            : 'Saved always-rule for';
      showToast(`${verb} ${vars.approvalId}`);
    },
    onError: (err: unknown, vars) => {
      // Roll the optimistic removal back so the row reappears.
      setOptimisticallyResolved((prev) => {
        if (!prev.has(vars.approvalId)) return prev;
        const next = new Set(prev);
        next.delete(vars.approvalId);
        return next;
      });
      const msg = err instanceof Error ? err.message : 'Could not resolve approval';
      showToast(msg, 'error');
    },
    onSettled: (_data, _err, vars) => {
      // Clear the optimistic id once the cache has had a chance to refetch so
      // we don't accidentally hide a still-pending row if the server kept it.
      setOptimisticallyResolved((prev) => {
        if (!prev.has(vars.approvalId)) return prev;
        const next = new Set(prev);
        next.delete(vars.approvalId);
        return next;
      });
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
      <div className="grid h-full grid-cols-1 overflow-hidden md:grid-cols-[440px_1fr]">
        <div className="flex flex-col border-b border-border bg-secondary p-4 md:border-b-0 md:border-r">
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
    <div className="grid h-full grid-cols-1 overflow-hidden md:grid-cols-[440px_1fr]">
      {/* LEFT - queue list. On narrow viewports we stack and cap height so the
          selected approval still gets screen real estate below. */}
      <aside className="flex max-h-[40vh] flex-col overflow-hidden border-b border-border bg-secondary md:max-h-none md:border-b-0 md:border-r">
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

      {/* RIGHT - selected approval */}
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

      {/* Inline toast - bottom-right; success auto-clears after 2s, errors 5s. */}
      <div
        aria-live={toast?.kind === 'error' ? 'assertive' : 'polite'}
        role={toast?.kind === 'error' ? 'alert' : 'status'}
        className="pointer-events-none fixed bottom-10 right-6 z-50"
      >
        {toast && (
          <div
            className={cn(
              'pointer-events-auto rounded-md border px-3 py-2 text-body shadow-md',
              toast.kind === 'error'
                ? 'border-destructive/50 bg-destructive/15 text-destructive'
                : 'border-border bg-card text-foreground',
            )}
          >
            {toast.message}
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
  // Scroll the keyboard-focused row into view and surface a visible focus ring
  // so arrow-key navigation is observable. We focus the inner button rather
  // than the <li> because buttons get the platform-native focus ring.
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!selected) return;
    const el = buttonRef.current;
    if (!el) return;
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [selected]);

  return (
    // role="option" must live on the listbox child element itself, not on a
    // descendant button - otherwise screen readers see an empty listbox.
    <li role="option" aria-selected={selected}>
      <button
        ref={buttonRef}
        type="button"
        onClick={onClick}
        className={cn(
          'group flex w-full items-start justify-between gap-2.5 border-b border-border-subtle px-4 py-3 text-left transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
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
  const diffPayload = approvalDiff(approval);
  // Only count +/- from real diff text; JSON-args fallback would otherwise
  // produce confusing "+12 −0" badges from quoted property syntax.
  const { plus, minus } =
    diffPayload.kind === 'diff' ? diffCounts(diffPayload.text) : { plus: 0, minus: 0 };
  const filename = diffFilename(approval);
  const taskId = approvalTaskId(approval);
  const target = approvalTarget(approval);
  const sloS = approval.ttl_seconds;
  const waitS = waitSeconds(approval.created_at, now);

  // Destructive policy actions get a confirmation step. We use window.confirm
  // to avoid pulling in @radix-ui/react-alert-dialog as a new dep.
  const confirmAndDecide = (
    decision: Decision,
    reason: string,
    confirmMessage: string,
  ) => {
    if (typeof window !== 'undefined' && !window.confirm(confirmMessage)) return;
    onDecision(decision, reason);
  };

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
            {diffPayload.kind === 'diff' ? (
              <span className="ml-2.5 tabular-nums text-meta-foreground">
                <span className="text-success">+{plus}</span>{' '}
                <span className="text-destructive">−{minus}</span>
              </span>
            ) : (
              <span className="ml-2.5 text-meta-foreground">
                {diffPayload.kind === 'args' ? '· tool args' : '· no diff available'}
              </span>
            )}
          </div>
        </div>
        {diffPayload.kind === 'empty' ? (
          <div className="px-3.5 py-6">
            <EmptyState
              title="No diff available"
              description="This tool call did not include a diff or patch payload."
            />
          </div>
        ) : (
          <pre
            className="m-0 max-h-[240px] overflow-auto bg-background px-3.5 py-2.5 font-mono text-log text-foreground"
            aria-label={diffPayload.kind === 'diff' ? 'Approval diff' : 'Approval tool args'}
          >
            {diffPayload.text.split('\n').map((line, i) => (
              <span
                key={i}
                className={cn(
                  'block',
                  diffPayload.kind === 'diff' &&
                    line.startsWith('+') &&
                    !line.startsWith('+++') &&
                    'bg-success/20 text-success',
                  diffPayload.kind === 'diff' &&
                    line.startsWith('-') &&
                    !line.startsWith('---') &&
                    'bg-destructive/15 text-destructive',
                )}
              >
                {line || ' '}
              </span>
            ))}
          </pre>
        )}
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
            onClick={() =>
              confirmAndDecide(
                'always',
                `policy:always_allow:tool:${approval.tool_name}`,
                `Always allow tool "${approval.tool_name}" without future review?`,
              )
            }
          >
            Always allow · this tool
          </GhostAction>
          <GhostAction
            disabled={pending}
            onClick={() =>
              confirmAndDecide(
                'always',
                `policy:always_allow:agent:${approval.agent_role}`,
                `Always allow agent "${approval.agent_role}" without future review?`,
              )
            }
          >
            Always allow · this agent
          </GhostAction>
          <GhostAction
            disabled={pending}
            onClick={() =>
              confirmAndDecide(
                // Distinguish always-deny from a one-off Deny so the backend
                // can persist a deny rule when policy support lands. The
                // structured `policy:always_deny:*` reason survives even when
                // the API still treats `decision` as the leading signal.
                'always',
                `policy:always_deny:tool:${approval.tool_name}`,
                `Always deny tool "${approval.tool_name}" for future calls? This is destructive.`,
              )
            }
          >
            Always deny · this tool
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
