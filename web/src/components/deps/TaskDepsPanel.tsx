// Stub for the Deps tab — replaced by Agent E dispatch.

export interface TaskDepsPanelProps {
  taskId: string;
  active?: boolean;
}

export function TaskDepsPanel(_props: TaskDepsPanelProps) {
  return (
    <div className="rounded-md border border-border-subtle bg-card/60 px-4 py-6 text-center text-[12.5px] text-muted-foreground">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
        Deps
      </div>
      <div className="mt-1.5">Deps panel under construction.</div>
    </div>
  );
}
