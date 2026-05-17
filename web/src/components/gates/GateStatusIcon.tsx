// Single-status icon used in gate rows. Colour comes from the bucket so the
// row tone stays consistent with the chip and count strip.

import { AlertTriangle, CheckCircle2, CircleSlash, Clock, Loader2, XCircle } from 'lucide-react';

import { cn } from '@/lib/utils';

import { bucketFor } from './buckets';
import type { GateStatus } from './types';

interface Props {
  status: GateStatus;
  className?: string;
}

function classFor(status: GateStatus): string {
  switch (bucketFor(status)) {
    case 'failing':
      return 'text-destructive';
    case 'pending':
      return 'text-warning';
    case 'passing':
      return 'text-success';
    case 'skipped':
    default:
      return 'text-muted-foreground';
  }
}

export function GateStatusIcon({ status, className }: Props) {
  const cls = cn('size-3.5 shrink-0', classFor(status), className);
  switch (status) {
    case 'pass':
      return <CheckCircle2 className={cls} aria-hidden="true" />;
    case 'fail':
      return <XCircle className={cls} aria-hidden="true" />;
    case 'timeout':
      return <Clock className={cls} aria-hidden="true" />;
    case 'warn':
      return <AlertTriangle className={cls} aria-hidden="true" />;
    case 'pending':
      return <Loader2 className={cn(cls, 'animate-spin')} aria-hidden="true" />;
    case 'skipped':
    case 'bypassed':
    default:
      return <CircleSlash className={cls} aria-hidden="true" />;
  }
}
