// Composes the per-panel controls: search, level filter, stats, action row.
//
// Layout (collapses gracefully when the drawer is narrow):
//
//   ┌──────────────────────────────────────────────────────────────────────┐
//   │ [search input ........................................][stats][acts]│
//   │ [level filter chips]                                                 │
//   └──────────────────────────────────────────────────────────────────────┘

import { forwardRef } from 'react';
import {
  ALargeSmall,
  Download,
  Eraser,
  HelpCircle,
  Keyboard,
  Wand2,
} from 'lucide-react';

import { cn } from '@/lib/utils';

import { LogLevelFilter } from './LogLevelFilter';
import { LogPauseControl } from './LogPauseControl';
import { LogSearchBar, type LogSearchBarHandle } from './LogSearchBar';
import { LogStats } from './LogStats';
import type { LogLevel } from './types';

interface Props {
  // Search
  query: string;
  regex: boolean;
  caseSensitive: boolean;
  matchCount: number;
  activeIndex: number;
  onQueryChange: (q: string) => void;
  onRegexToggle: () => void;
  onCaseToggle: () => void;
  onNext: () => void;
  onPrev: () => void;
  onClearSearch: () => void;

  // Level filter
  activeLevels: ReadonlySet<LogLevel>;
  includeUntyped: boolean;
  onToggleLevel: (l: LogLevel) => void;
  onToggleUntyped: () => void;
  onResetLevels: () => void;

  // Pause
  paused: boolean;
  pendingCount: number;
  onTogglePause: () => void;
  onFlushPending: () => void;

  // Stats
  totalLines: number;
  totalBytes: number;
  rate: number;

  // Display toggles
  showTimestamps: boolean;
  showLevels: boolean;
  onToggleTimestamps: () => void;
  onToggleLevels: () => void;

  // Actions
  onClearBuffer: () => void;
  onDownload: () => void;
  onCopyAll: () => void;
  onShowHelp: () => void;
}

export const LogToolbar = forwardRef<LogSearchBarHandle, Props>(function LogToolbar(props, ref) {
  return (
    <div className="space-y-1.5 border-b border-border-subtle bg-card/60 px-3 py-2">
      <div className="flex items-center gap-2">
        <LogSearchBar
          ref={ref}
          query={props.query}
          regex={props.regex}
          caseSensitive={props.caseSensitive}
          matchCount={props.matchCount}
          activeIndex={props.activeIndex}
          onQueryChange={props.onQueryChange}
          onRegexToggle={props.onRegexToggle}
          onCaseToggle={props.onCaseToggle}
          onNext={props.onNext}
          onPrev={props.onPrev}
          onClear={props.onClearSearch}
        />
        <LogStats
          totalLines={props.totalLines}
          totalBytes={props.totalBytes}
          rate={props.rate}
          className="hidden xl:inline-flex"
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <LogLevelFilter
          active={props.activeLevels}
          includeUntyped={props.includeUntyped}
          onToggleLevel={props.onToggleLevel}
          onToggleUntyped={props.onToggleUntyped}
          onReset={props.onResetLevels}
        />
        <div className="flex items-center gap-1">
          <ToolbarAction
            pressed={props.showTimestamps}
            onClick={props.onToggleTimestamps}
            title="Toggle timestamps"
          >
            <ALargeSmall className="size-3.5" />
          </ToolbarAction>
          <ToolbarAction
            pressed={props.showLevels}
            onClick={props.onToggleLevels}
            title="Toggle inline level badges"
          >
            <Wand2 className="size-3.5" />
          </ToolbarAction>
          <Divider />
          <LogPauseControl
            paused={props.paused}
            pendingCount={props.pendingCount}
            onToggle={props.onTogglePause}
            onFlush={props.onFlushPending}
          />
          <Divider />
          <ToolbarAction onClick={props.onCopyAll} title="Copy all lines">
            <Keyboard className="size-3.5" />
          </ToolbarAction>
          <ToolbarAction onClick={props.onDownload} title="Download as .log">
            <Download className="size-3.5" />
          </ToolbarAction>
          <ToolbarAction
            onClick={props.onClearBuffer}
            title="Clear buffer (c)"
            tone="destructive"
          >
            <Eraser className="size-3.5" />
          </ToolbarAction>
          <Divider />
          <ToolbarAction onClick={props.onShowHelp} title="Keyboard shortcuts (?)">
            <HelpCircle className="size-3.5" />
          </ToolbarAction>
        </div>
      </div>
    </div>
  );
});

function ToolbarAction({
  children,
  onClick,
  title,
  pressed,
  tone,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  pressed?: boolean;
  tone?: 'destructive';
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      aria-pressed={pressed}
      className={cn(
        'inline-flex size-6 items-center justify-center rounded-sm border border-transparent transition-colors',
        pressed && 'border-accent/30 bg-accent/10 text-accent',
        !pressed && 'text-meta-foreground hover:bg-secondary hover:text-foreground',
        tone === 'destructive' && 'hover:bg-destructive/10 hover:text-destructive',
      )}
    >
      {children}
    </button>
  );
}

function Divider() {
  return <span className="mx-0.5 h-3 w-px bg-border-subtle" aria-hidden="true" />;
}
