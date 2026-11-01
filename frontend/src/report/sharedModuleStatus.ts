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
  /** What the check left out is listed under the points, reason included. */
  coverageListed?: boolean;
  detailSnapshot?: "current" | "stale" | "not_recorded";
}

/** How strongly the state should read: done, partial or unknown. */
export function sharedModuleTone(state: State): "done" | "partial" | "unknown" {
  return state === "partial" || state === "incomplete" ? "partial" : state === "not_recorded" ? "unknown" : "done";
}

export function sharedModuleStatus({ kind, state, findings, leads, coverageReason, coverageShownElsewhere, coverageListed, detailSnapshot }: SharedModuleStatusInput): { title: string; points: string[] } {
  const found = plural(findings, "possible inconsistency", "possible inconsistencies");
  const notCovered = coverageListed ? [] : coverageReason && coverageCopy(coverageReason)
    ? [`Not covered: ${coverageCopy(coverageReason)}`]
    : coverageShownElsewhere ? [] : ["This link does not record what this check did not cover."];
  const leadLine = leads > 0 ? [`${plural(leads, "comparison lead", "comparison leads")}: a lead is a passage to compare yourself. It is not a finding and is not scored.`] : [];
  if (state === "completed") {
    return findings > 0
      ? { title: "Completed", points: [`${found} recorded.`, ...leadLine] }
      : { title: "Completed", points: ["No possible inconsistencies were recorded.", `This covers only what this check looks at: ${SCOPE[kind]}.`, ...leadLine] };
  }
  if (state === "partial") {
    return findings > 0
      ? { title: "Ran only in part", points: [`${found} recorded.`, "Parts the check did not cover may have others.", ...notCovered, ...leadLine] }
      : { title: "Ran only in part", points: ["No possible inconsistencies were recorded in the part it checked.", "That does not mean there are none.", ...notCovered, ...leadLine] };
  }
  if (state === "incomplete") {
    // It may have checked little or nothing, so no claim about a checked part.
    return findings > 0
      ? { title: "Not fully run", points: [`${found} recorded.`, "What it did not check may have others.", ...notCovered, ...leadLine] }
      : { title: "Not fully run", points: ["No possible inconsistencies were recorded.", "The check did not run on everything, so that does not mean there are none.", ...notCovered, ...leadLine] };
  }
  if (state === "not_applicable") {
    return { title: "Not applicable", points: [`The check found no ${NOUN[kind]} to check in the text it could read, so it did not run.`, "This does not mean the paper has none."] };
  }
  const why = detailSnapshot === "stale"
    ? ["The report changed after this link was made. Ask the instructor for a new link to see each check's status."]
    : detailSnapshot === "not_recorded" ? ["This link was saved without each check's status."] : [];
  return {
    title: "Status not recorded",
    points: ["This link does not say whether this check ran.", findings > 0 ? `The ${found} shown here are all it saved.` : "No findings are shown, but that does not mean there are none.", ...why, ...leadLine],
  };
}

/** What a shared link holds, said once for the whole Findings tab. */
export function sharedFindingsIntro(detailSnapshot?: "current" | "stale" | "not_recorded"): string {
  const explanations = detailSnapshot === "stale" ? "Explanations are not shown, because the report changed after the link was made."
    : detailSnapshot === "not_recorded" ? "No explanations were saved with this link."
    : "Explanations and quotations are shown as they were when the link was made.";
  return `These are possible inconsistencies, not accusations. This link shows every finding the checks saved, as the report stands now. ${explanations} It does not include the instructor's reasons for changing a grade or the private record of changes.`;
}

/** "3 findings", "10 comparison leads", "2 findings, 1 comparison lead". */
export function sharedCountLabel(findings: number, leads: number): string {
  if (findings && leads) return `${plural(findings, "finding", "findings")}, ${plural(leads, "comparison lead", "comparison leads")}`;
  if (leads) return plural(leads, "comparison lead", "comparison leads");
  return plural(findings, "finding", "findings");
}
