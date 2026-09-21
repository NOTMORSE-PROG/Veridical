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
    verificationPrompt: "Recalculate the reported values from the shown sample size and summary statistics, then check for a rounding or transcription issue.",
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
