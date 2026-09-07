import type { FlagOut } from "../api/types";

/**
 * BUG-170: a whole-document/chapter/resubmission originality-reuse flag's
 * `evidence_excerpt` is always a system-authored template sentence, never
 * a quote from the manuscript (`systemFindingCopy.ts`'s own docstring
 * already draws this line) -- every screen that renders a flag's evidence
 * must agree on which flags get the quote treatment and which get the
 * honest "system record" treatment instead. Shared here (not duplicated
 * per screen) after `ux-critic` found `FlagDetail.tsx` and
 * `SignalDocumentViewer.tsx` had drifted: the first screen fixed this
 * ticket's own defect, the second -- rendering the identical flag via the
 * same `useFlag` hook -- still rendered the fake quote unconditionally.
 */
// The narrowest form of the question, usable by both `FlagOut` (the full
// evidence detail payload) and `FlagSummaryOut` (the report list's lighter
// row shape, which carries `check_kind`/`is_passage_level` but no
// `ai_reasoning`/`passage_pair`) -- a structural `Pick`, not tied to
// either concrete type, so a third screen can reuse just this half
// without needing the full evidence-page shape `evidenceDisplayState`
// below computes.
export function isManuscriptQuote(flag: { check_kind: string; is_passage_level: boolean }): boolean {
  return flag.check_kind !== "originality_reuse" || flag.is_passage_level;
}

export interface EvidenceDisplayState {
  showsManuscriptQuote: boolean;
  // Falls back to evidence_excerpt only for a non-quote reuse flag -- the
  // dedup that usually nulls `ai_reasoning` when it's identical to
  // evidence_excerpt (BUG-112) would otherwise leave a screen with NO
  // explanatory sentence at all once the fake quote is removed.
  reasoningText: string | null;
  showsPassagePanel: boolean;
  showsManuscriptEvidence: boolean;
  isResubmission: boolean;
}

export function evidenceDisplayState(
  flag: Pick<FlagOut, "check_kind" | "is_passage_level" | "ai_verdict_summary" | "ai_reasoning" | "evidence_excerpt" | "passage_pair">,
): EvidenceDisplayState {
  const showsManuscriptQuote = isManuscriptQuote(flag);
  const reasoningText = showsManuscriptQuote ? flag.ai_reasoning : (flag.ai_reasoning ?? flag.evidence_excerpt);
  const showsPassagePanel = !!flag.passage_pair;
  return {
    showsManuscriptQuote,
    reasoningText,
    showsPassagePanel,
    showsManuscriptEvidence: showsManuscriptQuote || showsPassagePanel,
    isResubmission: flag.ai_verdict_summary === "reuse_same_instructor_resubmission",
  };
}
