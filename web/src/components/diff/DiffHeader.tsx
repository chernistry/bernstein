// Header strip for the Diff panel. Shows branch / base ref, totals,
// fetched-at, and the unified vs split toggle plus copy/download/refresh
// actions.

import {
  Check,
  Columns2,
  Copy,
  Download,
  GitBranch,
  Rows3,
  RefreshCcw,
  WrapText,
} from 'lucide-react';
import { useEffect, useState } from 'react';

import { cn } from '@/lib/utils';

import type { DiffViewMode } from './types';
import { formatTimestamp } from './utils';

interface Props {
  branch: string | null;
  baseRef: string;
  headRef: string | null;
  additions: number;
  deletions: number;
  fileCount: number;
  truncated: boolean;
  note: string | null;
  generatedAt: number;
  viewMode: DiffViewMode;
  onViewModeChange: (mode: DiffViewMode) => void;
  wrap: boolean;
  onWrapChange: (wrap: boolean) => void;
  loading: boolean;
  onRefresh: () => void;
  onCopy: () => Promise<boolean>;
  onDownload: () => void;
  disableActions: boolean;
}

interface FlashState {
  copied: boolean;
}

export function DiffHeader({
  branch,
  baseRef,
  headRef,
  additions,
  deletions,
  fileCount,
  truncated,
  note,
  generatedAt,
  viewMode,
  onViewModeChange,
  wrap,
  onWrapChange,
  loading,
  onRefresh,
  onCopy,
  onDownload,
  disableActions,
}: Props) {
  const [flash, setFlash] = useState<FlashState>({ copied: false });

  useEffect(() => {
    if (!flash.copied) return;
    const t = window.setTimeout(() => setFlash({ copied: false }), 1500);
    return () => window.clearTimeout(t);
  }, [flash.copied]);

  const handleCopy = async () => {
    const ok = await onCopy();
    if (ok) setFlash({ copied: true });
  };

  return (
    <div className="border-b border-border-subtle bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
          Diff
        </span>
        <div className="flex min-w-0 items-center gap-1.5">
          <GitBranch className="size-3.5 shrink-0 text-meta-foreground" aria-hidden />
          <span
            className="truncate font-mono text-[11px] text-foreground"
            title={headRef ?? 'working tree'}
          >
            {headRef ?? 'working tree'}
          </span>
          <span className="text-meta-foreground/60">→</span>
          <span className="font-mono text-[11px] text-meta-foreground" title={`base: ${baseRef}`}>
            {baseRef}
          </span>
        </div>
        <div className="flex items-center gap-2 font-mono text-[10.5px]">
          <span className="text-success">+{additions}</span>
          <span className="text-destructive">-{deletions}</span>
          <span className="text-meta-foreground">
            {fileCount} file{fileCount === 1 ? '' : 's'}
          </span>
        </div>
        {truncated && (
          <span className="rounded-sm border border-warning/30 bg-warning/10 px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-tight text-warning">
            truncated
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5 font-mono text-[10px] text-meta-foreground">
          <span className="hidden md:inline">fetched</span>
          <span>{formatTimestamp(generatedAt)}</span>
        </span>
        <div className="flex items-center gap-1">
          <div
            role="group"
            aria-label="View mode"
            className="flex h-7 items-center overflow-hidden rounded-sm border border-border-subtle bg-card"
          >
            <button
              type="button"
              onClick={() => onViewModeChange('unified')}
              aria-pressed={viewMode === 'unified'}
              title="Unified view"
              className={cn(
                'flex h-full items-center gap-1 px-2 font-mono text-[10px] uppercase tracking-tight transition-colors',
                viewMode === 'unified'
                  ? 'bg-muted text-foreground'
                  : 'text-meta-foreground hover:bg-muted/40 hover:text-foreground',
              )}
            >
              <Rows3 className="size-3" aria-hidden />
              Unified
            </button>
            <span className="h-full w-px bg-border-subtle" aria-hidden />
            <button
              type="button"
              onClick={() => onViewModeChange('split')}
              aria-pressed={viewMode === 'split'}
              title="Side-by-side view"
              className={cn(
                'flex h-full items-center gap-1 px-2 font-mono text-[10px] uppercase tracking-tight transition-colors',
                viewMode === 'split'
                  ? 'bg-muted text-foreground'
                  : 'text-meta-foreground hover:bg-muted/40 hover:text-foreground',
              )}
            >
              <Columns2 className="size-3" aria-hidden />
              Split
            </button>
          </div>
          <button
            type="button"
            onClick={() => onWrapChange(!wrap)}
            title={wrap ? 'Disable word wrap' : 'Enable word wrap'}
            aria-pressed={wrap}
            className={cn(
              'inline-flex h-7 items-center gap-1 rounded-sm border border-border-subtle px-2 font-mono text-[10px] uppercase tracking-tight transition-colors',
              wrap
                ? 'bg-muted text-foreground'
                : 'bg-card text-meta-foreground hover:bg-muted/40 hover:text-foreground',
            )}
          >
            <WrapText className="size-3" aria-hidden />
            Wrap
          </button>
          <button
            type="button"
            onClick={handleCopy}
            disabled={disableActions}
            title="Copy unified diff"
            className="inline-flex h-7 items-center gap-1 rounded-sm border border-border-subtle bg-card px-2 font-mono text-[10px] uppercase tracking-tight text-meta-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            {flash.copied ? (
              <Check className="size-3 text-success" aria-hidden />
            ) : (
              <Copy className="size-3" aria-hidden />
            )}
            {flash.copied ? 'Copied' : 'Copy'}
          </button>
          <button
            type="button"
            onClick={onDownload}
            disabled={disableActions}
            title="Download as .patch"
            className="inline-flex h-7 items-center gap-1 rounded-sm border border-border-subtle bg-card px-2 font-mono text-[10px] uppercase tracking-tight text-meta-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download className="size-3" aria-hidden />
            Patch
          </button>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            title="Reload diff"
            className="inline-flex h-7 items-center gap-1 rounded-sm border border-border-subtle bg-card px-2 font-mono text-[10px] uppercase tracking-tight text-meta-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCcw className={cn('size-3', loading && 'animate-spin')} aria-hidden />
            Refresh
          </button>
        </div>
      </div>
      {(branch || note) && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-border-subtle/60 bg-card px-3 py-1 font-mono text-[10px] text-meta-foreground">
          {branch && (
            <span>
              branch <span className="text-foreground">{branch}</span>
            </span>
          )}
          {note && <span className="text-warning/90">{note}</span>}
        </div>
      )}
    </div>
  );
}
