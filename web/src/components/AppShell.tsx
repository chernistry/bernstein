import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  Activity,
  DollarSign,
  ListChecks,
  Moon,
  Network,
  ScrollText,
  Settings as SettingsIcon,
  ShieldCheck,
  Sun,
  User,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from './ThemeProvider';

type GuiMeta = { version: string; commit: string; build_time: string };

// 5 sidebar items (Hick's Law ceiling). Settings → user menu, Fleet → topbar toggle.
const NAV = [
  { to: '/tasks', label: 'Tasks', icon: ListChecks },
  { to: '/agents', label: 'Agents', icon: Activity },
  { to: '/approvals', label: 'Approvals', icon: ShieldCheck },
  { to: '/audit', label: 'Audit', icon: ScrollText },
  { to: '/costs', label: 'Costs', icon: DollarSign },
] as const;

export default function AppShell({ children }: { children: ReactNode }) {
  const { theme, setTheme } = useTheme();
  const location = useLocation();
  const [meta, setMeta] = useState<GuiMeta | null>(null);
  const [fleetMode, setFleetMode] = useState<boolean>(
    () => typeof window !== 'undefined' && window.localStorage.getItem('bernstein-fleet-mode') === '1',
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch('/api/v1/gui-meta')
      .then((r) => (r.ok ? (r.json() as Promise<GuiMeta>) : null))
      .then(setMeta)
      .catch(() => setMeta(null));
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, [menuOpen]);

  const current = NAV.find((n) => n.to === location.pathname);
  const toggleFleet = () => {
    const next = !fleetMode;
    setFleetMode(next);
    window.localStorage.setItem('bernstein-fleet-mode', next ? '1' : '0');
  };

  return (
    <div className="min-h-screen flex">
      <aside className="w-56 shrink-0 border-r border-border bg-card flex flex-col">
        <div className="px-4 py-5 border-b border-border">
          <Link to="/tasks" className="flex items-center gap-2 font-semibold tracking-wide">
            <span className="size-7 rounded grid place-items-center bg-primary text-primary-foreground font-mono">
              B
            </span>
            <span>Bernstein</span>
          </Link>
          <p className="text-[10px] text-muted-foreground mt-1 uppercase tracking-widest">
            Conducting podium
          </p>
        </div>
        <nav className="flex-1 p-2 space-y-1">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = location.pathname === item.to;
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  'flex items-center gap-3 px-3 py-2 text-sm rounded-md transition-colors',
                  active
                    ? 'bg-secondary text-secondary-foreground'
                    : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                )}
              >
                <Icon className="size-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="p-3 border-t border-border text-[11px] text-muted-foreground font-mono">
          {meta ? `${meta.version} · ${meta.commit.slice(0, 7)}` : 'connecting…'}
        </div>
      </aside>
      <main className="flex-1 flex flex-col">
        <header className="h-12 border-b border-border flex items-center justify-between px-4 bg-card/30">
          <div className="text-sm text-muted-foreground">{current?.label ?? ''}</div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={toggleFleet}
              className={cn(
                'h-8 px-3 rounded-md text-xs font-medium border border-border transition-colors',
                fleetMode
                  ? 'bg-secondary text-secondary-foreground'
                  : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
              )}
              aria-pressed={fleetMode}
              title={fleetMode ? 'Fleet mode on — click for single-project' : 'Single project — click for fleet mode'}
            >
              <Network className="size-3.5 inline-block mr-1.5 -mt-0.5" />
              {fleetMode ? 'Fleet' : 'Single'}
            </button>
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                className="size-8 grid place-items-center rounded-md hover:bg-secondary text-muted-foreground"
                aria-label="User menu"
                aria-expanded={menuOpen}
              >
                <User className="size-4" />
              </button>
              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-9 w-56 bg-popover border border-border rounded-md shadow-md py-1 z-50"
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setTheme(theme === 'dark' ? 'light' : 'dark');
                    }}
                    className="w-full flex items-center gap-3 px-3 py-2 text-sm text-popover-foreground hover:bg-secondary text-left"
                  >
                    {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
                    {theme === 'dark' ? 'Light theme' : 'Dark theme'}
                  </button>
                  <Link
                    to="/settings"
                    role="menuitem"
                    onClick={() => setMenuOpen(false)}
                    className="flex items-center gap-3 px-3 py-2 text-sm text-popover-foreground hover:bg-secondary"
                  >
                    <SettingsIcon className="size-4" />
                    Settings
                  </Link>
                  <div className="border-t border-border my-1" />
                  <div className="px-3 py-2 text-[11px] text-muted-foreground font-mono">
                    {meta ? `${meta.version} · ${meta.commit.slice(0, 7)}` : 'connecting…'}
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>
        <div className="flex-1 p-6 overflow-auto">{children}</div>
      </main>
    </div>
  );
}
