/** Protocol authority for the assessment actually displayed to an instructor. */
export function isAssessmentRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function keepNewerAssessment<T extends { assessment_revision: number | null }>(
  current: T | undefined, incoming: T,
): T {
  return current && isAssessmentRevision(current.assessment_revision)
    && (!isAssessmentRevision(incoming.assessment_revision)
      || current.assessment_revision > incoming.assessment_revision) ? current : incoming;
}
