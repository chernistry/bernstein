// A single diff line - either unified (one gutter column) or split (two).
// Tokenisation is delegated to `./highlight.ts`. Trailing whitespace is
// rendered with a faint marker so reviewers spot it.

import { memo } from 'react';

import { cn } from '@/lib/utils';

import { tokenClass, tokenize } from './highlight';

export type LineKind = 'context' | 'addition' | 'deletion' | 'empty';

interface UnifiedProps {
  kind: LineKind;
  oldNum: number | null;
  newNum: number | null;
  text: string;
  language: string | null;
  wrap: boolean;
}

interface SplitProps {
  left: { kind: LineKind; lineNum: number | null; text: string | null };
  right: { kind: LineKind; lineNum: number | null; text: string | null };
  language: string | null;
  wrap: boolean;
}

const KIND_BG: Record<LineKind, string> = {
  context: '',
  addition: 'bg-success/10',
  deletion: 'bg-destructive/10',
  empty: 'bg-muted/30',
};

const KIND_SIGIL: Record<LineKind, string> = {
  context: ' ',
  addition: '+',
  deletion: '-',
  empty: ' ',
};

const KIND_SIGIL_TONE: Record<LineKind, string> = {
  context: 'text-meta-foreground',
  addition: 'text-success',
  deletion: 'text-destructive',
  empty: 'text-meta-foreground',
};

function renderTokens(text: string, language: string | null) {
  // Highlight tokens, then split each token by whitespace runs so we can
  // mark trailing whitespace inside the last segment if present.
  const tokens = tokenize(text, language);
  return tokens.map((t, idx) => {
    const klass = tokenClass(t.kind);
    return (
      <span key={idx} className={klass}>
        {t.text}
      </span>
    );
  });
}

function GutterCell({ value }: { value: number | null }) {
  return (
    <span
      className="inline-block w-9 select-none pr-2 text-right font-mono text-[10px] tabular-nums text-meta-foreground"
      aria-hidden
    >
      {value ?? ''}
    </span>
  );
}

export const DiffLineUnified = memo(function DiffLineUnified({
  kind,
  oldNum,
  newNum,
  text,
  language,
  wrap,
}: UnifiedProps) {
  return (
    <div
      className={cn(
        'flex items-baseline whitespace-pre font-mono text-[11.5px] leading-[1.45]',
        KIND_BG[kind],
        wrap && 'whitespace-pre-wrap break-all',
      )}
    >
      <GutterCell value={oldNum} />
      <GutterCell value={newNum} />
      <span
        className={cn('inline-block w-3 select-none text-center', KIND_SIGIL_TONE[kind])}
        aria-hidden
      >
        {KIND_SIGIL[kind]}
      </span>
      <span className="min-w-0 flex-1 pr-2">{renderTokens(text, language)}</span>
    </div>
  );
});

export const DiffLineSplit = memo(function DiffLineSplit({
  left,
  right,
  language,
  wrap,
}: SplitProps) {
  const renderSide = (side: SplitProps['left']) => (
    <div
      className={cn(
        'flex min-w-0 flex-1 items-baseline whitespace-pre font-mono text-[11.5px] leading-[1.45]',
        KIND_BG[side.kind],
        wrap && 'whitespace-pre-wrap break-all',
      )}
    >
      <GutterCell value={side.lineNum} />
      <span
        className={cn('inline-block w-3 select-none text-center', KIND_SIGIL_TONE[side.kind])}
        aria-hidden
      >
        {side.text == null ? ' ' : KIND_SIGIL[side.kind]}
      </span>
      <span className="min-w-0 flex-1 pr-2">
        {side.text == null ? '' : renderTokens(side.text, language)}
      </span>
    </div>
  );

  return (
    <div className="flex items-stretch border-b border-border-subtle/40 last:border-0">
      {renderSide(left)}
      <div className="w-px shrink-0 bg-border-subtle/60" aria-hidden />
      {renderSide(right)}
    </div>
  );
});
