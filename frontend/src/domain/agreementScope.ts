import type { AgreementScopeOut } from "../api/types";
import { READINESS_EXCLUSION_COPY } from "./readinessExclusion";

// What the internal-agreement check covered, worded for the readiness report,
// manuscript review and the adviser view (BUG-617). The number of pairs
// compared is coverage; there is never a count of agreeing pairs, because the
// pairing is not yet good enough for such a count to be trusted. Same strings
// as backend/app/report/data/agreement_scope.json, which the PDF uses.

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

// With this many parts or more, the first few are named and the rest summed.
const ROLE_FOLDING = { foldAt: 4, shown: 2 } as const;

export const AGREEMENT_SCOPE_COPY = {
  heading: "What this check compared",
  setAsideListHeading: "Results set aside",
  setAsideListIntro: "These statements read like results but are in parts of the paper that usually do not report this study's own results. Open one to check it yourself.",
  limit: `Partly assessed: ${READINESS_EXCLUSION_COPY.statement_roles_unverified.replace(/\.$/, "")}, so this check does not change readiness.`,
  absence: "An objective with no matching result is not reported here.",
  interrupted: "The check was interrupted before it compared every pair.",
  interruptedTail: "Pairs it did not compare are not treated as passed.",
  notIdentified: "The results section could not be found, so possible results were taken from every part of the paper. Some of them may describe other studies.",
  olderNoScope: "This report was made before VERIDICAL recorded what this check compared.",
  adviserSetAside: "The instructor's report lists the results set aside.",
  matchingHeading: "About these pairs",
  matchingCaveat: "VERIDICAL matches each objective with the results most like it. Some matched pairs may not belong together, and this matching is being improved. A matched pair is not proof that both statements are about the same thing.",
} as const;

// Only these are possible inconsistencies. An injection warning sits on a
// pair that read as consistent and is a finding of another kind.
const INCONSISTENCY_KINDS = new Set(["agreement_contradictory", "agreement_partial"]);

export function countInconsistencies(flags: readonly { check_kind: string; problem_kind?: string | null }[]): number {
  return flags.filter((flag) => flag.check_kind === "internal_agreement" && INCONSISTENCY_KINDS.has(flag.problem_kind ?? "")).length;
}

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

/** Said only when fewer objectives were matched than found, so "found 17"
 * never reads as 17 checked. */
export function matchedLine(scope: AgreementScopeOut): string | null {
  const found = scope.n_objectives;
  const matched = scope.n_objectives_matched;
  if (!found || matched == null || matched >= found) return null;
  if (matched === 0) return `None of the ${found} objectives were matched to a result.`;
  // Pairs are counted apart: one objective can be paired with several results.
  return `${matched} of the ${found} objectives ${plural(matched, "was", "were")} matched to at least one result. One objective can be paired with more than one result.`;
}

/** One sentence per part of the paper results were set aside from, most first. */
export function setAsideLines(scope: AgreementScopeOut): string[] {
  const roles = scope.set_aside_by_role ?? [];
  const fold = roles.length >= ROLE_FOLDING.foldAt;
  const lines = roles.slice(0, fold ? ROLE_FOLDING.shown : roles.length).map(({ role, count }) => {
    const known = ROLE_PHRASES[role];
    const subject = `${count} ${plural(count, "result", "results")}`;
    const verb = plural(count, "was", "were");
    return known
      ? `${subject} from the ${known[0]} ${verb} set aside, because that part of a paper usually ${known[1]}.`
      : `${subject} from another part of the paper ${verb} set aside, because that part does not usually report the study's own results.`;
  });
  if (fold) {
    const rest = roles.slice(ROLE_FOLDING.shown).reduce((sum, { count }) => sum + count, 0);
    lines.push(`${rest} more ${plural(rest, "was", "were")} set aside from other parts of the paper.`);
  }
  const short = scope.n_results_too_short ?? 0;
  if (short > 0) lines.push(`${short} ${plural(short, "result was", "results were")} set aside as too short to compare.`);
  return lines;
}

/** Why no pair was compared, said only as far as the record shows it. */
function nothingCompared(scope: AgreementScopeOut): string {
  if (scope.n_pairs_skipped) return "The check was interrupted before it compared any pair, so it makes no finding.";
  if (scope.n_objectives === 0) return "No objectives were found, so nothing was compared and the check makes no finding.";
  if (scope.n_results === 0) return "No results were left to compare, so the check makes no finding.";
  if (scope.n_results_set_aside == null || scope.n_results_too_short == null) return "No pair was compared, so the check makes no finding.";
  if (scope.n_results != null && scope.n_results <= scope.n_results_set_aside + scope.n_results_too_short) {
    return "No results were left to compare, so the check makes no finding.";
  }
  return "No result was close enough to an objective to compare, so the check makes no finding.";
}

export interface OutcomeInput {
  /** Possible inconsistencies raised as findings. */
  findings: number;
  /** Pairs compared but not decided. */
  undecided: number;
  /** Pairs compared; null when the run did not record it. */
  compared: number | null;
  /** The run stopped, or skipped pairs, before it compared everything. */
  interrupted?: boolean;
  /** Where the lists are: below on the report, on the readiness report from
   * manuscript review, and with the instructor from the adviser view. */
  surface: "screen" | "document" | "adviser";
}

/** The run stopped, or skipped pairs it could not judge, before it compared
 * everything. Same rule as the PDF. */
export function isInterrupted(scope: AgreementScopeOut, state: string | undefined): boolean {
  return state === "incomplete" || Boolean(scope.n_pairs_skipped);
}

function findingsSentence(findings: number, surface: OutcomeInput["surface"]): string {
  const where = surface === "document" ? "on the readiness report" : "below";
  return findings === 1 ? `It is listed ${where}.` : `They are listed ${where}.`;
}

function undecidedSentence(undecided: number, afterFindings: boolean, surface: OutcomeInput["surface"]): string {
  const one = undecided === 1;
  if (surface === "adviser") return one ? "It is not a finding; the instructor's report lists it." : "They are not findings; the instructor's report lists them.";
  if (surface === "document") return one ? "Read it on the readiness report." : "Read them on the readiness report.";
  const where = afterFindings ? "after the findings" : "below for you to read";
  return one ? `It is listed ${where}.` : `They are listed ${where}.`;
}

/** The comparison's outcome. The compared count is coverage, never a count
 * of agreeing pairs. */
export function outcomeLines(scope: AgreementScopeOut, { findings, undecided, compared, interrupted = false, surface }: OutcomeInput): string[] {
  const inconsistencies = (n: number) => `${n} possible ${plural(n, "inconsistency", "inconsistencies")}`;
  const pairCount = (n: number) => `${n} ${plural(n, "pair", "pairs")}`;
  if (compared == null) {
    // With no objectives or no results, nothing could have been compared.
    if (scope.n_objectives === 0 || scope.n_results === 0) return [nothingCompared(scope), "This does not mean the objectives and results agree."];
    const lines = ["This report did not record how many pairs were compared."];
    if (findings > 0) lines.push(`It found ${inconsistencies(findings)}. ${findingsSentence(findings, surface)}`);
    if (undecided > 0) lines.push(`${pairCount(undecided)} could not be decided. ${undecidedSentence(undecided, findings > 0, surface)}`);
    return lines;
  }
  if (compared === 0) return [nothingCompared(scope), "This does not mean the objectives and results agree."];
  const base = interrupted
    ? `The check was interrupted. Among the ${pairCount(compared)} it compared before then`
    : `Among the ${pairCount(compared)} VERIDICAL matched`;
  let lines: string[];
  if (findings > 0) {
    lines = [`${base}, the comparison found ${inconsistencies(findings)}. ${findingsSentence(findings, surface)}`];
    if (undecided > 0) lines.push(`${undecided} other ${plural(undecided, "pair", "pairs")} could not be decided. ${undecidedSentence(undecided, true, surface)}`);
  } else if (undecided === 0) {
    lines = [`${base}, the comparison found no possible inconsistency.`];
  } else if (undecided >= compared) {
    // Interrupted, the outcome still names the interruption here.
    const lead = interrupted ? "The check was interrupted. " : "";
    const which = interrupted ? "it compared before then" : "VERIDICAL matched";
    lines = [compared === 1
      ? `${lead}The comparison could not decide on the one pair ${which}, so it makes no finding. ${undecidedSentence(1, false, surface)}`
      : `${lead}The comparison could not decide on any of the ${pairCount(compared)} ${which}, so it makes no finding. ${undecidedSentence(compared, false, surface)}`];
  } else {
    lines = [
      `${base}, the comparison found no possible inconsistency in the pairs it could decide.`,
      `${pairCount(undecided)} could not be decided. ${undecidedSentence(undecided, false, surface)}`,
    ];
  }
  if (interrupted) lines.push(AGREEMENT_SCOPE_COPY.interruptedTail);
  return lines;
}

/** The interruption still needs saying when the outcome does not name it:
 * unknown or no pairs compared, without every pair having been skipped. */
export function interruptedLine(scope: AgreementScopeOut, { compared, interrupted, outcomeShown }: { compared: number | null; interrupted: boolean; outcomeShown: boolean }): string | null {
  if (!interrupted) return null;
  const named = compared ? true : compared === 0 && Boolean(scope.n_pairs_skipped);
  return outcomeShown && named ? null : `${AGREEMENT_SCOPE_COPY.interrupted} ${AGREEMENT_SCOPE_COPY.interruptedTail}`;
}
