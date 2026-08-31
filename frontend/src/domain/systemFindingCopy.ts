/**
 * BUG-115 migration guard for F7 findings written before named-band copy
 * shipped. The raw similarity value remains in the finding detail/Audit;
 * only the precision-looking parenthetical in system-authored prose is
 * removed from instructor-facing text.
 *
 * Passage evidence is the manuscript's own quoted text and must never be
 * rewritten. Passage reasoning, whole-document evidence, and chapter
 * evidence are system-authored templates and are safe to migrate here.
 */
const LEGACY_REUSE_MATCH_PERCENT = /\s*\(\d+(?:\.\d+)?%\s+match\)/giu;

export function systemFindingCopy(
  text: string,
  kind: string | null | undefined,
  surface: "evidence" | "reasoning",
): string {
  if (!kind?.startsWith("reuse_")) return text;
  if (surface === "evidence" && kind.endsWith("_passage")) return text;
  return text.replace(LEGACY_REUSE_MATCH_PERCENT, "");
}
