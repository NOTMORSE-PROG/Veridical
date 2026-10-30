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
  counted: "These notes count toward readiness.",
  notCounted: "These notes do not count toward readiness.",
  oldCanBeRight: "An older source can still be the right one to cite, for example a foundational work; dismiss its note with a reason if so.",
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
  unreadableReason: {
    no_date: "marked n.d. or in press",
    no_year: "no year given",
    conflicting_years: "more than one year",
    no_text: "no entry text",
  } as Record<string, string>,
} as const;

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

function listNumbers(numbers: number[]): string {
  if (numbers.length <= 1) return numbers.join("");
  return `${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
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
  lines: string[];
}

export function citationRecencyNote(recency: CitationRecencySummaryOut | null | undefined): CitationRecencyNote | null {
  if (!recency) return null;
  const { label } = CITATION_RECENCY_COPY;
  if (recency.status === "off") {
    return { title: `${label}: ${CITATION_RECENCY_COPY.notChecked}`,
      lines: [recency.enabled_source === "format" ? CITATION_RECENCY_COPY.offByFormat : CITATION_RECENCY_COPY.offByDefault] };
  }
  if (recency.status === "unassessed") {
    const reason = recency.unassessed_reason ? CITATION_RECENCY_COPY.unassessedReason[recency.unassessed_reason] : undefined;
    return { title: `${label}: ${CITATION_RECENCY_COPY.notAssessed}`,
      lines: [reason ? `Source dates were not read because ${reason}.` : "Source dates were not read for this run."] };
  }
  const window = recency.window_years ?? 0;
  const lines: string[] = [];
  if (recency.reference_year != null) {
    lines.push(`The ${window}-year window counts back from ${referenceYearPhrase(recency)}, so sources from ${recency.oldest_year_in_window ?? recency.reference_year - window} onward are inside it.`);
  }
  if (recency.n_year_not_readable > 0) {
    const reasons = Object.entries(recency.year_not_readable_reasons ?? {})
      .filter(([, count]) => count > 0)
      .map(([reason, count]) => `${CITATION_RECENCY_COPY.unreadableReason[reason] ?? reason}: ${count}`);
    const numbers = recency.year_not_readable_reference_numbers ?? [];
    const which = numbers.length ? ` (${numbers.length === 1 ? "reference" : "references"} ${listNumbers(numbers)})` : "";
    lines.push(`${plural(recency.n_year_not_readable, "source has", "sources have")} no readable year${which} and ${recency.n_year_not_readable === 1 ? "is" : "are"} not judged${reasons.length ? `: ${reasons.join("; ")}` : ""}.`);
  }
  if (recency.n_dated_after_reference_year > 0 && recency.reference_year != null) {
    lines.push(`${plural(recency.n_dated_after_reference_year, "dated source is", "dated sources are")} later than ${recency.reference_year}.`);
  }
  if (recency.counts_toward_readiness != null) lines.push(recency.counts_toward_readiness ? CITATION_RECENCY_COPY.counted : CITATION_RECENCY_COPY.notCounted);
  if (recency.n_outside_window > 0) lines.push(CITATION_RECENCY_COPY.oldCanBeRight);
  const outside = recency.n_outside_window;
  const title = recency.n_dated === 0
    ? `${label}: no dated sources to compare with the ${window}-year window`
    : `${label}: ${outside} of ${plural(recency.n_dated, "dated source", "dated sources")} ${outside === 1 ? "is" : "are"} older than the ${window}-year window`;
  return { title, lines };
}
