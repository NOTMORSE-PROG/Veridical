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

describe("the citation recency note", () => {
  it("counts sources outside the window and says where the window starts", () => {
    const note = citationRecencyNote(ASSESSED)!;
    expect(note.title).toBe("Citation recency: 2 of 18 dated sources are older than the 5-year window");
    expect(note.lines).toContain(`The 5-year window counts back from 2023, the manuscript's year (read from "December 2023", p. 1), so sources from 2018 onward are inside it.`);
    expect(note.lines).toContain("1 source has no readable year (reference 7) and is not judged: marked n.d. or in press: 1.");
    expect(note.lines).toContain("These notes do not count toward readiness.");
  });

  it("never calls an old source wrong, and offers to keep a foundational one", () => {
    const text = [citationRecencyNote(ASSESSED)!.title, ...citationRecencyNote(ASSESSED)!.lines].join(" ");
    expect(text).not.toMatch(/\b(wrong|invalid|outdated|bad|error)\b/i);
    expect(text).toContain("An older source can still be the right one to cite");
    expect(text).not.toContain("%");
  });

  it("says when the check date was used and why", () => {
    const note = citationRecencyNote({ ...ASSESSED, reference_year: 2026, reference_year_basis: "check_date",
      reference_year_evidence_text: null, reference_year_fallback_reason: "manuscript_year_not_found", oldest_year_in_window: 2021 })!;
    expect(note.lines[0]).toBe("The 5-year window counts back from 2026, the year this check ran, because the manuscript's own year was not found on its title page, so sources from 2021 onward are inside it.");
  });

  it("uses the singular for one source and drops the dismiss hint when none is old", () => {
    expect(citationRecencyNote({ ...ASSESSED, n_outside_window: 1 })!.title).toBe("Citation recency: 1 of 18 dated sources is older than the 5-year window");
    expect(citationRecencyNote({ ...ASSESSED, n_outside_window: 0 })!.lines.join(" ")).not.toContain("An older source");
  });

  it("names several unreadable references and later-dated sources", () => {
    const note = citationRecencyNote({ ...ASSESSED, n_year_not_readable: 3, year_not_readable_reasons: { no_date: 2, conflicting_years: 1 },
      year_not_readable_reference_numbers: [4, 9, 12], n_dated_after_reference_year: 1 })!;
    expect(note.lines).toContain("3 sources have no readable year (references 4, 9 and 12) and are not judged: marked n.d. or in press: 2; more than one year: 1.");
    expect(note.lines).toContain("1 dated source is later than 2023.");
  });

  it("says why the list was not read, and whether the format turned the note off", () => {
    expect(citationRecencyNote({ ...ASSESSED, status: "unassessed", unassessed_reason: "reference_list_not_bound" })).toEqual({
      title: "Citation recency: not assessed", lines: ["Source dates were not read because the reference list could not be told apart from the text around it."] });
    expect(citationRecencyNote({ ...ASSESSED, status: "off", enabled_source: "format" })!.lines).toEqual(["This format turns the recency note off."]);
    expect(citationRecencyNote({ ...ASSESSED, status: "off", enabled_source: "default" })!.lines).toEqual(["The recency note is not turned on for this format."]);
    expect(citationRecencyNote(null)).toBeNull();
  });
});
