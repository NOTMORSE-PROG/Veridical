// BUG-613: how much quoted evidence an automatic grade rests on, said on the
// card itself so "2 of 2 grading passes" never stands in for evidence. It
// states only what can be checked: the passages quoted and where they are.
import { pageRanges } from "./criterionSourceCoverage";
import { anchorLocationKey, instructorAnchor } from "./sourceAnchor";

export const GRADE_EVIDENCE_COPY = {
  label: "Quoted evidence:",
  none: "none recorded",
  one: "1 passage",
  many: "{count} passages",
  page: "page {pages}",
  pages: "pages {pages}",
  paragraph: "extracted paragraph {paragraphs}",
  paragraphs: "extracted paragraphs {paragraphs}",
  more: "and {count} more",
  unlocated: "{count} with no recorded location",
  noLocation: "no manuscript location recorded",
} as const;

// More page runs than this are summed up rather than listed.
const MAX_RUNS = 5;

const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (_match, key: string) => String(values[key]));

function runs(numbers: number[]): string {
  const all = pageRanges(numbers).split(", ");
  if (all.length <= MAX_RUNS) return all.join(", ");
  return `${all.slice(0, MAX_RUNS).join(", ")} ${fill(GRADE_EVIDENCE_COPY.more, { count: all.length - MAX_RUNS })}`;
}

const UNPLACED = "Reconstructed text (location unavailable)";

/** "1 passage (page 2)", "3 passages (pages 3, 7, 12)", "none recorded". */
export function gradeEvidenceValue(evidence: readonly { anchor: string | null | undefined }[]): string {
  if (evidence.length === 0) return GRADE_EVIDENCE_COPY.none;
  const pages = new Set<number>();
  const paragraphs = new Set<number>();
  const other: string[] = [];
  let unlocated = 0;
  for (const item of evidence) {
    const key = anchorLocationKey(item.anchor);
    const text = item.anchor?.trim() ?? "";
    if (key?.startsWith("page:")) pages.add(Number(key.slice(5)));
    else if (key?.startsWith("paragraph:")) {
      const shown = instructorAnchor(text);
      if (shown === UNPLACED) unlocated += 1; else paragraphs.add(Number(key.slice(10)) + 1);
    } else if (text) {
      const shown = instructorAnchor(text);
      if (shown === UNPLACED) unlocated += 1; else if (!other.includes(shown)) other.push(shown);
    } else unlocated += 1;
  }
  const count = evidence.length === 1 ? GRADE_EVIDENCE_COPY.one : fill(GRADE_EVIDENCE_COPY.many, { count: evidence.length });
  const groups: string[] = [];
  if (pages.size) groups.push(fill(pages.size === 1 ? GRADE_EVIDENCE_COPY.page : GRADE_EVIDENCE_COPY.pages, { pages: runs([...pages]) }));
  if (paragraphs.size) groups.push(fill(paragraphs.size === 1 ? GRADE_EVIDENCE_COPY.paragraph : GRADE_EVIDENCE_COPY.paragraphs, { paragraphs: runs([...paragraphs]) }));
  groups.push(...other);
  if (!groups.length) return `${count} (${GRADE_EVIDENCE_COPY.noLocation})`;
  if (unlocated) groups.push(fill(GRADE_EVIDENCE_COPY.unlocated, { count: unlocated }));
  return `${count} (${groups.join("; ")})`;
}
