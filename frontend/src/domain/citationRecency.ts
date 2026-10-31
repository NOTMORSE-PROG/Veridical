// V-092: what the citation recency note says about a run. Counts, never a
// percentage, and a source is never called wrong for being old: the note
// only says how many fall outside the window this format sets.
import type { CitationRecencySummaryOut } from "../api/types";

export const CITATION_RECENCY_COPY = {
  label: "Citation recency",
  notAssessed: "not assessed",
  notChecked: "not checked",
  offByFormat: "This format turns the recency note off.",
  offByDefault: "The recency note is not turned on for this format.",
  counted: "These notes count toward readiness as one finding.",
  notCounted: "These notes do not count toward readiness.",
  details: "How this was counted",
  // Only a counted note has a Dismiss action, so only then is it offered.
  oldCanBeRightCounted: "An older source can still be the right one to cite, for example a foundational work; dismiss its note with a reason if so.",
  oldCanBeRight: "An older source can still be the right one to cite, for example a foundational work.",
  unassessedReason: {
    reference_list_not_bound: "the reference list could not be told apart from the text around it",
    reference_membership_partial: "only part of the reference list could be confirmed",
    reference_list_unavailable: "no reference list was read",
    source_unavailable: "the manuscript text was not available",
    no_references: "no references were found",
    format_setting_invalid: "this format's recency setting is no longer valid; open the format to set it again",
    recency_policy_unavailable: "the recency setting could not be read",
  } as Record<string, string>,
  fallbackReason: {
    manuscript_year_not_found: "the manuscript's own year was not found on its title page",
    manuscript_year_ambiguous: "the title page gives more than one year",
    manuscript_year_after_check_date: "the year on the title page is later than the check date",
    manuscript_year_precedes_cited_source: "a cited source is dated after the year on the title page",
  } as Record<string, string>,
  // [said of one reference, said after a count]
  unreadableReason: {
    no_date: ["marked n.d. or in press", "marked n.d. or in press"],
    no_year: ["no year given", "with no year given"],
    conflicting_years: ["more than one year given", "with more than one year given"],
    no_text: ["no entry text", "with no entry text"],
  } as Record<string, [string, string]>,
  unreadableOther: ["year not readable", "with a year that could not be read"] as [string, string],
} as const;

// Reference numbers listed before the rest are summed up.
const LISTED_REFERENCES = 10;

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

function listNumbers(numbers: number[]): string {
  const shown = numbers.slice(0, LISTED_REFERENCES);
  const more = numbers.length - shown.length;
  if (more > 0) return `${shown.join(", ")} and ${more} more`;
  if (shown.length <= 1) return shown.join("");
  return `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}

// Where the window counts back from, in the instructor's words.
function referenceYearPhrase(recency: CitationRecencySummaryOut): string {
  const year = recency.reference_year;
  if (recency.reference_year_basis === "manuscript_year") {
    const evidence = recency.reference_year_evidence_text?.trim();
    const anchor = recency.reference_year_evidence_anchor?.trim();
    return evidence
      ? `${year}, the manuscript's year (read from "${evidence}"${anchor ? `, ${anchor}` : ""})`
      : `${year}, the manuscript's year`;
  }
  const fallback = recency.reference_year_fallback_reason ? CITATION_RECENCY_COPY.fallbackReason[recency.reference_year_fallback_reason] : undefined;
  return fallback ? `${year}, the year this check ran, because ${fallback}` : `${year}, the year this check ran`;
}

export interface CitationRecencyNote {
  title: string;
  // Said beside the title.
  summary: string[];
  // Behind "How this was counted".
  details: string[];
}

export function citationRecencyNote(recency: CitationRecencySummaryOut | null | undefined): CitationRecencyNote | null {
  if (!recency) return null;
  const { label } = CITATION_RECENCY_COPY;
  if (recency.status === "off") {
    return { title: `${label}: ${CITATION_RECENCY_COPY.notChecked}`,
      summary: [recency.enabled_source === "format" ? CITATION_RECENCY_COPY.offByFormat : CITATION_RECENCY_COPY.offByDefault], details: [] };
  }
  if (recency.status === "unassessed") {
    const reason = recency.unassessed_reason ? CITATION_RECENCY_COPY.unassessedReason[recency.unassessed_reason] : undefined;
    return { title: `${label}: ${CITATION_RECENCY_COPY.notAssessed}`,
      summary: [reason ? `Source dates were not read because ${reason}.` : "Source dates were not read for this run."], details: [] };
  }
  const window = recency.window_years ?? 0;
  const oldest = recency.oldest_year_in_window ?? (recency.reference_year != null ? recency.reference_year - window : null);
  const summary: string[] = [];
  const details: string[] = [];
  if (recency.counts_toward_readiness != null && recency.n_outside_window > 0) summary.push(recency.counts_toward_readiness ? CITATION_RECENCY_COPY.counted : CITATION_RECENCY_COPY.notCounted);
  if (recency.reference_year != null && oldest != null) {
    details.push(`The ${window}-year window counts back from ${referenceYearPhrase(recency)}: sources published in ${oldest} or later are inside it, and sources published before ${oldest} are noted.`);
  }
  if (recency.n_year_not_readable > 0) {
    const numbers = recency.year_not_readable_reference_numbers ?? [];
    const reasons = Object.entries(recency.year_not_readable_reasons ?? {})
      .filter(([, count]) => count > 0)
      .map(([reason, count]) => ({ words: CITATION_RECENCY_COPY.unreadableReason[reason] ?? CITATION_RECENCY_COPY.unreadableOther, count }));
    const which = numbers.length ? `${numbers.length === 1 ? "reference" : "references"} ${listNumbers(numbers)}` : "";
    const why = reasons.length === 1 ? reasons[0].words[0] : reasons.map((reason) => `${reason.count} ${reason.words[1]}`).join(", ");
    const inside = [which, why].filter(Boolean).join(numbers.length === 1 && reasons.length === 1 ? ": " : "; ");
    const lead = plural(recency.n_year_not_readable, "source has", "sources have");
    summary.push(`${lead} no readable year and ${recency.n_year_not_readable === 1 ? "is" : "are"} not judged${inside ? ` (${inside})` : ""}.`);
  }
  if (recency.n_dated_after_reference_year > 0 && recency.reference_year != null) {
    details.push(`${plural(recency.n_dated_after_reference_year, "dated source is", "dated sources are")} later than ${recency.reference_year}; ${recency.n_dated_after_reference_year === 1 ? "it counts" : "they count"} as inside the window.`);
  }
  if (recency.n_outside_window > 0) details.push(recency.counts_toward_readiness ? CITATION_RECENCY_COPY.oldCanBeRightCounted : CITATION_RECENCY_COPY.oldCanBeRight);
  const outside = recency.n_outside_window;
  // The check date as the starting point moves the count, so it is said up front.
  const fromCheckDate = recency.reference_year_basis === "check_date" && recency.reference_year != null ? `, counted from the check date (${recency.reference_year})` : "";
  const title = recency.n_dated === 0
    ? `${label}: no dated sources to compare with the ${window}-year window`
    : `${label}: ${outside} of ${plural(recency.n_dated, "dated source", "dated sources")} ${outside === 1 ? "is" : "are"} older than the ${window}-year window${fromCheckDate}`;
  return { title, summary, details };
}
