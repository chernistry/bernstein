// Search input + match navigation. Lives inline in the toolbar.

import {
  forwardRef,
  useImperativeHandle,
  useRef,
  type ChangeEvent,
} from 'react';
import { ChevronDown, ChevronUp, Regex, Search, X } from 'lucide-react';

import { cn } from '@/lib/utils';

export interface LogSearchBarHandle {
  focus: () => void;
}

interface Props {
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
  onClear: () => void;
}

export const LogSearchBar = forwardRef<LogSearchBarHandle, Props>(function LogSearchBar(
  {
    query,
    regex,
    caseSensitive,
    matchCount,
    activeIndex,
    onQueryChange,
    onRegexToggle,
    onCaseToggle,
    onNext,
    onPrev,
    onClear,
  },
  ref,
) {
  const inputRef = useRef<HTMLInputElement>(null);
  useImperativeHandle(ref, () => ({
    focus: () => {
      inputRef.current?.focus();
      inputRef.current?.select();
    },
  }));

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => onQueryChange(e.target.value);

  return (
    <div
      className={cn(
        'inline-flex h-7 min-w-0 flex-1 items-center gap-1 rounded-md border border-border-subtle bg-card pl-2 pr-1 text-[11.5px]',
        'focus-within:border-border-strong focus-within:ring-1 focus-within:ring-accent/30',
      )}
    >
      <Search className="size-3 shrink-0 text-meta-foreground" />
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={handleChange}
        placeholder="Search logs…"
        className="min-w-0 flex-1 bg-transparent font-mono outline-none placeholder:text-meta-foreground/70"
        aria-label="Search logs"
        spellCheck={false}
        autoComplete="off"
      />
      {query && (
        <span className="select-none whitespace-nowrap font-mono text-[10px] tabular-nums text-meta-foreground">
          {matchCount === 0 ? '0/0' : `${activeIndex + 1}/${matchCount}`}
        </span>
      )}
      <ToolBtn
        onClick={onPrev}
        title="Previous match (Shift+Enter)"
        disabled={matchCount === 0}
      >
        <ChevronUp className="size-3" />
      </ToolBtn>
      <ToolBtn
        onClick={onNext}
        title="Next match (Enter)"
        disabled={matchCount === 0}
      >
        <ChevronDown className="size-3" />
      </ToolBtn>
      <Toggle pressed={caseSensitive} onToggle={onCaseToggle} title="Case sensitive">
        <span className="font-mono text-[10px]">Aa</span>
      </Toggle>
      <Toggle pressed={regex} onToggle={onRegexToggle} title="Regular expression">
        <Regex className="size-3" />
      </Toggle>
      {query && (
        <ToolBtn onClick={onClear} title="Clear search (Esc)">
          <X className="size-3" />
        </ToolBtn>
      )}
    </div>
  );
});

function ToolBtn({
  children,
  onClick,
  title,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className={cn(
        'inline-flex size-5 items-center justify-center rounded-sm text-meta-foreground transition-colors',
        'hover:bg-secondary hover:text-foreground',
        'disabled:cursor-not-allowed disabled:opacity-40',
      )}
    >
      {children}
    </button>
  );
}

function Toggle({
  children,
  pressed,
  onToggle,
  title,
}: {
  children: React.ReactNode;
  pressed: boolean;
  onToggle: () => void;
  title: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={pressed}
      title={title}
      aria-label={title}
      className={cn(
        'inline-flex size-5 items-center justify-center rounded-sm transition-colors',
        pressed
          ? 'bg-accent/20 text-accent'
          : 'text-meta-foreground hover:bg-secondary hover:text-foreground',
      )}
    >
      {children}
    </button>
  );
}
