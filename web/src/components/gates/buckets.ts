// Status → bucket projection and ordering helpers for the Gates panel.
//
// The on-disk backend uses six raw statuses (pass / fail / warn / timeout /
// skipped / bypassed) plus a synthetic "pending" we surface when a partial
// report is in flight. Five of those collapse into four operator-friendly
// buckets used everywhere in the UI: chips, counts, tone, sort order.

import type { GateBucket, GateResult, GateStatus } from './types';

/** Project a raw backend status onto the coarse UI bucket. */
export function bucketFor(status: GateStatus): GateBucket {
  switch (status) {
    case 'fail':
    case 'timeout':
      return 'failing';
    case 'warn':
      // ``warn`` is non-blocking by design but still wants the operator's
      // attention - group with pending so it sits above the green wall.
      return 'pending';
    case 'pending':
      return 'pending';
    case 'pass':
      return 'passing';
    case 'skipped':
    case 'bypassed':
    default:
      return 'skipped';
  }
}

/**
 * Treat a result as failing when it actually broke the build (``blocked``)
 * OR when its status alone implies failure. Non-required failures still land
 * in the failing bucket because operators want to see them - they just don't
 * gate completion.
 */
export function isFailing(r: GateResult): boolean {
  return r.blocked || r.status === 'fail' || r.status === 'timeout';
}

/** Sort rank - failing first, then pending/warn, then passing, then skipped. */
const BUCKET_RANK: Record<GateBucket, number> = {
  failing: 0,
  pending: 1,
  passing: 2,
  skipped: 3,
};

export function bucketRank(bucket: GateBucket): number {
  return BUCKET_RANK[bucket];
}

/**
 * Comparator with stable secondary keys: required gates above advisory ones
 * inside the same bucket, then alphabetical by name so the order is
 * deterministic across refetches.
 */
export function compareResults(a: GateResult, b: GateResult): number {
  const ba = bucketFor(a.status);
  const bb = bucketFor(b.status);
  if (ba !== bb) return BUCKET_RANK[ba] - BUCKET_RANK[bb];
  if (a.required !== b.required) return a.required ? -1 : 1;
  return a.name.localeCompare(b.name);
}

/** Tally results into per-bucket counts for the header strip. */
export function tallyBuckets(results: GateResult[]): Record<GateBucket, number> {
  const counts: Record<GateBucket, number> = {
    failing: 0,
    pending: 0,
    passing: 0,
    skipped: 0,
  };
  for (const r of results) {
    counts[bucketFor(r.status)] += 1;
  }
  return counts;
}

/** Filter ``results`` by an active bucket selection. Empty = show all. */
export function filterByBuckets(
  results: GateResult[],
  active: Set<GateBucket>,
): GateResult[] {
  if (active.size === 0) return results;
  return results.filter((r) => active.has(bucketFor(r.status)));
}

/** Display label per bucket - used in chips, headers, and ARIA. */
export const BUCKET_LABEL: Record<GateBucket, string> = {
  failing: 'failing',
  pending: 'pending',
  passing: 'passing',
  skipped: 'skipped',
};

/** Short verb used in count pills ("4 passing", "2 failing"). */
export const BUCKET_VERB: Record<GateBucket, string> = {
  failing: 'failing',
  pending: 'pending',
  passing: 'passing',
  skipped: 'skipped',
};

/** Human-readable label per raw status, used inside row metadata. */
export const STATUS_LABEL: Record<GateStatus, string> = {
  pass: 'pass',
  fail: 'fail',
  warn: 'warn',
  timeout: 'timeout',
  skipped: 'skipped',
  bypassed: 'bypassed',
  pending: 'pending',
};
