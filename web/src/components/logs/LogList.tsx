// Virtualised log list.
//
// Strategy: every line renders at a fixed `LOG_LINE_HEIGHT` so we can window
// purely on scroll offset. Long lines render with `whitespace-pre`, so they
// overflow horizontally (the container scrolls X) — this is the standard
// terminal behaviour and keeps row heights predictable.
//
// Auto-follow: when `following` is true the list pins the scroll position to
// the bottom on every new line. The parent flips `following` off when the
// operator scrolls upward, and flips it back on when they hit the bottom or
// click the "Jump to bottom" pill.

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type UIEvent,
} from 'react';

import { cn } from '@/lib/utils';

import { LogLine } from './LogLine';
import type { LogLine as LogLineRecord, SearchMatch } from './types';
import { LOG_LINE_HEIGHT, LOG_VIRTUAL_OVERSCAN } from './types';

export interface LogListHandle {
  scrollToBottom: (smooth?: boolean) => void;
  scrollToTop: (smooth?: boolean) => void;
  scrollToLine: (id: number, smooth?: boolean) => void;
}

interface Props {
  lines: LogLineRecord[];
  matches: ReadonlyArray<SearchMatch>;
  activeMatchIndex: number;
  following: boolean;
  onFollowingChange: (b: boolean) => void;
  onUnreadCountChange: (n: number) => void;
  onActivateLine?: (id: number) => void;
  showTimestamp: boolean;
  showLevel: boolean;
  className?: string;
}

interface ScrollState {
  scrollTop: number;
  viewportHeight: number;
}

const STICK_TO_BOTTOM_PX = 24;

export const LogList = forwardRef<LogListHandle, Props>(function LogList(props, ref) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollState, setScrollState] = useState<ScrollState>({
    scrollTop: 0,
    viewportHeight: 0,
  });
  const lastLineCountRef = useRef(0);
  const lastSeenCountRef = useRef(0);
  const followingRef = useRef(props.following);
  followingRef.current = props.following;

  // Track the container's height with a ResizeObserver so virtualisation
  // works correctly when the drawer is resized or zoomed.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setScrollState((prev) => ({
        ...prev,
        viewportHeight: el.clientHeight,
      }));
    });
    ro.observe(el);
    setScrollState((prev) => ({ ...prev, viewportHeight: el.clientHeight }));
    return () => ro.disconnect();
  }, []);

  // Match lookup by line id — keeps render-time math O(visible) instead of
  // O(visible × matches).
  const matchesByLineId = useMemo(() => {
    const m = new Map<number, SearchMatch[]>();
    let i = 0;
    for (const match of props.matches) {
      const arr = m.get(match.lineId);
      if (arr) arr.push(match);
      else m.set(match.lineId, [match]);
      i += 1;
    }
    return { byId: m, total: i };
  }, [props.matches]);

  // Per-line global offset — `activeMatchIndex` is an index into the flat
  // `matches` array, so we need to know how many matches preceded a given
  // line to map it back to "which mark is the active one".
  const lineFirstMatchOffset = useMemo(() => {
    const offsets = new Map<number, number>();
    let cursor = 0;
    for (const line of props.lines) {
      offsets.set(line.id, cursor);
      cursor += matchesByLineId.byId.get(line.id)?.length ?? 0;
    }
    return offsets;
  }, [props.lines, matchesByLineId]);

  const { scrollTop, viewportHeight } = scrollState;
  const totalHeight = props.lines.length * LOG_LINE_HEIGHT;
  const startIdx = Math.max(
    0,
    Math.floor(scrollTop / LOG_LINE_HEIGHT) - LOG_VIRTUAL_OVERSCAN,
  );
  const visibleCount = Math.max(
    1,
    Math.ceil(viewportHeight / LOG_LINE_HEIGHT) + LOG_VIRTUAL_OVERSCAN * 2,
  );
  const endIdx = Math.min(props.lines.length, startIdx + visibleCount);
  const offsetY = startIdx * LOG_LINE_HEIGHT;

  const visibleLines = useMemo(
    () => props.lines.slice(startIdx, endIdx),
    [props.lines, startIdx, endIdx],
  );

  const handleScroll = useCallback(
    (e: UIEvent<HTMLDivElement>) => {
      const el = e.currentTarget;
      const st = el.scrollTop;
      const vh = el.clientHeight;
      const sh = el.scrollHeight;
      setScrollState({ scrollTop: st, viewportHeight: vh });
      const atBottom = sh - st - vh <= STICK_TO_BOTTOM_PX;
      if (atBottom && !followingRef.current) {
        props.onFollowingChange(true);
        lastSeenCountRef.current = props.lines.length;
        props.onUnreadCountChange(0);
      } else if (!atBottom && followingRef.current) {
        props.onFollowingChange(false);
        lastSeenCountRef.current = props.lines.length;
      }
    },
    [props.lines.length, props.onFollowingChange, props.onUnreadCountChange],
  );

  // Auto-scroll to bottom on new lines when following; bump unread count
  // when not following.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const lineCount = props.lines.length;
    if (lineCount === lastLineCountRef.current) return;
    if (followingRef.current) {
      el.scrollTop = el.scrollHeight;
      lastSeenCountRef.current = lineCount;
      props.onUnreadCountChange(0);
    } else {
      const unread = Math.max(0, lineCount - lastSeenCountRef.current);
      props.onUnreadCountChange(unread);
    }
    lastLineCountRef.current = lineCount;
  }, [props.lines.length, props.onUnreadCountChange]);

  // Hop the scroll position to the active match if it's currently
  // off-screen. We use `data-line-id` so we don't need to know the row's
  // index in the virtual window.
  useEffect(() => {
    if (props.activeMatchIndex < 0 || props.matches.length === 0) return;
    const match = props.matches[props.activeMatchIndex];
    const el = containerRef.current;
    if (!el) return;
    const idx = props.lines.findIndex((l) => l.id === match.lineId);
    if (idx < 0) return;
    const targetTop = idx * LOG_LINE_HEIGHT;
    const inView =
      targetTop >= el.scrollTop && targetTop <= el.scrollTop + el.clientHeight - LOG_LINE_HEIGHT;
    if (!inView) {
      el.scrollTo({
        top: Math.max(0, targetTop - el.clientHeight / 2),
        behavior: 'smooth',
      });
      props.onFollowingChange(false);
    }
  }, [props.activeMatchIndex, props.matches, props.lines, props.onFollowingChange]);

  useImperativeHandle(
    ref,
    () => ({
      scrollToBottom: (smooth = false) => {
        const el = containerRef.current;
        if (!el) return;
        el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
        props.onFollowingChange(true);
      },
      scrollToTop: (smooth = false) => {
        const el = containerRef.current;
        if (!el) return;
        el.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' });
        props.onFollowingChange(false);
      },
      scrollToLine: (id: number, smooth = false) => {
        const el = containerRef.current;
        if (!el) return;
        const idx = props.lines.findIndex((l) => l.id === id);
        if (idx < 0) return;
        el.scrollTo({
          top: Math.max(0, idx * LOG_LINE_HEIGHT - el.clientHeight / 2),
          behavior: smooth ? 'smooth' : 'auto',
        });
        props.onFollowingChange(false);
      },
    }),
    [props.lines, props.onFollowingChange],
  );

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className={cn(
        'relative h-full overflow-auto bg-background/40 [scrollbar-color:hsl(var(--border-strong))_transparent] [scrollbar-width:thin]',
        props.className,
      )}
      role="log"
      aria-live="polite"
      tabIndex={0}
    >
      <div style={{ height: totalHeight }} className="relative w-max min-w-full">
        <div
          style={{ transform: `translateY(${offsetY}px)` }}
          className="w-max min-w-full"
        >
          {visibleLines.map((line) => {
            const lineMatches = matchesByLineId.byId.get(line.id) ?? [];
            const offset = lineFirstMatchOffset.get(line.id) ?? 0;
            return (
              <LogLine
                key={line.id}
                line={line}
                matches={lineMatches}
                activeMatchIndex={props.activeMatchIndex}
                globalMatchOffset={offset}
                onActivate={props.onActivateLine}
                showTimestamp={props.showTimestamp}
                showLevel={props.showLevel}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
});
