// Single source of truth for how a check_kind (F4-F7 integrity checks)
// maps to display strings — extracted from FlagDetail.tsx (BUG-033) so it
// and SignalDocumentViewer.tsx (both call `checkKindMeta()`) can never
// independently drift on the same vocabulary, same reasoning as
// readinessTone.ts's own extraction.
interface CheckKindMeta {
  eyebrow: string;
  title: string;
  verificationPrompt: string;
  verificationPromptWithoutComparison?: string;
}

export const CHECK_KIND_META: Record<string, CheckKindMeta> = {
  internal_agreement: {
    eyebrow: "Internal agreement check",
    title: "Possible internal contradiction",
    verificationPrompt: "Compare the linked manuscript locations and confirm whether they describe the same item consistently.",
  },
  citation_integrity: {
    eyebrow: "Citation integrity check",
    title: "Possible citation issue",
    verificationPrompt: "Open the cited source and confirm that its author, title, year, and attached claim match the manuscript.",
  },
  statistical_forensics: {
    eyebrow: "Statistical forensics check",
    title: "Possible statistical inconsistency",
    verificationPrompt: "Check the recorded numbers and calculation against the original analysis output. This check does not verify the raw data.",
  },
  originality_reuse: {
    eyebrow: "Originality and reuse check",
    title: "Possible content overlap",
    verificationPrompt: "Compare the passages and decide whether the shared wording is expected citation, common phrasing, or possible reuse.",
    verificationPromptWithoutComparison: "Review the recorded scope and technical details, then decide whether the possible overlap has a legitimate source or explanation.",
  },
};

// A shorter label for the flags panel's group headers (BUG-033) — the
// eyebrow strings above read naturally in a detail-page header but are
// too long for a repeated group-row label.
export const CHECK_KIND_SHORT_LABEL: Record<string, string> = {
  internal_agreement: "Internal agreement",
  citation_integrity: "Citation integrity",
  statistical_forensics: "Statistical forensics",
  originality_reuse: "Originality and reuse",
};

// Fixed F4->F7 declaration order — flags panel groups never reorder as
// severities change (Jakob's Law / recognition over recall, DESIGN.md).
export const CHECK_KIND_ORDER = [
  "internal_agreement",
  "citation_integrity",
  "statistical_forensics",
  "originality_reuse",
] as const;

// Sentence case ("Not supported"), matching CHECK_KIND_META's own
// hand-written eyebrow strings ("Internal agreement check") — title
// case ("Not Supported") reads like two proper nouns in running text.
export function humanize(snake: string): string {
  const words = snake.split("_");
  return words.map((w, i) => (i === 0 ? w[0]?.toUpperCase() + w.slice(1) : w)).join(" ");
}

export function checkKindMeta(kind: string): CheckKindMeta {
  return CHECK_KIND_META[kind] ?? {
    eyebrow: humanize(kind),
    title: "Possible inconsistency",
    verificationPrompt: "Check the recorded evidence against the manuscript before deciding whether this finding needs action.",
  };
}

export function isPValueFinding(kind: string | null | undefined): boolean {
  return kind === "p_value_mismatch" || kind === "p_value_decision_error";
}

export function findingVerificationPrompt(checkKind: string, problemKind: string | null, hasPair: boolean, hasCitationComparison = false): string {
  if (checkKind === "citation_integrity" && problemKind === "possible_metadata_mismatch") {
    return hasCitationComparison
      ? "Compare the reference with the saved provider metadata. Open the DOI source to check which work the identifier names, then decide whether the reference needs correction. This comparison does not verify support for a manuscript claim."
      : "This historical finding has no saved provider comparison. Check the original reference and DOI independently, or run a new check to create a new evidence record. Source existence does not establish support for a manuscript claim.";
  }
  if (checkKind === "internal_agreement") {
    return problemKind === "agreement_unmatched_intent"
      ? "Check whether this statement is this study's objective. Read the results and surrounding text to see whether it was addressed; no paired result was recorded by the check."
      : "Check that both statements describe this study and refer to the same objective. Read the surrounding text before deciding whether the result addresses the objective.";
  }
  if (problemKind === "orphan_in_text_citation") {
    return "Check the manuscript's reference list for this author and year. Confirm whether the entry is missing or uses different spelling or a different year.";
  }
  if (problemKind === "uncited_reference") {
    return "Check whether this reference is cited in the manuscript text, tables, or captions. Citation spelling and style may differ. If you cannot find a matching citation, decide whether the reference should be cited or removed.";
  }
  if (isPValueFinding(problemKind)) {
    return "Compare the reported p-value with the saved recalculation. Check the original analysis output for the test value, degrees of freedom, and whether the test was one-sided or two-sided. This check does not verify the raw data.";
  }
  const meta = checkKindMeta(checkKind);
  return hasPair ? meta.verificationPrompt : meta.verificationPromptWithoutComparison ?? meta.verificationPrompt;
}
