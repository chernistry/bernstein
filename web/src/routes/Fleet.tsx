import { Network } from 'lucide-react';
import { PlaceholderScreen } from '@/components/PlaceholderScreen';

const FLEET_STORAGE_KEY = 'bernstein-fleet-mode';

/**
 * Toggle the same `bernstein-fleet-mode` localStorage flag the topbar uses,
 * then reload so AppShell's `useState` initializer picks the new value up.
 *
 * AppShell owns the live toggle state; we deliberately do NOT duplicate the
 * UI here — operators land on /ui/fleet via deep-link or accidental nav and
 * we surface the canonical control instead of forking it.
 */
function enableFleetModeFromDeepLink() {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(FLEET_STORAGE_KEY, '1');
  window.location.reload();
}

export default function Fleet() {
  return (
    <PlaceholderScreen
      name="Fleet"
      ticket="2026-05-15-frontend-fleet-mode.md"
      description="Multi-project supervision view. Each Bernstein installation appears as a card with active-agent count, today's spend, and health icon. Drill-in switches the entire UI scope."
    >
      <div className="rounded-md border border-border bg-card p-4 text-sm">
        <div className="flex items-start gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-secondary text-foreground">
            <Network className="size-3.5" strokeWidth={1.5} />
          </span>
          <div className="flex-1">
            <div className="font-medium text-foreground">
              Fleet mode is a topbar toggle
            </div>
            <p className="mt-1 text-muted-foreground">
              Use the{' '}
              <span className="font-mono text-foreground">Single / Fleet</span>{' '}
              segmented control in the topbar (right of the ⌘K box) to switch
              the entire UI scope between a single installation and the fleet
              view. This screen exists only as a deep-link target.
            </p>
            <button
              type="button"
              onClick={enableFleetModeFromDeepLink}
              className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary px-2.5 py-1.5 text-[12px] font-medium text-foreground hover:bg-card"
            >
              <Network className="size-3" strokeWidth={1.5} />
              Enable fleet mode
            </button>
          </div>
        </div>
      </div>
    </PlaceholderScreen>
  );
}
