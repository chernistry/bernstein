type Props = {
  name: string;
  ticket: string;
  description: string;
};

export function PlaceholderScreen({ name, ticket, description }: Props) {
  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-semibold mb-2">{name}</h1>
      <p className="text-muted-foreground mb-6">{description}</p>
      <div className="rounded-md border border-dashed border-border p-6 bg-card/50 text-sm">
        <div className="text-muted-foreground mb-1">Pending implementation</div>
        <code className="font-mono text-xs">.sdd/backlog/open/frontend/{ticket}</code>
      </div>
    </div>
  );
}
