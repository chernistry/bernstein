// Empty / loading / error placeholders for the Diff panel. Kept tiny and
// presentational so the panel composition stays readable.

import { AlertTriangle, GitCompare, Loader2, RefreshCcw } from 'lucide-react';

import { cn } from '@/lib/utils';

export function DiffLoadingState({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'flex h-full flex-col items-center justify-center gap-2 px-6 py-12 text-center',
        className,
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex size-9 items-center justify-center rounded-full border border-border-subtle bg-card text-meta-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden />
      </div>
      <div className="text-[13px] font-medium text-foreground">Loading diff…</div>
      <div className="w-full max-w-xs">
        <div className="flex flex-col gap-1 font-mono text-[10.5px] text-meta-foreground">
          {['-- --', '-- ---- --', '--- -- ----', '-- --'].map((row, i) => (
            <div key={i} className="truncate">
              {row}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

interface ErrorProps {
  onRetry: () => void;
  message?: string;
  className?: string;
}

export function DiffErrorState({ onRetry, message, className }: ErrorProps) {
  return (
    <div
      className={cn(
        'flex h-full flex-col items-center justify-center gap-2 px-6 py-12 text-center',
        className,
      )}
      role="alert"
    >
      <div className="flex size-9 items-center justify-center rounded-full border border-destructive/30 bg-destructive/5 text-destructive">
        <AlertTriangle className="size-4" aria-hidden />
      </div>
      <div className="text-[13px] font-medium text-foreground">Failed to load diff</div>
      <div className="max-w-xs text-[11.5px] leading-relaxed text-muted-foreground">
        {message ?? 'The orchestrator could not produce a diff for this task.'}
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="mt-1 inline-flex h-7 items-center gap-1.5 rounded-sm border border-border-subtle bg-card px-2.5 font-mono text-[10.5px] uppercase tracking-tight text-foreground transition-colors hover:bg-muted"
      >
        <RefreshCcw className="size-3" aria-hidden />
        Retry
      </button>
    </div>
  );
}

export function DiffEmptyState({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'flex h-full flex-col items-center justify-center gap-2 px-6 py-12 text-center',
        className,
      )}
      role="status"
    >
      <div className="flex size-9 items-center justify-center rounded-full border border-border-subtle bg-card text-meta-foreground">
        <GitCompare className="size-4" aria-hidden />
      </div>
      <div className="text-[13px] font-medium text-foreground">No changes yet</div>
      <div className="max-w-xs text-[11.5px] leading-relaxed text-muted-foreground">
        This view fills in once the agent commits work on its worktree branch
        or modifies tracked files.
      </div>
    </div>
  );
}
