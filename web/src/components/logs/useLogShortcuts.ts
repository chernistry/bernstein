// Keyboard shortcuts for the log panel.
//
// Active while the panel is mounted *and* the user is interacting with it
// (focus is somewhere inside the panel root). The hook is intentionally
// scoped to a ref'd element so it doesn't hijack `/` while the operator is
// editing a separate task search box.

import { useEffect, type RefObject } from 'react';

export interface LogShortcuts {
  onFocusSearch: () => void;
  onNextMatch: () => void;
  onPrevMatch: () => void;
  onJumpTop: () => void;
  onJumpBottom: () => void;
  onTogglePause: () => void;
  onClear: () => void;
  onClearSearch: () => void;
  onToggleHelp: () => void;
}

export interface UseLogShortcutsOptions extends LogShortcuts {
  containerRef: RefObject<HTMLElement | null>;
  enabled: boolean;
}

const TEXT_INPUT_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

function isTextInput(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (TEXT_INPUT_TAGS.has(target.tagName)) return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useLogShortcuts({
  containerRef,
  enabled,
  onFocusSearch,
  onNextMatch,
  onPrevMatch,
  onJumpTop,
  onJumpBottom,
  onTogglePause,
  onClear,
  onClearSearch,
  onToggleHelp,
}: UseLogShortcutsOptions): void {
  useEffect(() => {
    if (!enabled) return;

    const handler = (e: KeyboardEvent) => {
      const root = containerRef.current;
      if (!root) return;
      const inside = root.contains(document.activeElement);
      // We only react to key events when focus is somewhere inside our panel
      // OR the focused element is the document body (no focused control).
      if (!inside && document.activeElement !== document.body) return;
      // While the user is typing into the search field, only `Escape` and
      // `Enter` / `Shift+Enter` are handled here — the rest fall through to
      // the input.
      const inField = isTextInput(e.target);
      if (e.key === 'Escape') {
        e.preventDefault();
        onClearSearch();
        return;
      }
      if (inField) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (e.shiftKey) onPrevMatch();
          else onNextMatch();
        }
        return;
      }
      // Single-key shortcuts: ignore when a modifier other than Shift is
      // held so we don't conflict with browser shortcuts.
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      switch (e.key) {
        case '/':
          e.preventDefault();
          onFocusSearch();
          break;
        case 'n':
          if (!e.shiftKey) {
            e.preventDefault();
            onNextMatch();
          }
          break;
        case 'N':
          e.preventDefault();
          onPrevMatch();
          break;
        case 'j':
        case 'G':
          e.preventDefault();
          onJumpBottom();
          break;
        case 'k':
        case 'g':
          e.preventDefault();
          onJumpTop();
          break;
        case ' ':
          e.preventDefault();
          onTogglePause();
          break;
        case 'c':
          e.preventDefault();
          onClear();
          break;
        case '?':
          e.preventDefault();
          onToggleHelp();
          break;
        default:
          // unhandled — leave it alone
          break;
      }
    };

    document.addEventListener('keydown', handler);
    return () => {
      document.removeEventListener('keydown', handler);
    };
  }, [
    enabled,
    containerRef,
    onFocusSearch,
    onNextMatch,
    onPrevMatch,
    onJumpTop,
    onJumpBottom,
    onTogglePause,
    onClear,
    onClearSearch,
    onToggleHelp,
  ]);
}
