// Stub for the Diff tab — replaced by Agent C dispatch.
// See `web/src/components/logs/TaskLogsPanel.tsx` for the pattern.

export interface TaskDiffPanelProps {
  taskId: string;
  active?: boolean;
}

export function TaskDiffPanel(_props: TaskDiffPanelProps) {
  return (
    <div className="rounded-md border border-border-subtle bg-card/60 px-4 py-6 text-center text-[12.5px] text-muted-foreground">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
        Diff
      </div>
      <div className="mt-1.5">Diff panel under construction.</div>
    </div>
  );
}
