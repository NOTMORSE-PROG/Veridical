import { describe, expect, it } from "vitest";
import { validateIngestReceipt } from "../api/uploadOutcome";
import { imageReadingLine } from "./uploadImageReading";

const reading = (checked: number, with_content: number, not_checked: number, no_cue: number) =>
  ({ checked, with_content, not_checked, no_cue });

describe("upload image reading line", () => {
  it("never reports a pass that read nothing as done", () => {
    expect(imageReadingLine({ images: 81, image_reading: reading(12, 0, 69, 0) }))
      .toBe("81 images found; 12 checked for tables or equations, none had one; 69 not checked.");
    expect(imageReadingLine({ images: 81, image_reading: reading(0, 0, 81, 81) }))
      .toBe("81 images found; none checked for tables or equations; 81 not checked (no table or equation caption beside them).");
  });

  it("names the images without a cue apart from other unchecked ones", () => {
    expect(imageReadingLine({ images: 20, image_reading: reading(3, 2, 17, 15) }))
      .toBe("20 images found; 3 checked for tables or equations, 2 had one; 17 not checked (15 with no table or equation caption beside them).");
    expect(imageReadingLine({ images: 1, image_reading: reading(1, 1, 0, 0) }))
      .toBe("1 image found; 1 checked for tables or equations, 1 had one.");
  });

  it("says nothing without counts or images", () => {
    expect(imageReadingLine({ images: 4 })).toBeNull();
    expect(imageReadingLine({ images: 0, image_reading: reading(0, 0, 0, 0) })).toBeNull();
  });
});

describe("upload receipt image counts", () => {
  const receipt = (image_reading: unknown, images = 5) => ({
    manuscript_id: 3, group_label: "G", ingest_status: "done", anchor_kind: "page", image_only: false,
    vision_status: "none", page_count: 10, text_chars: 100, images, tables: 0, equations: 0, citations: 0,
    notes: [], group_proposal: { title: null, short_name: null, program: null, adviser: null, members: [], extraction_failed: false },
    existing_upload: null, section_tree: { source: "none", nodes: [] }, image_reading,
  });

  it("keeps counts that add up", () => {
    expect(validateIngestReceipt(receipt(reading(2, 1, 3, 3))).image_reading).toEqual(reading(2, 1, 3, 3));
  });

  it("drops counts that do not add up but still confirms the upload", () => {
    for (const bad of [reading(2, 3, 3, 0), reading(2, 1, 3, 4), reading(1, 0, 1, 0), { checked: -1 }, "x", null]) {
      const out = validateIngestReceipt(receipt(bad));
      expect(out.manuscript_id).toBe(3);
      expect(out.image_reading).toBeUndefined();
    }
  });
});
