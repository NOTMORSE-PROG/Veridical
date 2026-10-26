import type { BoundSourceEvidence, EvidenceItem, FlagRegionOut, PublicEvidenceImageOut } from "../api/types";
import manifest from "./sourceEvidence.json";

const entries: BoundSourceEvidence[] = manifest.entries.map(entry => {
  if (entry.subject.kind !== "criterion" && entry.subject.kind !== "flag") throw new Error("Unknown source-evidence subject");
  if (entry.precision !== "page_bbox" && entry.precision !== "page_only" && entry.precision !== "paragraph_only" && entry.precision !== "unavailable") throw new Error("Unknown source-evidence precision");
  const boxes: BoundSourceEvidence["boxes"] = entry.boxes.map(box => {
    if (box.length !== 4 || !box.every(Number.isFinite)) throw new Error("Invalid source-evidence geometry");
    return [box[0], box[1], box[2], box[3]];
  });
  return { ...entry, subject: { kind: entry.subject.kind, id: entry.subject.id }, precision: entry.precision, boxes };
});
export const sourceEvidence = Object.freeze(entries);
export const evidenceSourceHash = manifest.source_sha256;
export const evidenceFor = (kind: "criterion" | "flag", id: number) => sourceEvidence.filter(entry => entry.subject.kind === kind && entry.subject.id === id);
export const evidenceById = (id: string) => sourceEvidence.find(entry => entry.evidence_id === id);
export const evidenceByImageId = (id: string) => sourceEvidence.find(entry => entry.image_id === id);

export function evidenceRegion(entry: BoundSourceEvidence, context: { run: number; manuscript: number; sourceHash: string | undefined; kind: "criterion" | "flag"; subjectId: number; role: string; index: number; quote: string; anchor: string }, flagId: number): FlagRegionOut | null {
  const saved = evidenceById(entry.evidence_id);
  if (!saved || entry.check_run_id !== saved.check_run_id || entry.manuscript_id !== saved.manuscript_id || entry.subject?.kind !== saved.subject.kind || entry.subject.id !== saved.subject.id || entry.role !== saved.role || entry.evidence_index !== saved.evidence_index || entry.quote !== saved.quote || entry.anchor !== saved.anchor) return null;
  if (!saved || context.run !== saved.check_run_id || context.manuscript !== saved.manuscript_id || context.sourceHash !== saved.source_sha256 || context.kind !== saved.subject.kind || context.subjectId !== saved.subject.id || context.role !== saved.role || context.index !== saved.evidence_index || context.quote !== saved.quote || context.anchor !== saved.anchor || entry.quote_sha256 !== saved.quote_sha256 || entry.source_sha256 !== saved.source_sha256) return null;
  if ((context.kind === "flag" && flagId !== context.subjectId) || (context.kind === "criterion" && flagId >= 0)) return null;
  return { flag_id: flagId, kind: saved.precision, page: saved.page, end_page: null, bbox: saved.boxes[0] ?? null, all_bboxes: saved.boxes, paragraph: saved.paragraph, index: null };
}

export function criterionSource(criterionId: number, evidence: EvidenceItem) {
  return evidenceFor("criterion", criterionId).find(entry => entry.quote === evidence.quote && entry.anchor === evidence.anchor);
}

export function flagSourcePagePair(candidates: readonly BoundSourceEvidence[], context: { run: number; manuscript: number; sourceHash: string | undefined; flagId: number }): BoundSourceEvidence[] {
  if (candidates.length !== 2 || new Set(candidates.map(entry => entry.evidence_id)).size !== 2) return [];
  const pair: BoundSourceEvidence[] = [];
  for (const entry of candidates) {
    const saved = evidenceById(entry.evidence_id);
    if (!saved || !saved.image_id || !saved.image_sha256 || saved.page === null || saved.page < 1 || !["page_bbox", "page_only"].includes(saved.precision)) return [];
    if (entry.page !== saved.page || entry.image_id !== saved.image_id || entry.image_sha256 !== saved.image_sha256) return [];
    if (!evidenceRegion(entry, { run: context.run, manuscript: context.manuscript, sourceHash: context.sourceHash, kind: "flag", subjectId: context.flagId, role: entry.role, index: entry.evidence_index, quote: entry.quote, anchor: entry.anchor }, context.flagId)) return [];
    pair.push(saved);
  }
  return pair;
}

export function publicEvidenceImages(): PublicEvidenceImageOut[] {
  return sourceEvidence.filter(entry => entry.public_sample === true && entry.source_sha256 === evidenceSourceHash && entry.check_run_id === manifest.check_run_id && entry.manuscript_id === manifest.manuscript_id).map(entry => ({ evidence_id: entry.evidence_id, image_id: entry.image_id, image_sha256: entry.image_sha256, image_width: entry.image_width, image_height: entry.image_height, quote: entry.quote, anchor: entry.anchor, page: entry.page, label: `${entry.subject.kind === "criterion" ? "Criterion" : "Finding"} ${entry.subject.id}: ${entry.label}`, precision: entry.precision, precision_note: entry.precision_note }));
}
