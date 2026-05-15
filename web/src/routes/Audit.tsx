// Audit screen — HMAC-chained log with chain banner, filters, table, verify modal, paginated polling.
// Visual reference: design_handoff_bernstein_phase1/design-source/screens/screen-audit.jsx.
// Spec: design_handoff_bernstein_phase1/README.md §6.04 + §8 (states contract).

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  ChevronDown,
  Download,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react';
import { apiGet, apiPost } from '@/lib/api';
import { formatCount, formatRelative, truncateHash } from '@/lib/format';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Pill,
  StatusDot,
} from '@/lib/states';
import { cn } from '@/lib/utils';

type AuditEvent = {
  id: string;
  ts: string;
  actor: string;
  action: string;
  resource: string;
  hash: string;
  prev_hash?: string | null;
  chain_status?: 'verified' | 'rebuilt' | 'broken';
  event_type?: string;
};

type AuditPage = {
  items: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
};

type ChainVerify = {
  status: 'verified' | 'rebuilt' | 'broken';
  head_id: string;
  head_hash: string;
  total_entries: number;
  last_verified_at: string;
  walked_from?: string | null;
  walked_to?: string | null;
  rotated_at?: string | null;
  rotated_chunk?: number | null;
  sigstore?: {
    status: 'ok' | 'pending' | 'missing';
    rekor_log_index?: number | null;
    rekor_uuid?: string | null;
  } | null;
};

const PAGE_SIZE_DEFAULT = 25;
const OPERATOR_PREFIX = 'operator';

function isOperator(actor: string): boolean {
  return actor.toLowerCase().startsWith(OPERATOR_PREFIX);
}

function buildAuditPath(params: URLSearchParams): string {
  const q = new URLSearchParams();
  for (const k of ['search', 'actor', 'event_type', 'from', 'to'] as const) {
    const v = params.get(k);
    if (v) q.set(k, v);
  }
  q.set('page', params.get('page') ?? '1');
  q.set('page_size', params.get('page_size') ?? String(PAGE_SIZE_DEFAULT));
  return `/audit?${q.toString()}`;
}

export default function Audit() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const exportRef = useRef<HTMLDivElement>(null);

  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1);
  const pageSize = Math.max(
    1,
    Number(searchParams.get('page_size') ?? String(PAGE_SIZE_DEFAULT)) || PAGE_SIZE_DEFAULT,
  );

  // Local filter form state — committed to URL on change/reset.
  const [searchInput, setSearchInput] = useState(searchParams.get('search') ?? '');
  const [actorInput, setActorInput] = useState(searchParams.get('actor') ?? '');
  const [actionInput, setActionInput] = useState(searchParams.get('event_type') ?? '');
  const [fromInput, setFromInput] = useState(searchParams.get('from') ?? '');
  const [toInput, setToInput] = useState(searchParams.get('to') ?? '');

  useEffect(() => {
    setSearchInput(searchParams.get('search') ?? '');
    setActorInput(searchParams.get('actor') ?? '');
    setActionInput(searchParams.get('event_type') ?? '');
    setFromInput(searchParams.get('from') ?? '');
    setToInput(searchParams.get('to') ?? '');
  }, [searchParams]);

  // Close export dropdown on outside click.
  useEffect(() => {
    if (!exportMenuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportMenuOpen(false);
      }
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, [exportMenuOpen]);

  const path = useMemo(() => buildAuditPath(searchParams), [searchParams]);

  const auditQuery = useQuery<AuditPage>({
    queryKey: ['audit', path],
    queryFn: () => apiGet<AuditPage>(path),
    placeholderData: (prev) => prev,
  });

  const verifyQuery = useQuery<ChainVerify>({
    queryKey: ['audit', 'verify'],
    queryFn: () => apiGet<ChainVerify>('/audit/verify'),
    refetchInterval: 60_000,
  });

  const reverifyMutation = useMutation({
    mutationFn: (chunk: number | null) =>
      apiPost<ChainVerify>('/audit/verify', chunk != null ? { from_chunk: chunk } : {}),
    onSuccess: (data) => {
      // Refetch verify + audit list so the banner reflects the new walk.
      verifyQuery.refetch();
      auditQuery.refetch();
      return data;
    },
  });

  // Blob download — POST /audit/export?format=… returns binary; bypass JSON parsing.
  const exportMutation = useMutation({
    mutationFn: async (format: 'csv' | 'jsonl') => {
      const exportParams = new URLSearchParams();
      for (const k of ['search', 'actor', 'event_type', 'from', 'to'] as const) {
        const v = searchParams.get(k);
        if (v) exportParams.set(k, v);
      }
      exportParams.set('format', format);
      const token =
        typeof window !== 'undefined' ? window.localStorage.getItem('bernstein_token') : null;
      const res = await fetch(`/api/v1/audit/export?${exportParams.toString()}`, {
        method: 'POST',
        headers: {
          Accept: format === 'csv' ? 'text/csv' : 'application/x-ndjson',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
      if (!res.ok) throw new Error(`Export failed: ${res.status} ${res.statusText}`);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = `audit-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
      return format;
    },
  });

  const downloadExport = (format: 'csv' | 'jsonl') => {
    setExportMenuOpen(false);
    exportMutation.mutate(format);
  };

  const setParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === '') next.delete(k);
      else next.set(k, v);
    }
    setSearchParams(next, { replace: false });
  };

  const applyFilters = (e?: React.FormEvent) => {
    e?.preventDefault();
    setParams({
      search: searchInput || null,
      actor: actorInput || null,
      event_type: actionInput || null,
      from: fromInput || null,
      to: toInput || null,
      page: '1',
    });
  };

  const resetFilters = () => {
    setSearchInput('');
    setActorInput('');
    setActionInput('');
    setFromInput('');
    setToInput('');
    setParams({
      search: null,
      actor: null,
      event_type: null,
      from: null,
      to: null,
      page: '1',
    });
  };

  const data = auditQuery.data;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = total > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 1;
  const isLoading = auditQuery.isPending;
  const isError = auditQuery.isError && !auditQuery.data;
  const isEmpty = !isLoading && !isError && items.length === 0;

  const goPage = (next: number) => {
    const clamped = Math.max(1, Math.min(totalPages, next));
    setParams({ page: String(clamped) });
  };

  return (
    <div className="h-full overflow-auto">
      <div className="px-[22px] py-[18px]">
        {/* Header */}
        <div className="mb-4 flex items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-h1 text-foreground">Audit log</h1>
            <div className="mt-1.5 font-mono text-[12px] text-muted-foreground">
              HMAC-chained ·{' '}
              <span>
                last sync {verifyQuery.data ? formatRelative(verifyQuery.data.last_verified_at) : 'just now'}
              </span>
              {verifyQuery.data && (
                <>
                  {' · '}
                  <span className="tabular-nums">{formatCount(verifyQuery.data.total_entries)}</span>{' '}
                  entries
                </>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => auditQuery.refetch()}
              className="grid size-8 place-items-center rounded-md border border-border bg-card text-muted-foreground hover:text-foreground"
              aria-label="Refresh"
              title="Refresh"
            >
              <RefreshCw
                className={cn('size-3.5', auditQuery.isFetching && 'animate-spin')}
                strokeWidth={1.5}
              />
            </button>

            <div className="relative" ref={exportRef}>
              <button
                type="button"
                onClick={() => setExportMenuOpen((v) => !v)}
                className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-[12px] text-foreground hover:bg-secondary"
                aria-haspopup="menu"
                aria-expanded={exportMenuOpen}
              >
                <Download className="size-3.5" strokeWidth={1.5} />
                Export
                <ChevronDown className="size-3" strokeWidth={1.5} />
              </button>
              {exportMenuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-9 z-30 w-44 rounded-md border border-border bg-popover py-1 shadow-md"
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => downloadExport('csv')}
                    className="flex w-full items-center justify-between px-3 py-2 text-left text-[13px] text-popover-foreground hover:bg-secondary"
                  >
                    Export CSV
                    <span className="font-mono text-[10px] text-meta-foreground">.csv</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => downloadExport('jsonl')}
                    className="flex w-full items-center justify-between px-3 py-2 text-left text-[13px] text-popover-foreground hover:bg-secondary"
                  >
                    Export JSONL
                    <span className="font-mono text-[10px] text-meta-foreground">.jsonl</span>
                  </button>
                </div>
              )}
            </div>

            <button
              type="button"
              onClick={() => setVerifyOpen(true)}
              className="flex items-center gap-1.5 rounded-md border border-primary bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground hover:bg-primary/90"
            >
              <ShieldCheck className="size-3.5" strokeWidth={1.5} />
              Verify chain
            </button>
          </div>
        </div>

        {/* Chain status banner */}
        <ChainStatusBanner verify={verifyQuery} />

        {/* Filters */}
        <form
          onSubmit={applyFilters}
          className="mb-3 grid grid-cols-[1.4fr_1fr_1fr_1.2fr_auto] items-stretch gap-0 rounded-md border border-border bg-card p-2.5"
        >
          <FilterField
            label="search"
            divider
            input={
              <div className="flex items-center gap-1.5">
                <Search className="size-3 text-meta-foreground" strokeWidth={1.5} />
                <input
                  type="text"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  onBlur={applyFilters}
                  placeholder="actor:claude action:patch.* T-0419"
                  className="w-full bg-transparent font-mono text-[12px] text-foreground placeholder:text-meta-foreground focus:outline-none"
                />
              </div>
            }
          />
          <FilterField
            label="actor"
            divider
            input={
              <input
                type="text"
                value={actorInput}
                onChange={(e) => setActorInput(e.target.value)}
                onBlur={applyFilters}
                placeholder="all actors"
                className="w-full bg-transparent font-mono text-[12px] text-foreground placeholder:text-meta-foreground focus:outline-none"
              />
            }
          />
          <FilterField
            label="action"
            divider
            input={
              <input
                type="text"
                value={actionInput}
                onChange={(e) => setActionInput(e.target.value)}
                onBlur={applyFilters}
                placeholder="all actions"
                className="w-full bg-transparent font-mono text-[12px] text-foreground placeholder:text-meta-foreground focus:outline-none"
              />
            }
          />
          <FilterField
            label="time"
            input={
              <div className="flex items-center gap-1.5">
                <input
                  type="datetime-local"
                  value={fromInput}
                  onChange={(e) => setFromInput(e.target.value)}
                  onBlur={applyFilters}
                  aria-label="from"
                  className="w-full min-w-0 bg-transparent font-mono text-[11.5px] text-foreground focus:outline-none"
                />
                <span className="font-mono text-[10px] text-meta-foreground">→</span>
                <input
                  type="datetime-local"
                  value={toInput}
                  onChange={(e) => setToInput(e.target.value)}
                  onBlur={applyFilters}
                  aria-label="to"
                  className="w-full min-w-0 bg-transparent font-mono text-[11.5px] text-foreground focus:outline-none"
                />
              </div>
            }
          />
          <div className="flex items-center justify-end pl-2">
            <button
              type="button"
              onClick={resetFilters}
              className="rounded-md px-2.5 py-1 text-[12px] text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              Reset filters
            </button>
          </div>
        </form>

        {/* Table */}
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <table className="w-full table-fixed border-collapse">
            <colgroup>
              <col style={{ width: 200 }} />
              <col style={{ width: 180 }} />
              <col style={{ width: 150 }} />
              <col />
              <col style={{ width: 120 }} />
              <col style={{ width: 110 }} />
            </colgroup>
            <thead className="bg-secondary">
              <tr className="text-left">
                <Th>Timestamp</Th>
                <Th>Actor</Th>
                <Th>Action</Th>
                <Th>Resource</Th>
                <Th>Hash</Th>
                <Th>Chain</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {isError && (
                <tr>
                  <td colSpan={6} className="p-4">
                    <ErrorState
                      message={(auditQuery.error as Error | null)?.message ?? 'Failed to load audit log'}
                      retry={() => auditQuery.refetch()}
                    />
                  </td>
                </tr>
              )}
              {isLoading && !isError && (
                <tr>
                  <td colSpan={6} className="p-4">
                    <LoadingState rows={10} />
                  </td>
                </tr>
              )}
              {isEmpty && (
                <tr>
                  <td colSpan={6} className="p-4">
                    <EmptyState
                      title="Audit log empty"
                      description="No events recorded yet for this run."
                    />
                  </td>
                </tr>
              )}
              {!isLoading &&
                !isError &&
                items.map((row) => {
                  const operator = isOperator(row.actor);
                  const verified = (row.chain_status ?? 'verified') === 'verified';
                  return (
                    <tr key={row.id ?? row.hash + row.ts} className="h-[42px] hover:bg-secondary/40">
                      <Td className="font-mono text-[11.5px] tabular-nums text-foreground">
                        {row.ts}
                      </Td>
                      <Td
                        className={cn(
                          'font-mono text-[11.5px]',
                          operator ? 'text-foreground' : 'text-muted-foreground',
                        )}
                      >
                        {row.actor}
                      </Td>
                      <Td className="font-mono text-[11.5px] text-foreground">{row.action}</Td>
                      <Td className="truncate text-[12px] text-foreground">{row.resource}</Td>
                      <Td className="font-mono text-[11px] text-meta-foreground">
                        {truncateHash(row.hash, 8)}
                      </Td>
                      <Td>
                        <span className="inline-flex items-center gap-1.5 text-[11px]">
                          <StatusDot kind={verified ? 'done' : 'stalled'} />
                          <span className={cn(verified ? 'text-success' : 'text-warning')}>
                            {verified ? 'verified' : 'rebuilt'}
                          </span>
                        </span>
                      </Td>
                    </tr>
                  );
                })}
            </tbody>
          </table>

          {/* Pagination footer */}
          {!isLoading && !isError && !isEmpty && (
            <div className="flex items-center justify-between border-t border-border bg-secondary px-3.5 py-2.5 font-mono text-[11px] text-muted-foreground">
              <span>
                showing{' '}
                <span className="tabular-nums text-foreground">{formatCount(items.length)}</span>{' '}
                of <span className="tabular-nums">{formatCount(total)}</span>
              </span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => goPage(page - 1)}
                  disabled={page <= 1}
                  className="rounded-md border border-border-subtle bg-card px-2.5 py-1 text-[11.5px] text-foreground disabled:cursor-not-allowed disabled:opacity-40 hover:bg-secondary"
                >
                  Previous
                </button>
                <span className="px-2 text-[11.5px] tabular-nums text-meta-foreground">
                  Page <span className="text-foreground">{formatCount(page)}</span> of{' '}
                  {formatCount(totalPages)}
                </span>
                <button
                  type="button"
                  onClick={() => goPage(page + 1)}
                  disabled={page >= totalPages}
                  className="rounded-md border border-border bg-card px-2.5 py-1 text-[11.5px] text-foreground disabled:cursor-not-allowed disabled:opacity-40 hover:bg-secondary"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {verifyOpen && (
        <VerifyChainModal
          verify={verifyQuery.data ?? null}
          loading={verifyQuery.isPending}
          error={verifyQuery.isError ? (verifyQuery.error as Error).message : null}
          reverifying={reverifyMutation.isPending}
          onReverify={(chunk) => reverifyMutation.mutate(chunk)}
          onClose={() => setVerifyOpen(false)}
        />
      )}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="border-b border-border px-3 py-2 text-left font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
      {children}
    </th>
  );
}

function Td({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <td className={cn('px-3 py-2 align-middle', className)}>
      <div className="truncate">{children}</div>
    </td>
  );
}

function FilterField({
  label,
  input,
  divider = false,
}: {
  label: string;
  input: React.ReactNode;
  divider?: boolean;
}) {
  return (
    <div
      className={cn(
        'flex min-w-0 flex-col gap-1 px-2.5 py-1',
        divider && 'border-r border-border-subtle',
      )}
    >
      <span className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-meta-foreground">
        {label}
      </span>
      {input}
    </div>
  );
}

interface ChainStatusBannerProps {
  verify: ReturnType<typeof useQuery<ChainVerify>>;
}

function ChainStatusBanner({ verify }: ChainStatusBannerProps) {
  const containerCls =
    'mb-3.5 grid grid-cols-1 gap-0 rounded-md border border-border bg-card p-3.5 sm:grid-cols-2 lg:grid-cols-4';

  if (verify.isPending) {
    return (
      <div className={containerCls}>
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className={cn('px-4', i > 0 && 'lg:border-l lg:border-border-subtle')}
          >
            <LoadingState rows={1} />
          </div>
        ))}
      </div>
    );
  }

  if (verify.isError || !verify.data) {
    return (
      <div className="mb-3.5">
        <ErrorState
          message={
            (verify.error as Error | null)?.message ?? 'Failed to load chain verification status'
          }
          retry={() => verify.refetch()}
        />
      </div>
    );
  }

  const v = verify.data;
  const chainOk = v.status === 'verified';
  const sigOk = v.sigstore?.status === 'ok';
  const rekorRef =
    v.sigstore?.rekor_uuid ??
    (v.sigstore?.rekor_log_index != null ? `entry ${v.sigstore.rekor_log_index}` : '—');

  const cells: Array<{
    label: string;
    value: React.ReactNode;
    sub: React.ReactNode;
  }> = [
    {
      label: 'chain status',
      value: (
        <Pill kind={chainOk ? 'success' : 'warning'}>
          {chainOk ? '✓ verified' : v.status}
        </Pill>
      ),
      sub: (
        <span>
          rebuilt {v.last_verified_at ? formatRelative(v.last_verified_at) : '—'}
        </span>
      ),
    },
    {
      label: 'head',
      value: (
        <span className="font-mono text-[18px] font-medium tabular-nums text-foreground">
          #{formatCount(Number(v.head_id) || 0)}
        </span>
      ),
      sub: <span className="font-mono">{truncateHash(v.head_hash, 8)}</span>,
    },
    {
      label: 'sigstore anchor',
      value: (
        <span
          className={cn(
            'font-mono text-[14px]',
            sigOk ? 'text-success' : 'text-muted-foreground',
          )}
        >
          {sigOk ? 'ok' : v.sigstore?.status ?? '—'}
        </span>
      ),
      sub: <span className="truncate font-mono">{rekorRef}</span>,
    },
    {
      label: 'rotated',
      value: (
        <span className="font-mono text-[18px] font-medium tabular-nums text-foreground">
          {v.rotated_at ? formatRelative(v.rotated_at) : '—'}
        </span>
      ),
      sub: (
        <span className="font-mono">
          {v.rotated_chunk != null ? `chunk #${formatCount(v.rotated_chunk)}` : '—'}
        </span>
      ),
    },
  ];

  return (
    <div className={containerCls}>
      {cells.map((c, i) => (
        <div
          key={c.label}
          className={cn('px-4', i > 0 && 'lg:border-l lg:border-border-subtle')}
        >
          <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
            {c.label}
          </div>
          <div className="mt-1.5 flex items-baseline gap-2">{c.value}</div>
          <div className="mt-1 truncate font-mono text-[10.5px] text-meta-foreground">
            {c.sub}
          </div>
        </div>
      ))}
    </div>
  );
}

interface VerifyChainModalProps {
  verify: ChainVerify | null;
  loading: boolean;
  error: string | null;
  reverifying: boolean;
  onReverify: (chunk: number | null) => void;
  onClose: () => void;
}

function VerifyChainModal({
  verify,
  loading,
  error,
  reverifying,
  onReverify,
  onClose,
}: VerifyChainModalProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const reverifyChunk = verify?.rotated_chunk ?? null;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-foreground/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="verify-chain-title"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-md border border-border bg-card shadow-lg">
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="size-4 text-foreground" strokeWidth={1.5} />
            <h2 id="verify-chain-title" className="text-h3 text-foreground">
              Verify chain
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"
            aria-label="Close"
          >
            <X className="size-3.5" strokeWidth={1.5} />
          </button>
        </div>

        <div className="space-y-3 px-4 py-3.5">
          {loading && <LoadingState rows={4} />}
          {error && !loading && (
            <ErrorState message={error} retry={() => onReverify(null)} />
          )}
          {!loading && !error && verify && (
            <>
              <KV label="status">
                <Pill kind={verify.status === 'verified' ? 'success' : 'warning'}>
                  {verify.status === 'verified' ? '✓ verified' : verify.status}
                </Pill>
              </KV>
              <KV label="last verified head">
                <span className="font-mono text-[12px] tabular-nums text-foreground">
                  #{formatCount(Number(verify.head_id) || 0)} ·{' '}
                  {truncateHash(verify.head_hash, 10)}
                </span>
              </KV>
              <KV label="last verified at">
                <span className="font-mono text-[12px] text-foreground">
                  {verify.last_verified_at
                    ? `${formatRelative(verify.last_verified_at)} (${verify.last_verified_at})`
                    : '—'}
                </span>
              </KV>
              <KV label="walked range">
                <span className="font-mono text-[12px] text-foreground">
                  {verify.walked_from && verify.walked_to
                    ? `${truncateHash(verify.walked_from, 8)} → ${truncateHash(
                        verify.walked_to,
                        8,
                      )}`
                    : '—'}
                </span>
              </KV>
              <KV label="sigstore anchor">
                <span className="font-mono text-[12px] text-foreground">
                  {verify.sigstore
                    ? `${verify.sigstore.status}${
                        verify.sigstore.rekor_uuid
                          ? ` · ${truncateHash(verify.sigstore.rekor_uuid, 10)}`
                          : verify.sigstore.rekor_log_index != null
                            ? ` · entry ${verify.sigstore.rekor_log_index}`
                            : ''
                      }`
                    : '—'}
                </span>
              </KV>
              <KV label="rotated chunk">
                <span className="font-mono text-[12px] text-foreground">
                  {verify.rotated_chunk != null
                    ? `#${formatCount(verify.rotated_chunk)}${
                        verify.rotated_at ? ` · ${formatRelative(verify.rotated_at)}` : ''
                      }`
                    : '—'}
                </span>
              </KV>
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border-subtle bg-secondary px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-card px-3 py-1.5 text-[12px] text-foreground hover:bg-secondary"
          >
            Close
          </button>
          <button
            type="button"
            onClick={() => onReverify(reverifyChunk)}
            disabled={reverifying || (!verify && !error)}
            className="flex items-center gap-1.5 rounded-md border border-primary bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <ShieldCheck className="size-3.5" strokeWidth={1.5} />
            {reverifying
              ? 'Re-verifying…'
              : reverifyChunk != null
                ? `Re-verify from chunk #${formatCount(reverifyChunk)}`
                : 'Re-verify chain'}
          </button>
        </div>
      </div>
    </div>
  );
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] items-center gap-3">
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
        {label}
      </span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
