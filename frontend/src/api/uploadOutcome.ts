// F1/F2, screens 4c/4e/4m: a cause is not proof that no record was saved.
import { ApiError } from "./client";
import type { IngestSummary, Rubric } from "./types";

export class UnconfirmedUploadError extends Error {
  constructor() { super("VERIDICAL could not confirm this upload's result."); }
}
export class UploadSessionUnavailableError extends Error {
  constructor() { super("Could not confirm your sign-in status. Check your session before continuing."); }
}
export class StaleUploadContextError extends Error {}

export function isKnownUploadRejection(error: unknown, kind: "manuscript" | "format") {
  return error instanceof UploadSessionUnavailableError || (error instanceof ApiError && (
    (error.status === 503 && error.code === "document_preparation_busy")
    || (error.status === 429 && error.code === "rate_limited")
    || (error.status === 401 && error.code === "unauthenticated")
    || (error.status === 409 && error.code === "actor_context_changed")
    // The server finished preparing the file and refused it: a definite
    // outcome, never an unconfirmed one.
    || (kind === "manuscript" && error.status === 422 && error.code === "upload_processing_failed")
    || (kind === "format" && error.status === 503 && error.code === "ai_not_configured")
    || (kind === "format" && ((error.status === 413 && error.code === "too_large")
      || (error.status === 422 && error.code === "file_malformed")))
  ));
}

export function isUploadFileError(error: unknown) {
  return error instanceof ApiError && ((error.status === 413 && error.code === "too_large")
    || (error.status === 422 && error.code === "file_malformed"));
}

type RecordValue = Record<string, unknown>;
const object = (v: unknown): v is RecordValue => typeof v === "object" && v !== null && !Array.isArray(v);
const natural = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const positive = (v: unknown): v is number => natural(v) && v > 0;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");
const nullableString = (v: unknown) => v === null || typeof v === "string";
const finite = (v: unknown) => typeof v === "number" && Number.isFinite(v);
const uuid = (v: unknown): v is string => typeof v === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);

// Accept API timestamps with a timezone and real calendar day, not Date's
// rollover of e.g. February 30. No fabricated fallback date is a receipt.
function timestamp(v: unknown): v is string {
  if (typeof v !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(v);
  if (!match || !Number.isFinite(Date.parse(v))) return false;
  const [, y, m, d, h, min, sec] = match.map(Number);
  const calendar = new Date(0);
  calendar.setUTCFullYear(y, m - 1, d);
  return calendar.getUTCFullYear() === y && calendar.getUTCMonth() === m - 1
    && calendar.getUTCDate() === d && h < 24 && min < 60 && sec < 60;
}
function anchored(v: unknown) {
  return object(v) && typeof v.value === "string" && typeof v.anchor === "string" && v.anchor.trim() !== "";
}

export function validateIngestReceipt(value: unknown): IngestSummary {
  const v = value;
  if (!object(v) || !positive(v.manuscript_id) || typeof v.group_label !== "string"
    || v.ingest_status !== "done" || !(v.anchor_kind === "page" || v.anchor_kind === "paragraph")
    || typeof v.image_only !== "boolean" || !(v.vision_status === "none" || v.vision_status === "done" || v.vision_status === "unavailable")
    || ![v.page_count, v.text_chars, v.images, v.tables, v.equations, v.citations].every(natural)
    || !strings(v.notes) || !object(v.group_proposal)) throw new UnconfirmedUploadError();
  const p = v.group_proposal;
  if (![p.title, p.short_name, p.program, p.adviser].every((x) => x === null || anchored(x))
    || !Array.isArray(p.members) || !p.members.every(anchored)
    || typeof p.extraction_failed !== "boolean") throw new UnconfirmedUploadError();
  const prior = v.existing_upload;
  if (prior !== null && (!object(prior) || !positive(prior.manuscript_id)
    || !timestamp(prior.created_at) || !nullableString(prior.original_filename)
    || !(prior.purged_at === null || timestamp(prior.purged_at)))) throw new UnconfirmedUploadError();
  if (v.image_reading === undefined || imageReading(v.image_reading, v.images as number)) return v as unknown as IngestSummary;
  return { ...(v as unknown as IngestSummary), image_reading: undefined };
}

// Counts that do not add up are dropped, not trusted; the upload itself is
// still confirmed, and the screen then says nothing about image reading.
const REASONS = ["no_cue", "too_small", "unsupported", "over_limit", "unavailable", "not_requested"] as const;

function imageReading(value: unknown, images: number): IngestSummary["image_reading"] {
  const keys = ["checked", "with_content", "unclear", "not_checked", ...REASONS] as const;
  if (!object(value) || !keys.every((key) => natural(value[key]))) return undefined;
  const v = value as Record<(typeof keys)[number], number>;
  if (v.with_content > v.checked || v.checked + v.unclear + v.not_checked !== images
    || REASONS.reduce((sum, key) => sum + v[key], 0) !== v.not_checked) return undefined;
  return Object.fromEntries(keys.map((key) => [key, v[key]])) as unknown as IngestSummary["image_reading"];
}

export function validateRubricReceipt(value: unknown, familyId?: string): Rubric {
  const v = value;
  if (!object(v) || !positive(v.id) || !uuid(v.rubric_family_id) || !positive(v.version)
    || (familyId !== undefined && v.rubric_family_id.toLowerCase() !== familyId.toLowerCase())
    || typeof v.title !== "string" || !(v.parse_status === "parsed" || v.parse_status === "needs_review")
    || !(v.parse_issues === null || strings(v.parse_issues)) || typeof v.is_active !== "boolean"
    || typeof v.is_latest_version !== "boolean"
    || !(v.can_edit_criteria === undefined || typeof v.can_edit_criteria === "boolean")
    || !Array.isArray(v.criteria)) throw new UnconfirmedUploadError();
  const ids = new Set<number>();
  for (const c of v.criteria) {
    if (!object(c) || !positive(c.id) || ids.has(c.id)
      || !(c.type === "structural" || c.type === "semantic" || c.type === "not_assessable")
      || typeof c.text !== "string" || c.text.trim() === "" || !nullableString(c.evidence)
      || !finite(c.weight) || (c.weight as number) <= 0
      || !natural(c.position) || !(c.levels === undefined || c.levels === null || Array.isArray(c.levels))) {
      throw new UnconfirmedUploadError();
    }
    ids.add(c.id);
    if (Array.isArray(c.levels)) {
      if (c.levels.length < 2 || !c.levels.every((l, index, all) => object(l) && positive(l.level)
        && (index === 0 || l.level > all[index - 1].level)
        && typeof l.name === "string" && l.name.trim() !== ""
        && typeof l.descriptor === "string" && l.descriptor.trim() !== ""
        && finite(l.points) && (l.points as number) >= 0)
        || !c.levels.some((l) => l.points > 0)) throw new UnconfirmedUploadError();
    }
  }
  return v as unknown as Rubric;
}
