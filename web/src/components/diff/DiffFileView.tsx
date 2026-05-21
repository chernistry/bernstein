// Renders one DiffFile inside the right-hand pane: a header strip with the
// path, status badge, change counts and collapse toggle, followed by the
// hunks in either unified or split layout.

import { ChevronDown, ChevronRight, FileBox } from 'lucide-react';
import { useMemo } from 'react';

import { cn } from '@/lib/utils';

import { DiffLineSplit, DiffLineUnified, type LineKind } from './DiffLine';
import type { DiffFile, DiffHunk, DiffViewMode } from './types';
import { formatChangeCount } from './utils';

interface Props {
  file: DiffFile;
  viewMode: DiffViewMode;
  collapsed: boolean;
  onToggleCollapse: () => void;
  wrap: boolean;
  registerRef?: (path: string, el: HTMLElement | null) => void;
}

interface UnifiedRow {
  kind: LineKind;
  oldNum: number | null;
  newNum: number | null;
  text: string;
}

interface SplitRow {
  left: { kind: LineKind; lineNum: number | null; text: string | null };
  right: { kind: LineKind; lineNum: number | null; text: string | null };
}

function buildUnifiedRows(hunk: DiffHunk): UnifiedRow[] {
  const rows: UnifiedRow[] = [];
  let oldNum = hunk.old_start;
  let newNum = hunk.new_start;
  for (const raw of hunk.lines) {
    if (raw.startsWith('+') && !raw.startsWith('+++')) {
      rows.push({ kind: 'addition', oldNum: null, newNum, text: raw.slice(1) });
      newNum += 1;
    } else if (raw.startsWith('-') && !raw.startsWith('---')) {
      rows.push({ kind: 'deletion', oldNum, newNum: null, text: raw.slice(1) });
      oldNum += 1;
    } else if (raw.startsWith('\\')) {
      // `\ No newline at end of file` - show as muted context-ish.
      rows.push({ kind: 'context', oldNum: null, newNum: null, text: raw });
    } else {
      const text = raw.startsWith(' ') ? raw.slice(1) : raw;
      rows.push({ kind: 'context', oldNum, newNum, text });
      oldNum += 1;
      newNum += 1;
    }
  }
  return rows;
}

function buildSplitRows(hunk: DiffHunk): SplitRow[] {
  // Pair runs of `-` followed by `+` so the user can see deletions and
  // additions side-by-side. Anything unpaired gets an empty placeholder on
  // the opposite side.
  const rows: SplitRow[] = [];
  let oldNum = hunk.old_start;
  let newNum = hunk.new_start;
  const lines = hunk.lines;
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    if (ln.startsWith('-') && !ln.startsWith('---')) {
      const dels: string[] = [];
      while (i < lines.length && lines[i].startsWith('-') && !lines[i].startsWith('---')) {
        dels.push(lines[i].slice(1));
        i += 1;
      }
      const adds: string[] = [];
      while (i < lines.length && lines[i].startsWith('+') && !lines[i].startsWith('+++')) {
        adds.push(lines[i].slice(1));
        i += 1;
      }
      const pairs = Math.max(dels.length, adds.length);
      for (let p = 0; p < pairs; p += 1) {
        const dText = dels[p];
        const aText = adds[p];
        rows.push({
          left:
            dText != null
              ? { kind: 'deletion', lineNum: oldNum + p, text: dText }
              : { kind: 'empty', lineNum: null, text: null },
          right:
            aText != null
              ? { kind: 'addition', lineNum: newNum + p, text: aText }
              : { kind: 'empty', lineNum: null, text: null },
        });
      }
      oldNum += dels.length;
      newNum += adds.length;
      continue;
    }
    if (ln.startsWith('+') && !ln.startsWith('+++')) {
      rows.push({
        left: { kind: 'empty', lineNum: null, text: null },
        right: { kind: 'addition', lineNum: newNum, text: ln.slice(1) },
      });
      newNum += 1;
      i += 1;
      continue;
    }
    if (ln.startsWith('\\')) {
      rows.push({
        left: { kind: 'context', lineNum: null, text: ln },
        right: { kind: 'context', lineNum: null, text: ln },
      });
      i += 1;
      continue;
    }
    const text = ln.startsWith(' ') ? ln.slice(1) : ln;
    rows.push({
      left: { kind: 'context', lineNum: oldNum, text },
      right: { kind: 'context', lineNum: newNum, text },
    });
    oldNum += 1;
    newNum += 1;
    i += 1;
  }
  return rows;
}

function statusToneClass(file: DiffFile): string {
  switch (file.status) {
    case 'added':
      return 'border-success/30 bg-success/10 text-success';
    case 'deleted':
      return 'border-destructive/30 bg-destructive/10 text-destructive';
    case 'renamed':
      return 'border-warning/30 bg-warning/10 text-warning';
    case 'binary':
      return 'border-border-subtle bg-muted text-meta-foreground';
    case 'modified':
    default:
      return 'border-border-subtle bg-muted/60 text-foreground';
  }
}

export function DiffFileView({
  file,
  viewMode,
  collapsed,
  onToggleCollapse,
  wrap,
  registerRef,
}: Props) {
  const counts = formatChangeCount(file.additions, file.deletions);
  const hunkRows = useMemo(() => {
    if (file.binary) return [];
    if (viewMode === 'split') {
      return file.hunks.map((h) => ({ hunk: h, rows: buildSplitRows(h) }));
    }
    return file.hunks.map((h) => ({ hunk: h, rows: buildUnifiedRows(h) }));
  }, [file.hunks, file.binary, viewMode]);

  return (
    <section
      ref={(el) => registerRef?.(file.path, el)}
      data-diff-file-path={file.path}
      className="overflow-hidden rounded-md border border-border-subtle bg-card"
      aria-label={`Diff for ${file.path}`}
    >
      <header
        className={cn(
          'flex items-center gap-2 border-b border-border-subtle bg-muted/40 px-2.5 py-1.5',
        )}
      >
        <button
          type="button"
          onClick={onToggleCollapse}
          className="inline-flex size-5 shrink-0 items-center justify-center rounded-sm text-meta-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-expanded={!collapsed}
          aria-label={collapsed ? `Expand ${file.path}` : `Collapse ${file.path}`}
        >
          {collapsed ? <ChevronRight className="size-3.5" /> : <ChevronDown className="size-3.5" />}
        </button>
        <span
          className={cn(
            'inline-flex h-4 items-center rounded-sm border px-1.5 font-mono text-[9.5px] uppercase tracking-tight',
            statusToneClass(file),
          )}
        >
          {file.status}
        </span>
        {file.old_path && file.old_path !== file.path && (
          <span
            className="truncate font-mono text-[10.5px] text-meta-foreground"
            title={`Renamed from ${file.old_path}`}
          >
            {file.old_path}
            <span className="mx-1.5 text-meta-foreground/60">→</span>
          </span>
        )}
        <span
          className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-foreground"
          title={file.path}
        >
          {file.path}
        </span>
        {file.language && (
          <span className="hidden shrink-0 rounded-sm bg-card px-1.5 py-px font-mono text-[9.5px] uppercase tracking-tight text-meta-foreground sm:inline-block">
            {file.language}
          </span>
        )}
        <span className="flex shrink-0 items-center gap-1.5 font-mono text-[10.5px]">
          {counts.additions && <span className="text-success">{counts.additions}</span>}
          {counts.deletions && <span className="text-destructive">{counts.deletions}</span>}
        </span>
      </header>
      {!collapsed && (
        <div className="overflow-x-auto">
          {file.binary ? (
            <div className="flex items-center gap-2 px-3 py-4 text-[11.5px] text-meta-foreground">
              <FileBox className="size-4" aria-hidden />
              Binary file - content not rendered.
            </div>
          ) : hunkRows.length === 0 ? (
            <div className="px-3 py-4 text-[11.5px] text-meta-foreground">
              No textual changes.
            </div>
          ) : (
            hunkRows.map((hr, hi) => (
              <div key={hi} className="border-b border-border-subtle/60 last:border-0">
                <div className="border-b border-border-subtle/40 bg-muted/30 px-3 py-0.5 font-mono text-[10px] text-meta-foreground">
                  {hr.hunk.header}
                </div>
                {viewMode === 'unified'
                  ? (hr.rows as UnifiedRow[]).map((row, ri) => (
                      <DiffLineUnified
                        key={ri}
                        kind={row.kind}
                        oldNum={row.oldNum}
                        newNum={row.newNum}
                        text={row.text}
                        language={file.language}
                        wrap={wrap}
                      />
                    ))
                  : (hr.rows as SplitRow[]).map((row, ri) => (
                      <DiffLineSplit
                        key={ri}
                        left={row.left}
                        right={row.right}
                        language={file.language}
                        wrap={wrap}
                      />
                    ))}
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}
