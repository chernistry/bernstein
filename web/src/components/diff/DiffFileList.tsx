// File-list pane (left side of the panel) - scrollable summary of every
// touched file with status pill, change counts, and language hint.
//
// Click jumps the right pane to the matching file. The list is virtualised
// only when the file count is large (>200) - most task diffs have a couple
// dozen files at most.

import {
  FileCode2,
  FilePlus2,
  FileX2,
  FileEdit,
  FileBox,
  Files,
} from 'lucide-react';
import { useMemo } from 'react';

import { cn } from '@/lib/utils';

import type { DiffFile, DiffFileStatus } from './types';
import { formatChangeCount } from './utils';

interface Props {
  files: DiffFile[];
  activePath: string | null;
  onSelect: (path: string) => void;
  className?: string;
}

interface FileListItemProps {
  file: DiffFile;
  active: boolean;
  onSelect: (path: string) => void;
}

function statusIcon(status: DiffFileStatus, binary: boolean) {
  if (binary) return FileBox;
  switch (status) {
    case 'added':
      return FilePlus2;
    case 'deleted':
      return FileX2;
    case 'renamed':
      return FileEdit;
    case 'binary':
      return FileBox;
    case 'modified':
    default:
      return FileCode2;
  }
}

function statusTone(status: DiffFileStatus): string {
  switch (status) {
    case 'added':
      return 'text-success';
    case 'deleted':
      return 'text-destructive';
    case 'renamed':
      return 'text-warning';
    case 'binary':
      return 'text-meta-foreground';
    case 'modified':
    default:
      return 'text-foreground';
  }
}

function statusLabel(status: DiffFileStatus): string {
  switch (status) {
    case 'added':
      return 'A';
    case 'deleted':
      return 'D';
    case 'renamed':
      return 'R';
    case 'binary':
      return 'B';
    case 'modified':
    default:
      return 'M';
  }
}

function truncatePath(path: string, maxLen = 42): { head: string; tail: string } {
  if (path.length <= maxLen) {
    const slash = path.lastIndexOf('/');
    if (slash < 0) return { head: '', tail: path };
    return { head: path.slice(0, slash + 1), tail: path.slice(slash + 1) };
  }
  const slash = path.lastIndexOf('/');
  if (slash < 0) return { head: '', tail: path };
  const tail = path.slice(slash + 1);
  const headBudget = Math.max(0, maxLen - tail.length - 1);
  const head = path.slice(0, headBudget);
  return { head: `${head}…/`, tail };
}

function FileListItem({ file, active, onSelect }: FileListItemProps) {
  const Icon = statusIcon(file.status, file.binary);
  const { head, tail } = truncatePath(file.path);
  const counts = formatChangeCount(file.additions, file.deletions);

  return (
    <button
      type="button"
      onClick={() => onSelect(file.path)}
      className={cn(
        'group flex w-full items-center gap-2 rounded-sm border border-transparent px-2 py-1 text-left text-[11.5px] transition-colors',
        active
          ? 'border-border-subtle bg-muted/60 text-foreground'
          : 'text-muted-foreground hover:bg-muted/30 hover:text-foreground',
      )}
      aria-current={active ? 'true' : undefined}
      title={file.path}
    >
      <Icon className={cn('size-3.5 shrink-0', statusTone(file.status))} />
      <span
        className={cn(
          'inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm font-mono text-[9px] uppercase tracking-tight',
          file.status === 'added' && 'bg-success/15 text-success',
          file.status === 'deleted' && 'bg-destructive/15 text-destructive',
          file.status === 'renamed' && 'bg-warning/15 text-warning',
          file.status === 'modified' && 'bg-muted text-meta-foreground',
          file.status === 'binary' && 'bg-muted text-meta-foreground',
        )}
        aria-hidden
      >
        {statusLabel(file.status)}
      </span>
      <span className="flex min-w-0 flex-1 items-baseline gap-0 truncate">
        {head && (
          <span className="truncate text-meta-foreground">{head}</span>
        )}
        <span className="truncate text-foreground">{tail}</span>
      </span>
      {file.binary ? (
        <span className="ml-auto shrink-0 font-mono text-[10px] uppercase text-meta-foreground">
          bin
        </span>
      ) : (
        <span className="ml-auto flex shrink-0 items-center gap-1 font-mono text-[10px]">
          {counts.additions && (
            <span className="text-success">{counts.additions}</span>
          )}
          {counts.deletions && (
            <span className="text-destructive">{counts.deletions}</span>
          )}
        </span>
      )}
    </button>
  );
}

export function DiffFileList({ files, activePath, onSelect, className }: Props) {
  const grouped = useMemo(() => {
    const byStatus: Record<DiffFileStatus, DiffFile[]> = {
      added: [],
      modified: [],
      renamed: [],
      deleted: [],
      binary: [],
    };
    for (const f of files) {
      byStatus[f.status].push(f);
    }
    return byStatus;
  }, [files]);

  if (files.length === 0) {
    return (
      <div
        className={cn(
          'flex h-full flex-col items-center justify-center gap-2 px-4 py-6 text-center',
          className,
        )}
      >
        <Files className="size-5 text-meta-foreground" aria-hidden />
        <div className="text-[12px] font-medium text-foreground">No files</div>
        <div className="text-[10.5px] text-muted-foreground">
          The diff is empty.
        </div>
      </div>
    );
  }

  const sections: Array<[string, DiffFile[]]> = [
    ['Added', grouped.added],
    ['Modified', grouped.modified],
    ['Renamed', grouped.renamed],
    ['Deleted', grouped.deleted],
    ['Binary', grouped.binary],
  ];

  return (
    <div
      className={cn('flex h-full flex-col overflow-y-auto px-1.5 py-1.5', className)}
      role="navigation"
      aria-label="Changed files"
    >
      {sections.map(([label, group]) =>
        group.length === 0 ? null : (
          <div key={label} className="mb-2">
            <div className="flex items-center justify-between px-1.5 py-1">
              <span className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-meta-foreground">
                {label}
              </span>
              <span className="font-mono text-[9.5px] text-meta-foreground">
                {group.length}
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              {group.map((f) => (
                <FileListItem
                  key={`${f.status}:${f.path}`}
                  file={f}
                  active={activePath === f.path}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </div>
        ),
      )}
    </div>
  );
}
