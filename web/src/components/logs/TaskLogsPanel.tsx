// Top-level orchestrator for the Logs tab inside the task drawer.
//
// Wires the SSE-backed buffer to the toolbar + virtualised list + keyboard
// shortcuts. Persists a handful of presentation preferences to localStorage
// so the operator's last setup carries between sessions.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { cn } from '@/lib/utils';

import { LogEmptyState } from './LogEmptyState';
import { LogFollowControl } from './LogFollowControl';
import { LogKeyboardHelp } from './LogKeyboardHelp';
import { LogList, type LogListHandle } from './LogList';
import { LogStats } from './LogStats';
import { LogStatusPill } from './LogStatusPill';
import { LogToolbar } from './LogToolbar';
import type { LogSearchBarHandle } from './LogSearchBar';
import type { LogLevel } from './types';
import { useLogSearch } from './useLogSearch';
import { useLogShortcuts } from './useLogShortcuts';
import { useTaskLogStream } from './useTaskLogStream';
import { useThroughput } from './useThroughput';
import { copyToClipboard, downloadText } from './utils';

interface Props {
  taskId: string;
  /** Allows the parent to suspend SSE when the Logs tab isn't active. */
  active?: boolean;
  className?: string;
}

const PREF_KEY = 'bernstein.logs.prefs.v1';

interface Prefs {
  showTimestamps: boolean;
  showLevels: boolean;
}

function loadPrefs(): Prefs {
  if (typeof window === 'undefined') {
    return { showTimestamps: true, showLevels: true };
  }
  try {
    const raw = window.localStorage.getItem(PREF_KEY);
    if (!raw) return { showTimestamps: true, showLevels: true };
    const parsed = JSON.parse(raw) as Partial<Prefs>;
    return {
      showTimestamps: parsed.showTimestamps ?? true,
      showLevels: parsed.showLevels ?? true,
    };
  } catch {
    return { showTimestamps: true, showLevels: true };
  }
}

function savePrefs(p: Prefs): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PREF_KEY, JSON.stringify(p));
  } catch {
    /* localStorage may be disabled in private mode — ignore */
  }
}

export function TaskLogsPanel({ taskId, active = true, className }: Props) {
  const [paused, setPaused] = useState(false);
  const [following, setFollowing] = useState(true);
  const [unreadCount, setUnreadCount] = useState(0);
  const [activeLevels, setActiveLevels] = useState<Set<LogLevel>>(() => new Set());
  const [includeUntyped, setIncludeUntyped] = useState(true);
  const [helpOpen, setHelpOpen] = useState(false);
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());

  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<LogListHandle>(null);
  const searchRef = useRef<LogSearchBarHandle>(null);

  const stream = useTaskLogStream({ taskId, enabled: active, paused });
  const rate = useThroughput(stream.totalLines);

  // Level filter projection. When no levels are explicitly selected we treat
  // the set as "all levels"; the untyped pill controls whether plain lines
  // pass through.
  const filteredLines = useMemo(() => {
    const showAllLevels = activeLevels.size === 0;
    if (showAllLevels && includeUntyped) return stream.lines;
    return stream.lines.filter((line) => {
      if (line.level == null) return includeUntyped;
      if (showAllLevels) return true;
      return activeLevels.has(line.level);
    });
  }, [stream.lines, activeLevels, includeUntyped]);

  const filterIds = useMemo(() => {
    if (filteredLines.length === stream.lines.length) return undefined;
    const ids = new Set<number>();
    for (const l of filteredLines) ids.add(l.id);
    return ids;
  }, [filteredLines, stream.lines]);

  const search = useLogSearch({ lines: stream.lines, filterIds });

  const persistPrefs = useCallback((next: Prefs) => {
    setPrefs(next);
    savePrefs(next);
  }, []);

  const toggleLevel = useCallback((level: LogLevel) => {
    setActiveLevels((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });
  }, []);

  const resetLevels = useCallback(() => {
    setActiveLevels(new Set());
    setIncludeUntyped(true);
  }, []);

  const handleFlushPending = useCallback(() => {
    stream.flushPending();
    setPaused(false);
    setUnreadCount(0);
    // Wait one tick so the freshly-flushed lines are in the buffer before we
    // pin the scroll position to the bottom.
    window.requestAnimationFrame(() => {
      listRef.current?.scrollToBottom(false);
    });
  }, [stream]);

  const handleJumpBottom = useCallback(() => {
    listRef.current?.scrollToBottom(true);
    setUnreadCount(0);
  }, []);

  const handleJumpTop = useCallback(() => {
    listRef.current?.scrollToTop(true);
  }, []);

  const handleCopyAll = useCallback(async () => {
    const text = filteredLines.map((l) => l.raw).join('\n');
    await copyToClipboard(text);
  }, [filteredLines]);

  const handleDownload = useCallback(() => {
    const text = filteredLines.map((l) => l.raw).join('\n');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    downloadText(`task-${taskId}-${stamp}.log`, text);
  }, [filteredLines, taskId]);

  const handleClearBuffer = useCallback(() => {
    stream.clear();
    search.clear();
    setUnreadCount(0);
    setFollowing(true);
  }, [stream, search]);

  useLogShortcuts({
    containerRef,
    enabled: active,
    onFocusSearch: () => searchRef.current?.focus(),
    onNextMatch: search.next,
    onPrevMatch: search.prev,
    onJumpTop: handleJumpTop,
    onJumpBottom: handleJumpBottom,
    onTogglePause: () => setPaused((p) => !p),
    onClear: handleClearBuffer,
    onClearSearch: () => {
      if (search.query) {
        search.clear();
      } else {
        setHelpOpen(false);
      }
    },
    onToggleHelp: () => setHelpOpen((o) => !o),
  });

  // Drop unread count back to zero when the user toggles follow on.
  useEffect(() => {
    if (following) setUnreadCount(0);
  }, [following]);

  const isEmpty = filteredLines.length === 0;

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative flex h-[min(60vh,540px)] min-h-[300px] flex-col overflow-hidden rounded-md border border-border-subtle bg-card',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-meta-foreground">
            Logs
          </span>
          <LogStats
            totalLines={stream.totalLines}
            totalBytes={stream.totalBytes}
            rate={rate}
            className="xl:hidden"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <LogFollowControl
            following={following}
            unreadCount={unreadCount}
            onJumpBottom={handleJumpBottom}
            onToggleFollow={() => {
              setFollowing(false);
            }}
          />
          <LogStatusPill phase={stream.phase} completeStatus={stream.completeStatus} />
        </div>
      </div>
      <LogToolbar
        ref={searchRef}
        query={search.query}
        regex={search.regex}
        caseSensitive={search.caseSensitive}
        matchCount={search.matches.length}
        activeIndex={search.activeIndex}
        onQueryChange={search.setQuery}
        onRegexToggle={() => search.setRegex(!search.regex)}
        onCaseToggle={() => search.setCaseSensitive(!search.caseSensitive)}
        onNext={search.next}
        onPrev={search.prev}
        onClearSearch={search.clear}
        activeLevels={activeLevels}
        includeUntyped={includeUntyped}
        onToggleLevel={toggleLevel}
        onToggleUntyped={() => setIncludeUntyped((b) => !b)}
        onResetLevels={resetLevels}
        paused={paused}
        pendingCount={stream.pendingCount}
        onTogglePause={() => setPaused((p) => !p)}
        onFlushPending={handleFlushPending}
        totalLines={stream.totalLines}
        totalBytes={stream.totalBytes}
        rate={rate}
        showTimestamps={prefs.showTimestamps}
        showLevels={prefs.showLevels}
        onToggleTimestamps={() =>
          persistPrefs({ ...prefs, showTimestamps: !prefs.showTimestamps })
        }
        onToggleLevels={() => persistPrefs({ ...prefs, showLevels: !prefs.showLevels })}
        onClearBuffer={handleClearBuffer}
        onDownload={handleDownload}
        onCopyAll={handleCopyAll}
        onShowHelp={() => setHelpOpen(true)}
      />
      <div className="relative flex-1 overflow-hidden">
        {isEmpty ? (
          <LogEmptyState phase={stream.phase} className="h-full" />
        ) : (
          <LogList
            ref={listRef}
            lines={filteredLines}
            matches={search.matches}
            activeMatchIndex={search.activeIndex}
            following={following}
            onFollowingChange={setFollowing}
            onUnreadCountChange={setUnreadCount}
            onActivateLine={search.setActiveByLineId}
            showTimestamp={prefs.showTimestamps}
            showLevel={prefs.showLevels}
            className="h-full"
          />
        )}
        <LogKeyboardHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
      </div>
    </div>
  );
}
