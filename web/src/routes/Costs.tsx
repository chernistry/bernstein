// Costs screen — KPI cards, 24h sparkline, by-adapter breakdown, top tasks.
// Per Variant A handoff §6.05. All numbers mono tabular-nums.

import { useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { apiGet, ApiError } from '@/lib/api';
import { useEventStream } from '@/lib/sse';
import { formatCount, formatTokens, formatUSD } from '@/lib/format';
import { EmptyState, ErrorState, LoadingState, Pill, SectionLabel } from '@/lib/states';
import { cn } from '@/lib/utils';

// ─────────────────────────────────────────────────────────────────────────────
// Types — match server contract documented in handoff §6.05.
// ─────────────────────────────────────────────────────────────────────────────

interface CostsCurrent {
  today_usd: number;
  week_usd: number;
  projected_month_usd: number;
  budget_usd: number;
  used_pct: number;
  prior_week_usd?: number;
  delta_hour_usd?: number;
  resets_at?: string;
  last_sync_at?: string;
}

interface CostsHistoryPoint {
  ts: string;
  usd: number;
}

interface CostsAdapterRow {
  adapter: string;
  calls: number;
  tokens: number;
  cost_usd: number;
  share_pct: number;
  delta_7d_pct: number;
}

interface CostsForecast {
  projected_month_usd: number;
  trend_label?: string;
}

interface CostsTopTask {
  id: string;
  title: string;
  agent: string;
  cost_usd: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export default function Costs() {
  const qc = useQueryClient();

  const current = useQuery<CostsCurrent>({
    queryKey: ['costs', 'current'],
    queryFn: () => apiGet<CostsCurrent>('/costs/current'),
    refetchInterval: 60_000,
  });

  const history = useQuery<CostsHistoryPoint[]>({
    queryKey: ['costs', 'history'],
    queryFn: () =>
      apiGet<CostsHistoryPoint[]>('/costs/history?hours=24&granularity=hour'),
    refetchInterval: 60_000,
  });

  const breakdown = useQuery<CostsAdapterRow[]>({
    queryKey: ['costs', 'breakdown'],
    queryFn: () => apiGet<CostsAdapterRow[]>('/costs/by-tag'),
    refetchInterval: 120_000,
  });

  const topTasks = useQuery<CostsTopTask[]>({
    queryKey: ['costs', 'top-tasks'],
    queryFn: () => apiGet<CostsTopTask[]>('/costs/top-tasks?limit=10'),
    refetchInterval: 120_000,
  });

  const forecast = useQuery<CostsForecast>({
    queryKey: ['costs', 'forecast'],
    queryFn: () => apiGet<CostsForecast>('/costs/forecast'),
    refetchInterval: 300_000,
    retry: false,
  });

  // Live tick channel — invalidate current + history on each tick.
  useEventStream('/api/v1/events/cost', {
    on: {
      cost_tick: () => {
        qc.invalidateQueries({ queryKey: ['costs', 'current'] });
        qc.invalidateQueries({ queryKey: ['costs', 'history'] });
      },
    },
  });

  const refetchAll = () => {
    current.refetch();
    history.refetch();
    breakdown.refetch();
    topTasks.refetch();
    forecast.refetch();
  };

  // Page-level error: only escalate when the *primary* query fails.
  if (current.isError) {
    return (
      <div className="mx-auto w-full max-w-7xl p-6">
        <PageHeader />
        <div className="mt-4">
          <ErrorState
            message={
              current.error instanceof ApiError
                ? current.error.message
                : 'Failed to load cost summary.'
            }
            retry={refetchAll}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 p-6">
      <PageHeader lastSync={current.data?.last_sync_at} />

      <KpiRow data={current.data} forecast={forecast.data} loading={current.isLoading} />

      <SparklineCard
        data={history.data}
        loading={history.isLoading}
        error={
          history.isError
            ? history.error instanceof ApiError
              ? history.error.message
              : 'Failed to load history.'
            : null
        }
        refetch={() => history.refetch()}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
        <AdapterCard
          rows={breakdown.data}
          loading={breakdown.isLoading}
          error={
            breakdown.isError
              ? breakdown.error instanceof ApiError
                ? breakdown.error.message
                : 'Failed to load breakdown.'
              : null
          }
          refetch={() => breakdown.refetch()}
          totalToday={current.data?.today_usd}
        />
        <TopTasksCard
          rows={topTasks.data}
          loading={topTasks.isLoading}
          error={
            topTasks.isError
              ? topTasks.error instanceof ApiError
                ? topTasks.error.message
                : 'Failed to load top tasks.'
              : null
          }
          refetch={() => topTasks.refetch()}
        />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Header
// ─────────────────────────────────────────────────────────────────────────────

function PageHeader({ lastSync }: { lastSync?: string }) {
  const syncLabel = lastSync ? formatSyncRelative(lastSync) : 'just now';
  return (
    <header className="flex flex-col gap-1">
      <h1 className="text-h1 text-foreground">Costs</h1>
      <p className="text-body text-muted-foreground">
        today / 7d / projected month ·{' '}
        <span className="font-mono tabular-nums text-meta-foreground">
          last sync {syncLabel}
        </span>
      </p>
    </header>
  );
}

function formatSyncRelative(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return 'just now';
  const deltaSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (deltaSec < 5) return 'just now';
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86_400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86_400)}d ago`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Top KPI row — 4 cards
// ─────────────────────────────────────────────────────────────────────────────

interface KpiRowProps {
  data: CostsCurrent | undefined;
  forecast: CostsForecast | undefined;
  loading: boolean;
}

function KpiRow({ data, forecast, loading }: KpiRowProps) {
  // Loading skeleton: 4 cards w/ LoadingState rows={1}.
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_1.4fr]">
        {Array.from({ length: 4 }).map((_, i) => (
          <KpiCardShell key={i} label={KPI_LABELS[i] ?? ''}>
            <LoadingState rows={1} />
          </KpiCardShell>
        ))}
      </div>
    );
  }

  // Empty: no data yet.
  if (!data || data.today_usd == null) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_1.4fr]">
        {KPI_LABELS.map((l) => (
          <KpiCardShell key={l} label={l}>
            <div className="text-stat-lg font-mono tabular-nums text-meta-foreground">
              —
            </div>
            <div className="mt-1 font-mono text-[11px] tabular-nums text-meta-foreground">
              no spend yet
            </div>
          </KpiCardShell>
        ))}
      </div>
    );
  }

  const usedPct = clampPct(data.used_pct);
  const gaugeColor =
    usedPct >= 90 ? 'bg-destructive' : usedPct >= 60 ? 'bg-warning' : 'bg-success';

  const deltaHour = data.delta_hour_usd;
  const todaySub =
    deltaHour != null
      ? `${deltaHour >= 0 ? '+' : '−'}${formatUSD(Math.abs(deltaHour))} since hour ago`
      : 'live tick · 6/min';

  const priorWeek = data.prior_week_usd;
  const weekDelta =
    priorWeek != null && priorWeek > 0
      ? ((data.week_usd - priorWeek) / priorWeek) * 100
      : null;
  const weekSub =
    priorWeek != null
      ? `vs ${formatUSD(priorWeek)} prior week${
          weekDelta != null
            ? ` (${weekDelta >= 0 ? '+' : ''}${weekDelta.toFixed(1)}%)`
            : ''
        }`
      : 'rolling 7-day window';

  const monthSub =
    forecast?.trend_label ??
    (data.projected_month_usd <= data.budget_usd * 30
      ? 'trend within budget'
      : 'trending above budget');

  const resets = data.resets_at ? `resets ${formatResetsAt(data.resets_at)}` : 'resets 04:00 UTC';

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_1.4fr]">
      <KpiCardShell label="today">
        <div className="text-stat-lg font-mono tabular-nums text-foreground">
          {formatUSD(data.today_usd)}
        </div>
        <div className="mt-1.5 font-mono text-[11px] tabular-nums text-meta-foreground">
          {todaySub}
        </div>
      </KpiCardShell>

      <KpiCardShell label="7-day">
        <div className="text-stat-lg font-mono tabular-nums text-foreground">
          {formatUSD(data.week_usd)}
        </div>
        <div className="mt-1.5 font-mono text-[11px] tabular-nums text-meta-foreground">
          {weekSub}
        </div>
      </KpiCardShell>

      <KpiCardShell label="projected month">
        <div className="text-stat-lg font-mono tabular-nums text-foreground">
          {formatUSD(data.projected_month_usd)}
        </div>
        <div className="mt-1.5 font-mono text-[11px] tabular-nums text-meta-foreground">
          {monthSub}
        </div>
      </KpiCardShell>

      <KpiCardShell label="daily budget">
        <div className="text-stat-lg font-mono tabular-nums text-accent">
          {formatUSD(data.today_usd)}{' '}
          <span className="text-meta-foreground">/ {formatUSD(data.budget_usd)}</span>
        </div>
        <div className="mt-2 h-[5px] w-full overflow-hidden rounded-sm bg-border-subtle">
          <div
            className={cn('h-full rounded-sm transition-[width]', gaugeColor)}
            style={{ width: `${usedPct}%` }}
          />
        </div>
        <div className="mt-1.5 font-mono text-[11px] tabular-nums text-meta-foreground">
          {usedPct.toFixed(0)}% used · {resets}
        </div>
      </KpiCardShell>
    </div>
  );
}

const KPI_LABELS = ['today', '7-day', 'projected month', 'daily budget'] as const;

function KpiCardShell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <SectionLabel className="mb-1.5">{label}</SectionLabel>
      {children}
    </div>
  );
}

function clampPct(n: number | null | undefined): number {
  if (n == null || Number.isNaN(n)) return 0;
  return Math.max(0, Math.min(100, n));
}

function formatResetsAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${hh}:${mm} UTC`;
}

// ─────────────────────────────────────────────────────────────────────────────
// 24h sparkline — recharts BarChart
// ─────────────────────────────────────────────────────────────────────────────

interface SparklineCardProps {
  data: CostsHistoryPoint[] | undefined;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

function SparklineCard({ data, loading, error, refetch }: SparklineCardProps) {
  const series = useMemo(() => {
    if (!data) return [];
    return data.map((p, i) => ({
      ts: p.ts,
      usd: p.usd,
      isLast: i === data.length - 1,
    }));
  }, [data]);

  const total = useMemo(
    () => series.reduce((acc, p) => acc + (p.usd ?? 0), 0),
    [series],
  );

  const peak = useMemo(() => {
    if (!series.length) return null;
    return series.reduce(
      (best, p) => (p.usd > best.usd ? p : best),
      series[0],
    );
  }, [series]);

  const peakLabel = peak ? formatHourLabel(peak.ts) : null;

  return (
    <div className="rounded-md border border-border bg-card p-4">
      <header className="mb-3 flex items-start justify-between">
        <div>
          <SectionLabel>last 24 hours · $/hour</SectionLabel>
          {loading ? (
            <div className="mt-1 h-7 w-32 animate-pulse rounded-sm bg-muted/60" />
          ) : (
            <div className="mt-1 flex items-baseline gap-3">
              <span className="text-stat-lg font-mono tabular-nums text-foreground">
                {formatUSD(total)}
              </span>
              {peak && (
                <span className="font-mono text-[11px] tabular-nums text-success">
                  ↑ peak {peakLabel} · {formatUSD(peak.usd)}/hr
                </span>
              )}
            </div>
          )}
        </div>
        <Pill kind="ghost">live · 6 ticks/min</Pill>
      </header>

      {loading ? (
        <div className="h-16 w-full animate-pulse rounded-sm bg-muted/40" />
      ) : error ? (
        <ErrorState message={error} retry={refetch} />
      ) : series.length === 0 ? (
        <EmptyState title="No data" description="No cost ticks recorded for the last 24h." />
      ) : (
        <>
          <div className="h-24 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={series}
                margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
                barCategoryGap={2}
              >
                <CartesianGrid
                  strokeDasharray="2 4"
                  stroke="hsl(var(--border-subtle))"
                  vertical={false}
                  horizontalPoints={[18, 36, 54]}
                />
                <XAxis dataKey="ts" hide />
                <YAxis hide />
                <Tooltip
                  cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
                  contentStyle={{
                    background: 'hsl(var(--popover))',
                    border: '1px solid hsl(var(--border))',
                    borderRadius: 6,
                    fontSize: 11,
                    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                    color: 'hsl(var(--foreground))',
                    padding: '6px 8px',
                  }}
                  labelFormatter={(label) => formatHourLabel(String(label))}
                  formatter={(value) => [formatUSD(Number(value)), 'spend']}
                  separator=" "
                />
                <Bar
                  dataKey="usd"
                  maxBarSize={8}
                  isAnimationActive={false}
                  radius={[1, 1, 0, 0]}
                >
                  {series.map((p, i) => (
                    <Cell
                      key={i}
                      fill={
                        p.isLast
                          ? 'hsl(var(--accent))'
                          : 'hsl(var(--foreground) / 0.78)'
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10px] tabular-nums text-meta-foreground">
            <span>−24h</span>
            <span>−18h</span>
            <span>−12h</span>
            <span>−6h</span>
            <span>now</span>
          </div>
        </>
      )}
    </div>
  );
}

function formatHourLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// By-adapter table
// ─────────────────────────────────────────────────────────────────────────────

interface AdapterCardProps {
  rows: CostsAdapterRow[] | undefined;
  loading: boolean;
  error: string | null;
  refetch: () => void;
  totalToday: number | undefined;
}

function AdapterCard({ rows, loading, error, refetch, totalToday }: AdapterCardProps) {
  const headerRight =
    rows && rows.length > 0 && totalToday != null
      ? `${formatUSD(totalToday)} across ${formatCount(rows.length)} adapters`
      : 'Drill down · Export breakdown';

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <header className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-body-md text-foreground">By adapter · last 24h</span>
        <span className="font-mono text-[11px] tabular-nums text-meta-foreground">
          {headerRight}
        </span>
      </header>

      {loading ? (
        <div className="p-4">
          <LoadingState rows={6} />
        </div>
      ) : error ? (
        <div className="p-4">
          <ErrorState message={error} retry={refetch} />
        </div>
      ) : !rows || rows.length === 0 ? (
        <div className="p-4">
          <EmptyState title="No data" description="No adapter spend recorded yet." />
        </div>
      ) : (
        <table className="w-full border-collapse">
          <thead className="bg-secondary">
            <tr>
              <Th align="left">Adapter</Th>
              <Th align="right">Calls</Th>
              <Th align="right">Tokens</Th>
              <Th align="right">Cost</Th>
              <Th align="left">Share</Th>
              <Th align="right">Δ 7d</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <AdapterRow key={r.adapter} row={r} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AdapterRow({ row }: { row: CostsAdapterRow }) {
  const share = clampPct(row.share_pct);
  const delta = row.delta_7d_pct ?? 0;
  const deltaLabel =
    delta === 0 ? '±0%' : `${delta > 0 ? '+' : '−'}${Math.abs(delta).toFixed(0)}%`;
  const deltaColor =
    delta < 0
      ? 'text-success'
      : delta > 20
        ? 'text-warning'
        : 'text-meta-foreground';

  return (
    <tr className="border-b border-border-subtle last:border-b-0">
      <Td className="font-mono text-body text-foreground">{row.adapter}</Td>
      <Td align="right" className="font-mono tabular-nums text-body text-foreground">
        {formatCount(row.calls)}
      </Td>
      <Td
        align="right"
        className="font-mono tabular-nums text-body text-muted-foreground"
      >
        {formatTokens(row.tokens)}
      </Td>
      <Td
        align="right"
        className="font-mono tabular-nums text-body font-medium text-foreground"
      >
        {formatUSD(row.cost_usd)}
      </Td>
      <Td>
        <div className="flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-sm bg-border-subtle">
            <div
              className="h-full rounded-sm bg-accent"
              style={{ width: `${share}%` }}
            />
          </div>
          <span className="w-10 text-right font-mono text-[10.5px] tabular-nums text-meta-foreground">
            {share.toFixed(1)}%
          </span>
        </div>
      </Td>
      <Td
        align="right"
        className={cn(
          'font-mono text-[11.5px] tabular-nums',
          deltaColor,
        )}
      >
        {deltaLabel}
      </Td>
    </tr>
  );
}

function Th({
  children,
  align = 'left',
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
}) {
  return (
    <th
      className={cn(
        'border-b border-border px-4 py-2 font-mono text-[10.5px] uppercase tracking-widest text-meta-foreground',
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
  children: React.ReactNode;
  align?: 'left' | 'right';
  className?: string;
}) {
  return (
    <td
      className={cn(
        'px-4 py-2.5',
        align === 'right' ? 'text-right' : 'text-left',
        className,
      )}
    >
      {children}
    </td>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Top tasks card
// ─────────────────────────────────────────────────────────────────────────────

interface TopTasksCardProps {
  rows: CostsTopTask[] | undefined;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

function TopTasksCard({ rows, loading, error, refetch }: TopTasksCardProps) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <header className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="text-body-md text-foreground">Top 10 tasks · 24h</span>
        <span className="font-mono text-[11px] tabular-nums text-meta-foreground">
          by spend · View forecast detail
        </span>
      </header>

      {loading ? (
        <div className="p-4">
          <LoadingState rows={6} />
        </div>
      ) : error ? (
        <div className="p-4">
          <ErrorState message={error} retry={refetch} />
        </div>
      ) : !rows || rows.length === 0 ? (
        <div className="p-4">
          <EmptyState title="No data" description="No task spend recorded yet." />
        </div>
      ) : (
        <ol className="divide-y divide-border-subtle">
          {rows.slice(0, 10).map((t, i) => (
            <li
              key={t.id}
              className="grid grid-cols-[28px_1fr_auto] items-center gap-3 px-4 py-2.5"
            >
              <span className="font-mono text-[10.5px] tabular-nums text-meta-foreground">
                {String(i + 1).padStart(2, '0')}
              </span>
              <div className="min-w-0">
                <div className="truncate text-body text-foreground">{t.title}</div>
                <div className="mt-0.5 truncate font-mono text-[10.5px] tabular-nums text-meta-foreground">
                  {t.id} · {t.agent}
                </div>
              </div>
              <span className="font-mono text-body-md tabular-nums font-medium text-foreground">
                {formatUSD(t.cost_usd)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
