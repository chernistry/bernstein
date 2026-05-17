// Inline stats row — lines + bytes + throughput. Sits in the toolbar so the
// operator can sanity-check whether the agent is actually producing output.

import { cn } from '@/lib/utils';

import { formatBytes, formatRate } from './utils';

interface Props {
  totalLines: number;
  totalBytes: number;
  rate: number;
  className?: string;
}

export function LogStats({ totalLines, totalBytes, rate, className }: Props) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 font-mono text-[10px] tabular-nums text-meta-foreground',
        className,
      )}
      aria-label="Log statistics"
    >
      <Stat label="lines" value={totalLines.toLocaleString()} />
      <span className="opacity-30">·</span>
      <Stat label="bytes" value={formatBytes(totalBytes)} />
      <span className="opacity-30">·</span>
      <Stat label="rate" value={formatRate(rate)} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span>{value}</span>
      <span className="text-[9px] uppercase tracking-[0.12em] opacity-60">{label}</span>
    </span>
  );
}
