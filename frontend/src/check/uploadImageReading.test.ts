import { describe, expect, it } from "vitest";
import type { ImageReadingSummary } from "../api/types";
import { validateIngestReceipt } from "../api/uploadOutcome";
import { IMAGE_READING_LIMIT, imageReadingLine } from "./uploadImageReading";

const none: ImageReadingSummary = {
  checked: 0, with_content: 0, unclear: 0, not_checked: 0, no_cue: 0, too_small: 0,
  unsupported: 0, over_limit: 0, unavailable: 0, not_requested: 0,
};
const reading = (patch: Partial<ImageReadingSummary>) => ({ ...none, ...patch });
const pdf = (images: number, image_reading?: ImageReadingSummary) => ({ images, image_reading, anchor_kind: "page" as const });

describe("upload image reading line", () => {
  it("leads with what happened to the images and says why none was read", () => {
    expect(imageReadingLine(pdf(44, reading({ not_checked: 44, no_cue: 44 })))).toBe(
      `44 images found. None was read for a table or equation: 44 had no recognised table or equation caption beside them. ${IMAGE_READING_LIMIT}`,
    );
  });

  it("gives every unread image a reason", () => {
    expect(imageReadingLine(pdf(92, reading({ not_checked: 92, no_cue: 89, too_small: 3 })))).toBe(
      `92 images found. None was read for a table or equation: 89 had no recognised table or equation caption beside them; 3 were too small to hold one. ${IMAGE_READING_LIMIT}`,
    );
  });

  it("never reports a pass that found nothing, or failed, as done", () => {
    expect(imageReadingLine(pdf(81, reading({ checked: 12, not_checked: 69, over_limit: 60, no_cue: 9 })))).toBe(
      `81 images found. 12 were read for a table or equation; none held one. 69 were not read: 9 had no recognised table or equation caption beside them; 60 were over the reading limit for one manuscript. ${IMAGE_READING_LIMIT}`,
    );
    expect(imageReadingLine(pdf(3, reading({ unclear: 3 })))).toBe(`3 images found. 3 could not be read reliably. ${IMAGE_READING_LIMIT}`);
    expect(imageReadingLine(pdf(3, reading({ checked: 1, not_checked: 2, unavailable: 2 }))))
      .toContain("2 were not read: 2 could not be read because the image reader was unavailable.");
  });

  it("adds no limit when every image was read", () => {
    expect(imageReadingLine(pdf(1, reading({ checked: 1, with_content: 1 })))).toBe("1 image found. 1 was read for a table or equation; 1 held one.");
  });

  it("says Word images are not read, without offering a caption as the reason", () => {
    const line = imageReadingLine({ images: 1, anchor_kind: "paragraph", image_reading: reading({ not_checked: 1, unsupported: 1 }) });
    expect(line).toBe(`1 image found. Images in Word files are not read for tables or equations. ${IMAGE_READING_LIMIT}`);
    expect(line).not.toContain("caption");
  });

  it("says nothing without counts or images", () => {
    expect(imageReadingLine(pdf(4))).toBeNull();
    expect(imageReadingLine(pdf(0, none))).toBeNull();
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
    const good = reading({ checked: 2, with_content: 1, unclear: 1, not_checked: 2, no_cue: 1, too_small: 1 });
    expect(validateIngestReceipt(receipt(good)).image_reading).toEqual(good);
  });

  it("drops counts that do not add up but still confirms the upload", () => {
    for (const bad of [
      reading({ checked: 2, with_content: 3, not_checked: 3, no_cue: 3 }),
      reading({ checked: 2, not_checked: 3, no_cue: 2 }),
      reading({ checked: 1, not_checked: 1, no_cue: 1 }),
      { checked: -1 }, "x", null,
    ]) {
      const out = validateIngestReceipt(receipt(bad));
      expect(out.manuscript_id).toBe(3);
      expect(out.image_reading).toBeUndefined();
    }
  });
});
