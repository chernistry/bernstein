// Motion ladder - used by Framer Motion or any Tailwind-driven transition wrapper.
// Every animated component MUST reference these tokens, not invent its own.
//
// Per Bernstein design north star + v1/v2 UX research:
// - 80–100ms simple feedback (checkbox, toggle, hover ack)
// - 200–300ms substantial transitions (drawer enter, modal open)
// - ≤ 500ms ceiling for any one animation
// - ease-out for entrances; never ease-in
// - prefers-reduced-motion: reduce → disable non-essential motion

export const ease = {
  out: [0.16, 1, 0.3, 1] as const,
} as const;

export const duration = {
  feedback: 0.09,
  panel: 0.25,
  hard: 0.5,
} as const;

/**
 * Returns `true` if the user has enabled "reduce motion" in their OS.
 * Use to skip drawer slides, fades that aren't load indicators, etc.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
