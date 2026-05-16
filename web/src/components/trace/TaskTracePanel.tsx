// Stub for the Trace tab — replaced by Agent F dispatch.

export interface TaskTracePanelProps {
  taskId: string;
  active?: boolean;
}

export function TaskTracePanel(_props: TaskTracePanelProps) {
  return (
    <div className="rounded-md border border-border-subtle bg-card/60 px-4 py-6 text-center text-[12.5px] text-muted-foreground">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
        Trace
      </div>
      <div className="mt-1.5">Trace panel under construction.</div>
    </div>
  );
}
