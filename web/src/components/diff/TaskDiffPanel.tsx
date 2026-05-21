// Top-level orchestrator for the Diff tab inside the task drawer.
//
// Pulls `/dashboard/tasks/{id}/diff` via react-query, then renders a
// two-pane layout: a scrollable file list on the left and a hunk view on
// the right. Operator preferences (view mode, word wrap, collapsed files)
// are persisted to localStorage so the dashboard feels stable between
// sessions.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { cn } from '@/lib/utils';

import { DiffFileList } from './DiffFileList';
import { DiffFileView } from './DiffFileView';
import { DiffHeader } from './DiffHeader';
import {
  DiffEmptyState,
  DiffErrorState,
  DiffLoadingState,
} from './DiffStates';
import type { DiffViewMode } from './types';
import { copyToClipboard, downloadText } from './utils';
import { useTaskDiff } from './useTaskDiff';

export interface TaskDiffPanelProps {
  taskId: string;
  /** Suspends data fetching when the tab isn't visible. */
  active?: boolean;
  className?: string;
}

const PREF_KEY = 'bernstein.diff.prefs.v1';

interface Prefs {
  viewMode: DiffViewMode;
  wrap: boolean;
}

function loadPrefs(): Prefs {
  if (typeof window === 'undefined') return { viewMode: 'unified', wrap: false };
  try {
    const raw = window.localStorage.getItem(PREF_KEY);
    if (!raw) return { viewMode: 'unified', wrap: false };
    const parsed = JSON.parse(raw) as Partial<Prefs>;
    return {
      viewMode: parsed.viewMode === 'split' ? 'split' : 'unified',
      wrap: parsed.wrap === true,
    };
  } catch {
    return { viewMode: 'unified', wrap: false };
  }
}

function savePrefs(prefs: Prefs): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
  } catch {
    /* private mode - ignore */
  }
}

export function TaskDiffPanel({ taskId, active = true, className }: TaskDiffPanelProps) {
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [activePath, setActivePath] = useState<string | null>(null);

  const query = useTaskDiff({ taskId, enabled: active });
  const data = query.data;

  const containerRef = useRef<HTMLDivElement>(null);
  const filePanelRef = useRef<HTMLDivElement>(null);
  const fileRefs = useRef<Map<string, HTMLElement>>(new Map());

  const persistPrefs = useCallback((next: Prefs) => {
    setPrefs(next);
    savePrefs(next);
  }, []);

  const registerRef = useCallback((path: string, el: HTMLElement | null) => {
    if (el === null) {
      fileRefs.current.delete(path);
    } else {
      fileRefs.current.set(path, el);
    }
  }, []);

  const handleSelect = useCallback((path: string) => {
    setActivePath(path);
    const el = fileRefs.current.get(path);
    if (el) {
      // Scroll inside the right pane only - don't move the whole drawer.
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, []);

  const handleToggleCollapse = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const handleRefresh = useCallback(() => {
    void query.refetch();
  }, [query]);

  const handleCopy = useCallback(async () => {
    const text = data?.unified ?? '';
    if (!text) return false;
    return copyToClipboard(text);
  }, [data]);

  const handleDownload = useCallback(() => {
    const text = data?.unified ?? '';
    if (!text) return;
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    downloadText(`task-${taskId}-${stamp}.patch`, text);
  }, [data, taskId]);

  // Sync the highlighted file in the left pane with whichever file is
  // currently in view in the right pane. Cheap IntersectionObserver - only
  // active while the panel is mounted.
  useEffect(() => {
    if (!filePanelRef.current || !data || data.files.length === 0) return;
    const root = filePanelRef.current;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        const top = visible[0]?.target as HTMLElement | undefined;
        const path = top?.dataset.diffFilePath;
        if (path) setActivePath(path);
      },
      { root, threshold: [0, 0.25, 0.5], rootMargin: '-10% 0px -50% 0px' },
    );
    for (const el of fileRefs.current.values()) observer.observe(el);
    return () => observer.disconnect();
  }, [data]);

  // Reset selection when the task changes.
  useEffect(() => {
    setCollapsed(new Set());
    setActivePath(null);
  }, [taskId]);

  const isLoading = query.isLoading || (query.isFetching && !data);
  const hasError = query.isError && !data;
  const hasNoFiles = useMemo(
    () => !!data && data.files.length === 0,
    [data],
  );

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label="Task diff"
      className={cn(
        'relative flex h-[min(60vh,540px)] min-h-[300px] flex-col overflow-hidden rounded-md border border-border-subtle bg-card',
        className,
      )}
    >
      <DiffHeader
        branch={data?.branch ?? null}
        baseRef={data?.base_ref ?? 'main'}
        headRef={data?.head_ref ?? null}
        additions={data?.additions ?? 0}
        deletions={data?.deletions ?? 0}
        fileCount={data?.files.length ?? 0}
        truncated={data?.truncated ?? false}
        note={data?.note ?? null}
        generatedAt={data?.generated_at ?? 0}
        viewMode={prefs.viewMode}
        onViewModeChange={(mode) => persistPrefs({ ...prefs, viewMode: mode })}
        wrap={prefs.wrap}
        onWrapChange={(wrap) => persistPrefs({ ...prefs, wrap })}
        loading={query.isFetching}
        onRefresh={handleRefresh}
        onCopy={handleCopy}
        onDownload={handleDownload}
        disableActions={!data?.unified}
      />
      <div className="relative flex min-h-0 flex-1">
        {isLoading ? (
          <DiffLoadingState className="flex-1" />
        ) : hasError ? (
          <DiffErrorState
            className="flex-1"
            onRetry={handleRefresh}
            message={query.error instanceof Error ? query.error.message : undefined}
          />
        ) : hasNoFiles ? (
          <DiffEmptyState className="flex-1" />
        ) : (
          <>
            <aside
              className="hidden w-[34%] min-w-[180px] max-w-[280px] shrink-0 border-r border-border-subtle bg-card md:flex md:flex-col"
              aria-label="Files in this diff"
            >
              <DiffFileList
                files={data?.files ?? []}
                activePath={activePath}
                onSelect={handleSelect}
              />
            </aside>
            <div
              ref={filePanelRef}
              className="min-w-0 flex-1 overflow-y-auto bg-background/30 p-2"
            >
              <div className="flex flex-col gap-2">
                {(data?.files ?? []).map((file) => (
                  <DiffFileView
                    key={`${file.status}:${file.path}`}
                    file={file}
                    viewMode={prefs.viewMode}
                    collapsed={collapsed.has(file.path)}
                    onToggleCollapse={() => handleToggleCollapse(file.path)}
                    wrap={prefs.wrap}
                    registerRef={registerRef}
                  />
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
