import { PlaceholderScreen } from '@/components/PlaceholderScreen';

export default function Settings() {
  return (
    <PlaceholderScreen
      name="Settings"
      ticket="2026-05-15-frontend-app-shell.md"
      description="Theme, density, keyboard shortcuts, auth token, fleet mode, telemetry opt-out. Backed by per-user localStorage and (where applicable) the server config."
    />
  );
}
