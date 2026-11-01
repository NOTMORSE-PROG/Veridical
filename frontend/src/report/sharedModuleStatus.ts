// BUG-658: what each check's result means to an adviser reading a shared
// report, said once, so an empty list never reads as "the paper is clean"
// unless the check completed. Every saved finding is in the shared report;
// only the check's own coverage decides what an empty list means.
import { coverageCopy } from "./reportCoverage";

type Kind = "internal_agreement" | "citation_integrity" | "statistical_forensics" | "originality_reuse";
type State = "completed" | "partial" | "incomplete" | "not_applicable" | "not_recorded";

// What a completed check looked at, so "none recorded" says how far it goes.
const SCOPE: Record<Kind, string> = {
  internal_agreement: "the objectives and results it could identify",
  citation_integrity: "in-text citations, the reference list, and the cited sources it could look up",
  statistical_forensics: "the statistics it could read in the text and tables. It does not check the raw data",
  originality_reuse: "the manuscripts it was compared with",
};
const NOUN: Record<Kind, string> = {
  internal_agreement: "objectives or results",
  citation_integrity: "citations or references",
  statistical_forensics: "statistics",
  originality_reuse: "eligible text",
};

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export interface SharedModuleStatusInput {
  kind: Kind;
  state: State;
  findings: number;
  leads: number;
  coverageReason?: string | null;
  /** The check says what it left out elsewhere on the page (agreement scope,
   * originality scope), so no generic "not covered" line is needed. */
  coverageShownElsewhere?: boolean;
  detailSnapshot?: "current" | "stale" | "not_recorded";
}

export function sharedModuleStatus({ kind, state, findings, leads, coverageReason, coverageShownElsewhere, detailSnapshot }: SharedModuleStatusInput): { lead: string; detail: string[] } {
  const found = plural(findings, "possible inconsistency", "possible inconsistencies");
  const notCovered = coverageReason && coverageCopy(coverageReason)
    ? [`Not covered: ${coverageCopy(coverageReason)}`]
    : coverageShownElsewhere ? [] : ["This link does not record what this check did not cover."];
  const leadLine = leads > 0 ? [`${plural(leads, "comparison lead", "comparison leads")}: a lead is a passage to compare yourself. It is not a finding and is not scored.`] : [];
  if (state === "completed") {
    return findings > 0
      ? { lead: `Completed. ${found} recorded.`, detail: ["All saved findings for this check are shown here.", ...leadLine] }
      : { lead: "Completed. No possible inconsistencies were recorded.", detail: [`This covers only what this check looks at: ${SCOPE[kind]}.`, ...leadLine] };
  }
  if (state === "partial" || state === "incomplete") {
    return findings > 0
      ? { lead: `Ran only in part. ${found} recorded.`, detail: ["All of them are shown here. Parts the check did not cover may have others.", ...notCovered, ...leadLine] }
      : { lead: "Ran only in part. No possible inconsistencies were recorded in the part it checked, so this does not mean there are none.", detail: [...notCovered, ...leadLine] };
  }
  if (state === "not_applicable") {
    return { lead: "Not applicable.", detail: [`The check found no ${NOUN[kind]} to check in the text it could read, so it did not run. This does not mean the paper has none.`] };
  }
  const why = detailSnapshot === "stale"
    ? ["The report changed after this link was made. Ask the instructor for a new link to see each check's status."]
    : detailSnapshot === "not_recorded" ? ["This link was saved without each check's status."] : [];
  return {
    lead: "Status not recorded.",
    detail: [findings > 0 ? `This link does not say whether this check ran. The ${found} shown here are all it saved.` : "This link does not say whether this check ran. No findings are shown, but that does not mean there are none.", ...why, ...leadLine],
  };
}

/** "3 findings", "10 comparison leads", "2 findings, 1 comparison lead". */
export function sharedCountLabel(findings: number, leads: number): string {
  if (findings && leads) return `${plural(findings, "finding", "findings")}, ${plural(leads, "comparison lead", "comparison leads")}`;
  if (leads) return plural(leads, "comparison lead", "comparison leads");
  return plural(findings, "finding", "findings");
}
