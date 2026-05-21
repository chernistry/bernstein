import type { ReactNode } from 'react';

type Props = {
  name: string;
  ticket: string;
  description: string;
  /** Optional rich content rendered above the "Pending implementation" card -
   *  use for screens that want to surface a usable affordance now (e.g. the
   *  Fleet placeholder pointing operators at the topbar toggle). */
  children?: ReactNode;
};

export function PlaceholderScreen({ name, ticket, description, children }: Props) {
  return (
    <div className="max-w-3xl p-6">
      <h1 className="text-2xl font-semibold mb-2">{name}</h1>
      <p className="text-muted-foreground mb-6">{description}</p>
      {children && <div className="mb-6">{children}</div>}
      <div className="rounded-md border border-dashed border-border p-6 bg-card/50 text-sm">
        <div className="text-muted-foreground mb-1">Pending implementation</div>
        <code className="font-mono text-xs">.sdd/backlog/open/frontend/{ticket}</code>
      </div>
    </div>
  );
}
