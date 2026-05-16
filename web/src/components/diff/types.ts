// Shared types for the Diff panel. These mirror the shape returned by the
// `/dashboard/tasks/{id}/diff` endpoint in
// `src/bernstein/core/routes/task_detail.py`.

export type DiffFileStatus =
  | 'added'
  | 'deleted'
  | 'renamed'
  | 'modified'
  | 'binary';

export interface DiffHunk {
  header: string;
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  lines: string[];
}

export interface DiffFile {
  path: string;
  old_path: string | null;
  status: DiffFileStatus;
  additions: number;
  deletions: number;
  binary: boolean;
  language: string | null;
  hunks: DiffHunk[];
}

export interface TaskDiffResponse {
  task_id: string;
  branch: string | null;
  base_ref: string;
  head_ref: string | null;
  additions: number;
  deletions: number;
  files: DiffFile[];
  unified: string;
  truncated: boolean;
  generated_at: number;
  note: string | null;
}

export type DiffViewMode = 'unified' | 'split';
