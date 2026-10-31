import { describe, expect, it } from "vitest";
import type { CitationRecencySummaryOut } from "../api/types";
import { citationRecencyNote } from "./citationRecency";

const ASSESSED: CitationRecencySummaryOut = {
  status: "assessed", window_years: 5, window_source: "format", counts_toward_readiness: false,
  reference_year: 2023, reference_year_basis: "manuscript_year",
  reference_year_evidence_text: "December 2023", reference_year_evidence_anchor: "p. 1",
  oldest_year_in_window: 2018, n_references: 21, n_dated: 18, n_outside_window: 2, n_within_window: 16,
  n_dated_after_reference_year: 0, n_year_not_readable: 1, year_not_readable_reasons: { no_date: 1 },
  year_not_readable_reference_numbers: [7],
};

const all = (recency: CitationRecencySummaryOut) => {
  const note = citationRecencyNote(recency)!;
  return [note.title, ...note.summary, ...note.details].join(" ");
};

describe("the citation recency note", () => {
  it("leads with the count and whether it counts, and keeps how it was counted one step away", () => {
    const note = citationRecencyNote(ASSESSED)!;
    expect(note.title).toBe("Citation recency: 2 of 18 dated sources are older than the 5-year window");
    expect(note.summary).toEqual([
      "These notes do not count toward readiness.",
      "1 source has no readable year and is not judged (reference 7: marked n.d. or in press).",
    ]);
    expect(note.details[0]).toBe(`The 5-year window counts back from 2023, the manuscript's year (read from "December 2023", p. 1): sources published in 2018 or later are inside it, and sources published before 2018 are noted.`);
  });

  it("never calls an old source wrong, and offers dismissal only where a note can be dismissed", () => {
    expect(all(ASSESSED)).not.toMatch(/\b(wrong|invalid|outdated|bad|error)\b|%/i);
    expect(all(ASSESSED)).toContain("An older source can still be the right one to cite, for example a foundational work.");
    expect(all(ASSESSED)).not.toContain("dismiss");
    expect(all({ ...ASSESSED, counts_toward_readiness: true })).toContain("dismiss its note with a reason if so");
    expect(citationRecencyNote({ ...ASSESSED, counts_toward_readiness: true })!.summary[0]).toBe("These notes count toward readiness as one finding.");
  });

  it("says up front when the window counts from the check date, and why", () => {
    const note = citationRecencyNote({ ...ASSESSED, reference_year: 2026, reference_year_basis: "check_date",
      reference_year_evidence_text: null, reference_year_fallback_reason: "manuscript_year_not_found", oldest_year_in_window: 2021 })!;
    expect(note.title).toBe("Citation recency: 2 of 18 dated sources are older than the 5-year window, counted from the check date (2026)");
    expect(note.details[0]).toContain("2026, the year this check ran, because the manuscript's own year was not found on its title page");
  });

  it("uses the singular for one source and drops the older-source hint when none is old", () => {
    expect(citationRecencyNote({ ...ASSESSED, n_outside_window: 1 })!.title).toBe("Citation recency: 1 of 18 dated sources is older than the 5-year window");
    expect(all({ ...ASSESSED, n_outside_window: 0 })).not.toContain("An older source");
  });

  it("names unreadable references with their reasons, caps a long list, and says later sources are inside", () => {
    const note = citationRecencyNote({ ...ASSESSED, n_year_not_readable: 3, year_not_readable_reasons: { no_date: 2, conflicting_years: 1 },
      year_not_readable_reference_numbers: [4, 9, 12], n_dated_after_reference_year: 1 })!;
    expect(note.summary).toContain("3 sources have no readable year and are not judged (references 4, 9 and 12; 2 marked n.d. or in press, 1 with more than one year given).");
    expect(note.details).toContain("1 dated source is later than 2023; it counts as inside the window.");
    const many = citationRecencyNote({ ...ASSESSED, n_year_not_readable: 14, year_not_readable_reasons: { no_year: 14 },
      year_not_readable_reference_numbers: Array.from({ length: 14 }, (_, index) => index + 1) })!;
    expect(many.summary[1]).toBe("14 sources have no readable year and are not judged (references 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 and 4 more; no year given).");
  });

  it("says nothing about readiness when no source is old, and never prints a raw reason code", () => {
    expect(all({ ...ASSESSED, n_outside_window: 0 })).not.toMatch(/count toward readiness/);
    expect(all({ ...ASSESSED, year_not_readable_reasons: { something_new: 1 } })).toContain("(reference 7: year not readable)");
  });

  it("says why the list was not read, and whether the format turned the note off", () => {
    expect(citationRecencyNote({ ...ASSESSED, status: "unassessed", unassessed_reason: "reference_list_not_bound" })).toEqual({
      title: "Citation recency: not assessed", summary: ["Source dates were not read because the reference list could not be told apart from the text around it."], details: [] });
    expect(citationRecencyNote({ ...ASSESSED, status: "off", enabled_source: "format" })!.summary).toEqual(["This format turns the recency note off."]);
    expect(citationRecencyNote({ ...ASSESSED, status: "off", enabled_source: "default" })!.summary).toEqual(["The recency note is not turned on for this format."]);
    expect(citationRecencyNote(null)).toBeNull();
  });
});
