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
  reviewLead: "internal agreement review lead",
  reviewLeads: "internal agreement review leads",
  noReviewLeads: "No internal agreement review lead was recorded.",
  comparisonUnknown: "The number of pairs compared with AI was not recorded.",
  comparedCount: "Pairs compared with AI: {n}.",
  unresolvedCount: "Selected pairs left unresolved: {n}.",
  noAiComparison: "No pair was compared with AI.",
  noAgreementInference: "This does not mean the objectives and results agree.",
  setAsideListHeading: "Results set aside",
  setAsideListIntro: "These statements read like results but are in parts of the paper that usually do not report this study's own results. Open one to check it yourself.",
  limit: `Partly assessed: ${READINESS_EXCLUSION_COPY.statement_roles_unverified.replace(/\.$/, "")}, so this check does not change readiness.`,
  absence: "An objective with no matching result is not reported here.",
  interrupted: "The check was interrupted before it compared every pair.",
  interruptedTail: "Pairs it did not compare are not treated as passed.",
  notIdentified: "The results section could not be found, so possible results were taken from every part of the paper. Some of them may describe other studies.",
  olderNoScope: "This report was made before VERIDICAL recorded what this check compared.",
  adviserSetAside: "This link counts these statements but does not quote them.",
  matchingHeading: "About these pairs",
  matchingCaveat: "VERIDICAL matches each objective with the results most like it. Some matched pairs may not belong together, and this matching is being improved. A matched pair is not proof that both statements are about the same thing.",
} as const;

// Factual and attainment leads share this aggregate. Unresolved pairs and
// injection warnings retain their separate meanings.
const REVIEW_LEAD_KINDS = new Set(["agreement_contradictory", "agreement_factual_conflict", "agreement_unmet", "agreement_partial"]);

export function countInconsistencies(flags: readonly { check_kind: string; problem_kind?: string | null }[]): number {
  return flags.filter((flag) => flag.check_kind === "internal_agreement" && REVIEW_LEAD_KINDS.has(flag.problem_kind ?? "")).length;
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

export interface OutcomeInput {
  /** Recorded factual or attainment review leads. */
  findings: number;
  /** Selected unresolved pairs, including local abstentions. */
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
  if (surface === "adviser") return one ? "It is not a finding and does not change readiness." : "They are not findings and do not change readiness.";
  if (surface === "document") return one ? "Read it on the readiness report." : "Read them on the readiness report.";
  const where = afterFindings ? "after the findings" : "below for you to read";
  return one ? `It is listed ${where}.` : `They are listed ${where}.`;
}

/** The comparison's outcome. The compared count is coverage, never a count
 * of agreeing pairs. */
export function outcomeLines(_scope: AgreementScopeOut, { findings, undecided, compared, interrupted = false, surface }: OutcomeInput): string[] {
  const copy = AGREEMENT_SCOPE_COPY;
  const lines = [compared == null
    ? copy.comparisonUnknown
    : copy.comparedCount.replace("{n}", String(compared))];
  if (compared === 0) lines.push(copy.noAiComparison, copy.noAgreementInference);
  if (findings > 0) {
    lines.push(`${findings} ${plural(findings, copy.reviewLead, copy.reviewLeads)}. ${findingsSentence(findings, surface)}`);
  } else if (compared != null && compared > 0) {
    lines.push(copy.noReviewLeads);
  }
  if (undecided > 0) lines.push(`${copy.unresolvedCount.replace("{n}", String(undecided))} ${undecidedSentence(undecided, findings > 0, surface)}`);
  if (interrupted) lines.push(copy.interrupted, copy.interruptedTail);
  return lines;
}

/** The interruption still needs saying when the outcome does not name it:
 * unknown or no pairs compared, without every pair having been skipped. */
export function interruptedLine(_scope: AgreementScopeOut, { interrupted, outcomeShown }: { compared: number | null; interrupted: boolean; outcomeShown: boolean }): string | null {
  if (!interrupted) return null;
  return outcomeShown ? null : `${AGREEMENT_SCOPE_COPY.interrupted} ${AGREEMENT_SCOPE_COPY.interruptedTail}`;
}
