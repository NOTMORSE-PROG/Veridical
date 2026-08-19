// V-064 (AC2/AC3): whether a rubric is eligible for a manuscript, purely
// by program. Permissive by default (AC3) -- either side unset (null or
// undefined -- undefined covers "not yet loaded/selected") stays fully
// eligible, never a lock-out just because a program hasn't been
// configured. A real block fires only when BOTH sides are set and
// disagree. This is display/filtering logic only (ground rule 7's
// engine-behavior guard, V-064 Q3) -- it decides what the instructor is
// OFFERED, never how a check is graded.
export function isRubricEligibleForProgram(
  manuscriptProgram: string | null | undefined,
  rubricProgram: string | null | undefined,
): boolean {
  return !manuscriptProgram || !rubricProgram || manuscriptProgram === rubricProgram;
}
