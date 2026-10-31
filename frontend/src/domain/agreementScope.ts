import type { AgreementScopeOut } from "../api/types";
import { READINESS_EXCLUSION_COPY } from "./readinessExclusion";

// What the internal-agreement check covered, worded for the readiness report,
// manuscript review, the adviser view and the PDF export (BUG-617). Coverage
// only: no count of agreeing pairs, because the pairing is not yet good enough
// for such a count to be trusted. Same strings as
// backend/app/report/data/agreement_scope.json.

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

// Where a set-aside result sat, and why that part of a paper is left out.
const ROLE_PHRASES: Record<string, [string, string]> = {
  literature: ["literature review", "describes other studies"],
  introduction: ["introduction", "gives background"],
  methodology: ["methodology", "describes how the study was done"],
  front_matter: ["front pages", "does not report the study's own results"],
  table_of_contents: ["table of contents", "does not report the study's own results"],
  biography: ["researchers' profile", "does not report the study's own results"],
  references: ["reference list", "does not report the study's own results"],
  appendix: ["appendices", "does not report the study's own results"],
  future_work: ["recommendations", "does not report the study's own results"],
};

export const AGREEMENT_SCOPE_COPY = {
  heading: "What this check compared",
  purpose: "This check compares the objectives stated in the paper with the results it reports.",
  setAsideListHeading: "Results set aside",
  setAsideListIntro: "These statements read like results but are in parts of the paper that usually do not report this study's own results. Open one to check it yourself.",
  limit: `${READINESS_EXCLUSION_COPY.statement_roles_unverified.replace(/\.$/, "")}, so these comparisons do not change readiness.`,
  absence: "An objective with no matching result is not reported here.",
  interrupted: "The check was interrupted before it compared every pair. Pairs it did not compare are not treated as passed.",
  notIdentified: "The results section could not be found, so possible results were taken from every part of the paper. Some of them may describe other studies.",
  olderNoSections: "This report was made before VERIDICAL recorded which results it set aside.",
  olderNoScope: "This report was made before VERIDICAL recorded what this check compared.",
  adviserSetAside: "The instructor's report lists the results set aside.",
} as const;

export function rolePhrase(role: string): string {
  return ROLE_PHRASES[role]?.[0] ?? "another part of the paper";
}

export function foundLine(scope: AgreementScopeOut): string | null {
  const objectives = scope.n_objectives;
  const results = scope.n_results;
  if (objectives == null || results == null) return null;
  const o = objectives === 0 ? "no possible objectives" : `${objectives} possible ${plural(objectives, "objective", "objectives")}`;
  const r = results === 0 ? "no possible results" : `${results} possible ${plural(results, "result", "results")}`;
  return `Found ${o} and ${r}.`;
}

/** One sentence per part of the paper results were set aside from, most first. */
export function setAsideLines(scope: AgreementScopeOut): string[] {
  const roles = scope.set_aside_by_role ?? [];
  const lines = roles.slice(0, roles.length >= 4 ? 2 : roles.length).map(({ role, count }) => {
    const known = ROLE_PHRASES[role];
    const subject = `${count} ${plural(count, "result", "results")}`;
    const verb = plural(count, "was", "were");
    return known
      ? `${subject} from the ${known[0]} ${verb} set aside, because that part of a paper usually ${known[1]}.`
      : `${subject} from another part of the paper ${verb} set aside, because that part does not usually report the study's own results.`;
  });
  if (roles.length >= 4) {
    const rest = roles.slice(2).reduce((sum, { count }) => sum + count, 0);
    lines.push(`${rest} more ${plural(rest, "was", "were")} set aside from other parts of the paper.`);
  }
  const short = scope.n_results_too_short ?? 0;
  if (short > 0) lines.push(`${short} ${plural(short, "result was", "results were")} set aside as too short to compare.`);
  return lines;
}

export interface OutcomeInput {
  /** Possible inconsistencies raised as findings. */
  findings: number;
  /** Pairs compared but not decided. */
  undecided: number;
  /** Pairs compared; null when the run did not record it. */
  compared: number | null;
  /** Where the lists are: below on screen, named sections in the PDF, and on
   * the adviser view the undecided pairs keep their own sentence. */
  surface: "screen" | "pdf" | "adviser";
}

/** The comparison's outcome, never a count of agreeing pairs. */
export function outcomeLines(scope: AgreementScopeOut, { findings, undecided, compared, surface }: OutcomeInput): string[] {
  if (compared == null) return [];
  if (findings > 0) {
    const lines = [findings === 1
      ? `The comparison found 1 possible inconsistency. It is listed ${surface === "pdf" ? "under Findings" : "below"}.`
      : `The comparison found ${findings} possible inconsistencies. They are listed ${surface === "pdf" ? "under Findings" : "below"}.`];
    if (undecided > 0 && surface !== "adviser") {
      const where = surface === "pdf" ? "under Pairs to read yourself" : "after the findings";
      lines.push(undecided === 1
        ? `1 other pair could not be decided. It is listed ${where}.`
        : `${undecided} other pairs could not be decided. They are listed ${where}.`);
    }
    return lines;
  }
  if (compared === 0) {
    const reason = scope.n_objectives === 0
      ? "No objectives were found, so nothing was compared and the check makes no finding."
      : scope.n_results === 0 || (scope.n_results != null && scope.n_results <= (scope.n_results_set_aside ?? 0) + (scope.n_results_too_short ?? 0))
        ? "No results were left to compare, so the check makes no finding."
        : "No result was close enough to an objective to compare, so the check makes no finding.";
    return [reason, "This does not mean the objectives and results agree."];
  }
  const listed = surface === "pdf" ? "under Pairs to read yourself" : "below for you to read";
  if (undecided === 0) return ["The comparison found no possible inconsistency among the pairs it compared."];
  if (undecided >= compared) {
    const lead = compared === 1
      ? "The comparison could not decide on the one pair it compared, so it makes no finding."
      : "The comparison could not decide on any pair it compared, so it makes no finding.";
    if (surface === "adviser") return [lead];
    return [`${lead} ${compared === 1 ? "The pair is" : "The pairs are"} listed ${listed}.`];
  }
  const decided = "The comparison found no possible inconsistency among the pairs it could decide.";
  if (surface === "adviser") return [decided];
  return [
    decided,
    undecided === 1 ? `1 pair could not be decided. It is listed ${listed}.` : `${undecided} pairs could not be decided. They are listed ${listed}.`,
  ];
}
