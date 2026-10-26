import type { FlagRegionOut } from "../api/types";
import { CRITERION_ANCHOR_REGION_ID } from "../config/ui";

/** Only an entire typed anchor is a location; narrative ranges are not. */
export function criterionAnchorRegion(anchor: string | null): FlagRegionOut | null {
  if (!anchor) return null;
  const page = anchor.trim().match(/^(?:page|p\.)\s*([1-9]\d*)$/i);
  const paragraph = anchor.trim().match(/^(?:¶|paragraph\s+)(\d+)$/i);
  if (!page && !paragraph) return null;
  const value = Number((page ?? paragraph)![1]);
  if (!Number.isSafeInteger(value)) return null;
  return { flag_id: CRITERION_ANCHOR_REGION_ID, kind: page ? "page_only" : "paragraph_only", page: page ? Number(page[1]) : null, end_page: null, bbox: null, all_bboxes: [], paragraph: paragraph ? Number(paragraph[1]) : null, index: null };
}
