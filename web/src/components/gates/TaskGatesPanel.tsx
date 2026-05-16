// Stub for the Gates tab — replaced by Agent D dispatch.

export interface TaskGatesPanelProps {
  taskId: string;
  active?: boolean;
}

export function TaskGatesPanel(_props: TaskGatesPanelProps) {
  return (
    <div className="rounded-md border border-border-subtle bg-card/60 px-4 py-6 text-center text-[12.5px] text-muted-foreground">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
        Gates
      </div>
      <div className="mt-1.5">Gates panel under construction.</div>
    </div>
  );
}
