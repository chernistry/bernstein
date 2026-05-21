// Single gate entry in the list. Click the header to expand the body which
// dumps the full ``details`` blob in a monospaced reading frame. Tone of the
// row hairline, name, and chip all come from the same bucket projection so
// the row reads as one cohesive unit.

import { ChevronDown, ChevronRight } from 'lucide-react';
import { useId } from 'react';

import { cn } from '@/lib/utils';

import { bucketFor, STATUS_LABEL } from './buckets';
import { GateStatusIcon } from './GateStatusIcon';
import type { GateResult } from './types';

interface Props {
  gate: GateResult;
  expanded: boolean;
  onToggle: () => void;
}

interface Tone {
  accent: string;
  pill: string;
  name: string;
  rowHover: string;
}

function toneFor(gate: GateResult): Tone {
  switch (bucketFor(gate.status)) {
    case 'failing':
      return {
        accent: 'border-l-destructive/70',
        pill: 'bg-destructive/15 text-destructive border-destructive/40',
        name: 'text-destructive',
        rowHover: 'hover:bg-destructive/[0.04]',
      };
    case 'pending':
      return {
        accent: 'border-l-warning/70',
        pill: 'bg-warning/15 text-warning border-warning/40',
        name: 'text-warning',
        rowHover: 'hover:bg-warning/[0.04]',
      };
    case 'passing':
      return {
        accent: 'border-l-success/50',
        pill: 'bg-success/10 text-success border-success/30',
        name: 'text-foreground',
        rowHover: 'hover:bg-success/[0.03]',
      };
    case 'skipped':
    default:
      return {
        accent: 'border-l-border-subtle',
        pill: 'bg-secondary text-muted-foreground border-border-subtle',
        name: 'text-muted-foreground',
        rowHover: 'hover:bg-muted/30',
      };
  }
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

export function GateRow({ gate, expanded, onToggle }: Props) {
  const tone = toneFor(gate);
  const bodyId = useId();
  const hasDetail = gate.details.trim().length > 0;
  const hasMetadata =
    gate.metadata != null && typeof gate.metadata === 'object' && Object.keys(gate.metadata).length > 0;
  const canExpand = hasDetail || hasMetadata;

  // Single-line summary that sits next to the chip when the row is collapsed.
  // We strip newlines so a multi-line ``details`` blob doesn't blow up the row
  // height; the full content lives in the expanded body.
  const summary = gate.details.split('\n', 1)[0]?.trim() ?? '';

  return (
    <li
      className={cn(
        'border-b border-border-subtle/60 border-l-2 last:border-b-0',
        tone.accent,
      )}
    >
      <button
        type="button"
        onClick={canExpand ? onToggle : undefined}
        disabled={!canExpand}
        aria-expanded={canExpand ? expanded : undefined}
        aria-controls={canExpand ? bodyId : undefined}
        className={cn(
          'group flex w-full items-start gap-2 px-3 py-2 text-left transition-colors',
          tone.rowHover,
          canExpand
            ? 'cursor-pointer focus-visible:outline-none focus-visible:bg-secondary/40'
            : 'cursor-default',
        )}
      >
        <span className="mt-0.5 flex w-4 shrink-0 items-center justify-center">
          {canExpand ? (
            expanded ? (
              <ChevronDown className="size-3.5 text-meta-foreground" aria-hidden="true" />
            ) : (
              <ChevronRight className="size-3.5 text-meta-foreground" aria-hidden="true" />
            )
          ) : (
            <span className="size-1 rounded-full bg-meta-foreground/40" aria-hidden="true" />
          )}
        </span>
        <GateStatusIcon status={gate.status} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cn('truncate font-mono text-[12px]', tone.name)}>{gate.name}</span>
            <span
              className={cn(
                'inline-flex items-center rounded-full border px-1.5 py-px font-mono text-[9.5px] uppercase tracking-[0.1em]',
                tone.pill,
              )}
            >
              {STATUS_LABEL[gate.status]}
            </span>
            {gate.required ? (
              <span
                className="inline-flex items-center rounded-full border border-border-subtle bg-card px-1.5 py-px font-mono text-[9.5px] uppercase tracking-[0.1em] text-meta-foreground"
                title="Failing this gate blocks task completion"
              >
                required
              </span>
            ) : (
              <span
                className="inline-flex items-center rounded-full border border-border-subtle bg-card px-1.5 py-px font-mono text-[9.5px] uppercase tracking-[0.1em] text-meta-foreground"
                title="Advisory gate - does not block"
              >
                advisory
              </span>
            )}
            {gate.cached && (
              <span
                className="inline-flex items-center rounded-full border border-border-subtle bg-card px-1.5 py-px font-mono text-[9.5px] uppercase tracking-[0.1em] text-meta-foreground"
                title="Result reused from the gate cache"
              >
                cached
              </span>
            )}
          </div>
          {summary && (
            <div className="mt-0.5 truncate text-[11.5px] text-muted-foreground">{summary}</div>
          )}
        </div>
        <span className="shrink-0 self-center font-mono text-[10.5px] tabular-nums text-meta-foreground">
          {formatDuration(gate.duration_ms)}
        </span>
      </button>
      {canExpand && expanded && (
        <div
          id={bodyId}
          role="region"
          aria-label={`${gate.name} output`}
          className="border-t border-border-subtle/60 bg-muted/20 px-3 py-2"
        >
          {hasDetail && (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-foreground">
              {gate.details}
            </pre>
          )}
          {hasMetadata && (
            <details className="mt-2 text-[11px]">
              <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground hover:text-foreground">
                metadata
              </summary>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px] text-muted-foreground">
                {JSON.stringify(gate.metadata, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </li>
  );
}
