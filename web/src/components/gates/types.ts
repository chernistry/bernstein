// Shared types for the Gates panel. Mirrors the on-disk ``GateReport`` /
// ``GateResult`` dataclasses persisted by ``GateRunner._persist_report_sync``
// at ``.sdd/runtime/gates/<task_id>.json`` and served by
// ``GET /api/v1/tasks/{task_id}/gates``.

/**
 * Native gate statuses produced by the backend pipeline.
 *
 * The backend never emits ``"pending"`` on disk (gates are not pre-listed
 * before a run), but we keep the bucket here for forward-compat: a future
 * orchestrator may stream partial reports while a run is in progress, and the
 * UI already supports that path through the filter chips.
 */
export type GateStatus =
  | 'pass'
  | 'fail'
  | 'warn'
  | 'timeout'
  | 'skipped'
  | 'bypassed'
  | 'pending';

/** Coarse UI bucket used for sorting, chips, counts, and tone. */
export type GateBucket = 'failing' | 'pending' | 'passing' | 'skipped';

/** Coarse lifecycle bucket for the task itself - drives polling cadence. */
export type TaskLifecycle = 'active' | 'terminal' | 'unknown';

export interface GateResult {
  name: string;
  status: GateStatus;
  required: boolean;
  blocked: boolean;
  cached: boolean;
  duration_ms: number;
  details: string;
  metadata?: Record<string, unknown> | null;
}

export interface GateReport {
  task_id: string;
  overall_pass: boolean;
  total_duration_ms: number;
  gates_run: string[];
  results: GateResult[];
  changed_files: string[];
  cache_hits: number;
  /** ISO-8601 UTC timestamp added by the API from the report file mtime. */
  generated_at?: string | null;
  /** Current task lifecycle status string echoed by the API. */
  task_status?: string | null;
}
