import { describe, expect, it } from "vitest";
import {
  formatManuscriptOption,
  manuscriptIdentity,
  truncateForOption,
  UNGROUPED_LABEL,
} from "./manuscriptLabel";

describe("manuscriptIdentity", () => {
  it("BUG-022: two manuscripts with the same default group_label produce distinct primaries once a filename exists", () => {
    const a = manuscriptIdentity(UNGROUPED_LABEL, "Chapter1-3_FinalDefense.pdf");
    const b = manuscriptIdentity(UNGROUPED_LABEL, "Chapter1-3_Revised.pdf");
    expect(a.primary).not.toBe(b.primary);
    expect(a.primary).toBe("Chapter1-3_FinalDefense.pdf");
    expect(a.secondary).toBeNull();
  });

  it("falls back to the pre-migration display exactly when original_filename is null", () => {
    const identity = manuscriptIdentity("Group 3 CS", null);
    expect(identity).toEqual({ primary: "Group 3 CS", secondary: null });
  });

  it("keeps group_label primary and filename secondary when the label isn't the default", () => {
    const identity = manuscriptIdentity("Group 3 CS", "native.pdf");
    expect(identity).toEqual({ primary: "Group 3 CS", secondary: "native.pdf" });
  });
});

describe("truncateForOption", () => {
  it("leaves short strings untouched", () => {
    expect(truncateForOption("native.pdf")).toBe("native.pdf");
  });

  it("truncates a realistic long capstone filename with an ellipsis, never an em/en dash", () => {
    const long =
      "CHAPTER1-5_FINAL DEFENSE COPY_A Mobile-Based Automated Irrigation and Crop " +
      "Monitoring System Using IoT Sensors for Smallholder Rice Farmers_BSIT-4A-Group7_v3_FINAL_FINALrevised2.docx";
    const truncated = truncateForOption(long);
    expect(truncated.length).toBe(40);
    expect(truncated.endsWith("…")).toBe(true);
    expect(truncated).not.toMatch(/[–—]/); // en dash / em dash
  });
});

describe("formatManuscriptOption", () => {
  it("formats the two-line case with parentheses, not an em dash", () => {
    const identity = manuscriptIdentity("Group 3 CS", "native.pdf");
    const text = formatManuscriptOption(identity, "Aug 12, 2026, 3:04 PM");
    expect(text).toBe("Group 3 CS (native.pdf), uploaded Aug 12, 2026, 3:04 PM");
    expect(text).not.toMatch(/[–—]/);
  });

  it("formats the single-line case (Ungrouped + filename) with no parentheses", () => {
    const identity = manuscriptIdentity(UNGROUPED_LABEL, "native.pdf");
    const text = formatManuscriptOption(identity, "Aug 12, 2026, 3:04 PM");
    expect(text).toBe("native.pdf, uploaded Aug 12, 2026, 3:04 PM");
  });

  it("reproduces today's shipped string byte-for-byte when original_filename is null", () => {
    const identity = manuscriptIdentity(UNGROUPED_LABEL, null);
    const text = formatManuscriptOption(identity, "Aug 12, 2026, 3:04 PM");
    expect(text).toBe("Ungrouped, uploaded Aug 12, 2026, 3:04 PM");
  });

  it("caps a long filename even when it wins the primary slot with no secondary (ux-critic finding, BUG-022 review) -- the common case this fix targets, not just the parenthetical", () => {
    const longFilename =
      "Final_Manuscript_ChapterOneThroughFive_DefensePanel_Submission_Copy_v3_FINAL.pdf";
    const identity = manuscriptIdentity(UNGROUPED_LABEL, longFilename);
    const text = formatManuscriptOption(identity, "Aug 12, 2026, 3:04 PM");
    expect(text).toBe(`${truncateForOption(longFilename)}, uploaded Aug 12, 2026, 3:04 PM`);
    expect(text.startsWith(longFilename)).toBe(false);
  });
});
