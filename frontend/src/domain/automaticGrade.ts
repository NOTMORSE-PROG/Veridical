// F3/F8 screen 4h: how an automatic grade was chosen, shown beside the grade
// until an instructor changes it (owner decision 2026-10-29).
import type { CriterionResultOut } from "../api/types";

export const AUTOMATIC_GRADE_COPY = {
  agreed: "Graded automatically: {agreeing} of {passes} grading passes chose this grade.",
  middle_of_three: "Graded automatically: the three grading passes disagreed, so the middle level was used.",
  lower_of_two: "Graded automatically: two grading passes disagreed and no third pass settled it with quotes VERIDICAL could check, so the lower of the two grades was used.",
  single_verified_pass: "Graded automatically: only one grading pass settled on a grade with quotes VERIDICAL could check, so its grade was used.",
  caution: "Evidence check not confirmed: {reason}",
  allRecorded: "Every criterion has a recorded outcome. Automatic grades are listed under Read saved grades, where Change grade sets a different level. Continue to the integrity signals.",
  confirmUnchanged: "Recording this decision also confirms the {count} automatic grade(s) you did not change.",
  change: "Change grade",
  changeAgain: "Change grade again",
} as const;

type AutomaticGradeRow = Pick<CriterionResultOut, "decision_basis" | "support_caution" | "agreeing_passes" | "grading_passes">;

const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (_match, key: string) => String(values[key]));

// What each grading pass chose, so a disagreement behind the grade is visible.
// Kept apart from AUTOMATIC_GRADE_COPY, which the PDF export mirrors word for word.
export const GRADE_PASSES_COPY = {
  passes: "What each grading pass chose: {passes}. A pass is one automatic grading attempt, not an independent reviewer.",
  pass: "Pass {n} {grade}",
  noGrade: "no grade recorded",
} as const;

// A pass/fail criterion's votes in the card's own words; a level name is shown
// as recorded. "uncertain" is an abstention, the same as no grade.
const VOTE_WORDS: Record<string, string> = { pass: "Meets criterion", partial: "Partly meets criterion", fail: "Does not meet" };

function voteWords(vote: string | null): string | null {
  const value = vote?.trim();
  if (!value || value.toLowerCase() === "uncertain") return null;
  return VOTE_WORDS[value.toLowerCase()] ?? value;
}

// What each grading pass chose, shown only when the passes were not all the
// same grade (a disagreement, or a pass that gave none): a unanimous grade
// already says so in how it was chosen. Worded neutrally, because a pass with
// no grade abstained rather than disagreed.
export function automaticGradePasses(row: Pick<CriterionResultOut, "votes">): string | null {
  const votes = Array.isArray(row.votes) ? row.votes.filter((vote) => vote === null || typeof vote === "string").map(voteWords) : [];
  if (votes.length < 2 || votes.every((vote) => vote === votes[0])) return null;
  return fill(GRADE_PASSES_COPY.passes, {
    passes: votes.map((vote, index) => fill(GRADE_PASSES_COPY.pass, { n: index + 1, grade: vote ?? GRADE_PASSES_COPY.noGrade })).join(", "),
  });
}

// How the automatic grade was chosen. Empty when the AI did not grade.
export function automaticGradeLines(row: AutomaticGradeRow): string[] {
  if (row.decision_basis) return [AUTOMATIC_GRADE_COPY[row.decision_basis]];
  if (row.grading_passes && row.agreeing_passes) {
    return [fill(AUTOMATIC_GRADE_COPY.agreed, { agreeing: row.agreeing_passes, passes: row.grading_passes })];
  }
  return [];
}

// Cautions written before grades were kept could end "no automatic grade was
// assigned". The grade beside them was kept, so that sentence is left out.
const STALE_NO_GRADE = /[\s;,.]*no automatic grade was assigned\.?\s*$/i;

// The specific caution on an automatic grade, shown apart from how it was chosen.
export function automaticGradeCaution(row: AutomaticGradeRow): string | null {
  const reason = row.support_caution?.replace(STALE_NO_GRADE, "").trim();
  if (!reason || automaticGradeLines(row).length === 0) return null;
  return fill(AUTOMATIC_GRADE_COPY.caution, { reason: /[.!?]$/.test(reason) ? reason : `${reason}.` });
}

export function fillAutomaticConfirmation(count: number): string {
  return fill(AUTOMATIC_GRADE_COPY.confirmUnchanged, { count });
}
