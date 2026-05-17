// Live search over the visible log buffer.
//
// Recomputes matches against the current line array whenever the query or
// the buffer changes. Highlights are returned as `[start, end)` character
// offsets into the line's `plain` text so the renderer can splice in
// `<mark>` tags without coupling to the styling.

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { LogLine, SearchMatch, SearchState } from './types';
import { escapeRegex } from './utils';

interface UseLogSearchOptions {
  lines: LogLine[];
  /** Optional pre-filter (e.g. level filter) — search runs over its output. */
  filterIds?: ReadonlySet<number>;
}

export interface LogSearchApi extends SearchState {
  setQuery: (q: string) => void;
  setRegex: (b: boolean) => void;
  setCaseSensitive: (b: boolean) => void;
  next: () => void;
  prev: () => void;
  setActiveByLineId: (id: number) => void;
  clear: () => void;
}

const EMPTY_STATE: SearchState = {
  query: '',
  regex: false,
  caseSensitive: false,
  matches: [],
  activeIndex: -1,
};

function buildMatcher(
  query: string,
  regex: boolean,
  caseSensitive: boolean,
): RegExp | null {
  if (!query) return null;
  try {
    const src = regex ? query : escapeRegex(query);
    return new RegExp(src, caseSensitive ? 'g' : 'gi');
  } catch {
    // Malformed user-typed regex — surface as "no matches" rather than throw.
    return null;
  }
}

export function useLogSearch({ lines, filterIds }: UseLogSearchOptions): LogSearchApi {
  const [query, setQuery] = useState('');
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const matches = useMemo<SearchMatch[]>(() => {
    const matcher = buildMatcher(query, regex, caseSensitive);
    if (!matcher) return [];
    const out: SearchMatch[] = [];
    for (const line of lines) {
      if (filterIds && !filterIds.has(line.id)) continue;
      matcher.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = matcher.exec(line.plain)) !== null) {
        out.push({ lineId: line.id, start: m.index, end: m.index + m[0].length });
        // Guard against zero-width matches (e.g. `.*`) — skip ahead manually.
        if (m[0].length === 0) matcher.lastIndex += 1;
      }
    }
    return out;
  }, [lines, query, regex, caseSensitive, filterIds]);

  // Clamp active index when the match set shrinks.
  useEffect(() => {
    if (matches.length === 0) {
      setActiveIndex(-1);
      return;
    }
    setActiveIndex((idx) => {
      if (idx < 0) return 0;
      if (idx >= matches.length) return matches.length - 1;
      return idx;
    });
  }, [matches]);

  const next = useCallback(() => {
    if (matches.length === 0) return;
    setActiveIndex((idx) => (idx + 1) % matches.length);
  }, [matches.length]);

  const prev = useCallback(() => {
    if (matches.length === 0) return;
    setActiveIndex((idx) => (idx <= 0 ? matches.length - 1 : idx - 1));
  }, [matches.length]);

  const setActiveByLineId = useCallback(
    (id: number) => {
      const i = matches.findIndex((m) => m.lineId === id);
      if (i >= 0) setActiveIndex(i);
    },
    [matches],
  );

  const clear = useCallback(() => {
    setQuery('');
    setActiveIndex(-1);
  }, []);

  return {
    ...EMPTY_STATE,
    query,
    regex,
    caseSensitive,
    matches,
    activeIndex,
    setQuery,
    setRegex,
    setCaseSensitive,
    next,
    prev,
    setActiveByLineId,
    clear,
  };
}
