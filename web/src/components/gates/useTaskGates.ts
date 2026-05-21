// Polling fetch for ``/tasks/{id}/gates`` backed by react-query.
//
// While the task is still live we refetch on a 6s cadence so the operator
// sees gates flip status without manual reload. As soon as the report says
// the task is terminal (``done`` / ``failed`` / ``cancelled``) we stop the
// timer - terminal reports are immutable on disk.

import { useQuery } from '@tanstack/react-query';

import { apiGet, ApiError } from '@/lib/api';

import type { GateReport, TaskLifecycle } from './types';

const ACTIVE_POLL_MS = 6_000;
const TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled', 'closed']);

export interface UseTaskGatesOptions {
  taskId: string;
  /** When false the query is disabled (e.g. the Gates tab isn't visible). */
  enabled?: boolean;
}

export interface UseTaskGatesResult {
  report: GateReport | null;
  /** True for the very first load only - drives the skeleton, not refetches. */
  initialLoading: boolean;
  isRefetching: boolean;
  isMissing: boolean;
  error: Error | null;
  refetch: () => Promise<unknown>;
  lifecycle: TaskLifecycle;
}

function lifecycleFor(report: GateReport | null): TaskLifecycle {
  const status = report?.task_status;
  if (status && TERMINAL_STATUSES.has(status)) return 'terminal';
  if (status) return 'active';
  return 'unknown';
}

export function useTaskGates({ taskId, enabled = true }: UseTaskGatesOptions): UseTaskGatesResult {
  const query = useQuery<GateReport | null, Error>({
    queryKey: ['task-gates', taskId],
    enabled: enabled && taskId.length > 0,
    queryFn: async () => {
      try {
        return await apiGet<GateReport>(`/tasks/${encodeURIComponent(taskId)}/gates`);
      } catch (err) {
        // 404 means "no report yet" - render the empty state instead of an
        // error banner. Any other error still propagates so the error state
        // can offer Retry.
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    refetchInterval: (q) => {
      const data = q.state.data ?? null;
      return lifecycleFor(data) === 'terminal' ? false : ACTIVE_POLL_MS;
    },
    refetchOnWindowFocus: true,
    staleTime: 1_500,
    // We deliberately keep the previous payload visible during refetch so the
    // list does not flash empty between polls.
    placeholderData: (prev) => prev,
  });

  return {
    report: query.data ?? null,
    initialLoading: query.isLoading && !query.data,
    isRefetching: query.isFetching && !query.isLoading,
    isMissing: !query.isLoading && !query.isError && query.data == null,
    error: query.error,
    refetch: query.refetch,
    lifecycle: lifecycleFor(query.data ?? null),
  };
}
