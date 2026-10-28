// F8/4h/4i/4w: recorded associations, separate from grade support and reader state.
import type { DocumentParagraphsOut } from "../api/types";

export interface EvidenceReference {
  reference_id: string;
  criterion_id: number | null;
  flag_id: number | null;
  role: "criterion_evidence" | "criterion_scope" | "agreement_intent" | "agreement_outcome" | "finding_excerpt" | "finding_scope" | "reuse_own" | "reuse_match";
  index: number;
  quote: string;
  anchor: string | null;
  source_scope: "own_manuscript" | "bounded_comparison_excerpt";
  source_state: "not_checked" | "assessment_revision_unverified" | "source_identity_unverified" | "bounded_excerpt_only";
}

export interface EvidenceManifest {
  snapshot_id: string;
  assessment_revision: number | null;
  llm_mode: "fake" | "real" | "unknown";
  references: EvidenceReference[];
}

export interface EvidencePlacement {
  reference_id: string;
  state: "text_layer_match" | "page_only" | "paragraph_only" | "unavailable";
  page: number | null;
  paragraph: number | null;
  boxes: [number, number, number, number][];
  reason: string | null;
}

export interface EvidenceLocation {
  snapshot_id: string;
  source_identity: string | null;
  source_format: "pdf" | "docx" | null;
  placement: EvidencePlacement;
  caption: string;
}

export interface EvidenceDocument {
  source_identity: string;
  document: DocumentParagraphsOut;
}

export interface EvidencePage {
  snapshot_id: string;
  placement: EvidencePlacement;
  image_data_url: string | null;
  image_sha256?: string | null;
  caption: string;
}

export interface EvidenceSourceRequest {
  snapshotId: string;
  reference: EvidenceReference;
  actionId: string;
}

export type OpenEvidenceSource = (request: EvidenceSourceRequest) => void;
