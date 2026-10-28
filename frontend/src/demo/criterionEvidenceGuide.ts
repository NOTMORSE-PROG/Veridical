import type { EvidenceItem } from "../api/types";
import { evidenceFor } from "./sourceEvidence";
import { sampleCriteria, WORKSPACE_SAMPLE } from "./workspaceFixture";

export interface CriterionEvidenceGroup {
  title: string;
  explanation: string;
  indexes: number[];
  previewIndexes?: number[];
  note?: string;
}

const GROUPS: Record<number, CriterionEvidenceGroup[]> = {
  1: [
    { title: "The prototype that was built", explanation: "The abstract describes a C console application. The implementation saves items in a text file. Together, these passages identify the delivered prototype.", indexes: [1, 3] },
    { title: "The database descriptions disagree", explanation: "The conclusion describes database integration as delivered, but the limitations say there is no database integration. Future work proposes moving to a relational database. Read these statements together when judging how clearly the completed scope is explained.", indexes: [0, 2, 4], previewIndexes: [0, 2] },
    { title: "Plans need to be separated from completed work", explanation: "The methodology proposes a web interface and relational database. These plans need to be distinguished from the console and text-file implementation described in 'The prototype that was built'.", indexes: [5, 6] },
  ],
  2: [
    { title: "Planned tests and reported ratings", explanation: "The evaluation describes intended user testing. The later summary rates the system and lists its components. Those ratings need measured test results and a consistent component count; a testing plan alone does not establish performance.", indexes: [0, 1, 2], previewIndexes: [0, 1] },
    { title: "Research claims need identifiable sources", explanation: "The literature passage refers to research and case studies without identifying a study. The published reference section contains an acknowledgement rather than research references. Check both before judging the support for the paper's claims.", indexes: [3, 4] },
  ],
  3: [
    { title: "Specific proofreading examples", explanation: "The declaration has a subject-verb error and 'top any other'. The acknowledgement mixes 'I' and 'our', and the budget misspells 'developers'. These examples support proofreading feedback; they do not by themselves describe every sentence in the manuscript.", indexes: [0, 1, 2], previewIndexes: [0] },
  ],
  4: [
    { title: "The document has an organized contents page", explanation: "Inspect the original contents page to see how the paper locates its requirements, design, evaluation, limitations, code and manual. This is a page-level layout observation.", indexes: [0] },
    { title: "Visible items still need a formatting pass", explanation: "Inspect the unfinished objectives list and the reference section. Page 4 includes a logo; its heading is included here as page-layout context, rather than evidence of a missing graphic.", indexes: [1, 2, 3], previewIndexes: [1, 2], note: "On page 6, the box locates the preceding objective. Check the next iv. line on the original page: an empty list item is a visual observation, not an exact quotation of missing text." },
  ],
};

/** The published case's source notes, never an inferred mapping for other papers. */
export function criterionEvidenceGuide(runId: number, criterionId: number, title: string, evidence: readonly EvidenceItem[]): CriterionEvidenceGroup[] | null {
  if (runId !== 1 || sampleCriteria.find(row => row.id === criterionId)?.text !== title) return null;
  const bound = evidenceFor("criterion", criterionId);
  if (!bound.length || evidence.length !== bound.length || bound.some((entry, index) => entry.check_run_id !== runId || entry.manuscript_id !== 1 || entry.source_sha256 !== WORKSPACE_SAMPLE.sha256 || entry.evidence_index !== index || entry.quote !== evidence[index]?.quote || entry.anchor !== evidence[index]?.anchor)) return null;
  return GROUPS[criterionId] ?? null;
}
