// `?`-triggered overlay listing every keyboard shortcut the panel responds
// to. Rendered as a stacked card that floats over the log body when open.

import { X } from 'lucide-react';

import { cn } from '@/lib/utils';

interface Props {
  open: boolean;
  onClose: () => void;
}

const SHORTCUTS: Array<{ keys: string[]; label: string }> = [
  { keys: ['/'], label: 'Focus search' },
  { keys: ['Esc'], label: 'Clear / dismiss search' },
  { keys: ['Enter'], label: 'Next match' },
  { keys: ['⇧', 'Enter'], label: 'Previous match' },
  { keys: ['n'], label: 'Next match' },
  { keys: ['⇧', 'N'], label: 'Previous match' },
  { keys: ['Space'], label: 'Pause / resume tail' },
  { keys: ['g'], label: 'Jump to top' },
  { keys: ['⇧', 'G'], label: 'Jump to bottom' },
  { keys: ['j'], label: 'Jump to bottom' },
  { keys: ['k'], label: 'Jump to top' },
  { keys: ['c'], label: 'Clear buffer' },
  { keys: ['?'], label: 'Toggle this help' },
];

export function LogKeyboardHelp({ open, onClose }: Props) {
  if (!open) return null;
  return (
    <div
      className="absolute inset-0 z-10 flex items-start justify-center bg-background/70 p-4 backdrop-blur-[2px]"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="log-help-title"
    >
      <div
        className="relative w-full max-w-sm rounded-md border border-border-subtle bg-card p-4 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3
            id="log-help-title"
            className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground"
          >
            Keyboard shortcuts
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close help"
            className="text-meta-foreground transition-colors hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        </div>
        <ul className="grid grid-cols-1 gap-1 text-[11.5px]">
          {SHORTCUTS.map((s, i) => (
            <li key={i} className="flex items-center justify-between gap-3">
              <span className="text-foreground/80">{s.label}</span>
              <span className="flex gap-1">
                {s.keys.map((k, j) => (
                  <kbd
                    key={j}
                    className={cn(
                      'inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-sm border border-border-subtle bg-background px-1 font-mono text-[10px] text-foreground',
                    )}
                  >
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
