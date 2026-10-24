import type { ResultRowCommon } from "../api/types";
import { criterionCoverageSuffix, privateCriterionCoverage } from "./criterionSourceCoverage";
import { criterionRuleFacts } from "./criterionRuleFacts";

type AssessmentSource = "ai" | "rule" | "unknown";
const ASSESSMENT_COPY = {
  ai: { automatic: "AI assessment details", earlier: "Earlier AI assessment", explanation: "Recorded AI explanation:", earlierExplanation: "Earlier AI explanation:", missing: "No AI explanation is available in this assessment record." },
  rule: { automatic: "Rule-check details", earlier: "Earlier rule-check record", explanation: "Recorded rule-check explanation:", earlierExplanation: "Earlier rule-check explanation:", missing: "No rule-check explanation is available in this assessment record." },
  unknown: { automatic: "Recorded assessment details", earlier: "Recorded assessment details", explanation: "Recorded explanation, source not identified:", earlierExplanation: "Recorded explanation, source not identified:", missing: "No explanation is available in this assessment record." },
} as const;

export function criterionAssessmentDetails(row: ResultRowCommon, publicView: boolean) {
  const source: AssessmentSource = (row.type === "semantic" || row.type === "structural") && row.kind === "semantic" && row.basis === "llm"
    ? "ai" : row.type === "structural" && row.kind === "structural" && row.basis === null ? "rule" : "unknown";
  const instructorRecorded = publicView ? row.resolved === true : row.resolution != null;
  const explanation = Boolean(row.reasoning?.trim());
  const hasLocations = Boolean(row.anchor?.trim() || row.evidence.some((item) => item.anchor?.trim()));
  const sourceCoverage = privateCriterionCoverage(row, publicView);
  const ruleFacts = source === "rule" ? criterionRuleFacts(row.rule_facts) : undefined;
  const hasDetails = Boolean(explanation || hasLocations || row.evidence.length || row.reason?.trim() || sourceCoverage || ruleFacts);
  const copy = ASSESSMENT_COPY[source];
  const relationship: string[] = [];
  if (instructorRecorded && hasDetails) {
    if (source !== "unknown") {
      relationship.push(publicView
        ? "The current result was recorded by the instructor. These details were retained from the automated assessment before that judgment. They do not record the instructor's reason."
        : "These details were retained from the automated assessment before the instructor judgment.");
    } else if (explanation || hasLocations || row.evidence.length) {
      relationship.push(publicView
        ? "The current result was recorded by the instructor. The report does not identify the source of these assessment details. They do not record the instructor's reason."
        : `The report does not identify the source of ${explanation ? "this explanation" : "these assessment details"}.`);
    }
    if (!publicView) {
      if (row.resolution?.reason?.trim()) relationship.push("The instructor's reason is shown above.");
      if (hasLocations) relationship.push(row.resolution?.reason?.trim()
        ? "The record does not link that reason to these manuscript locations."
        : "These locations are not recorded as references supplied with an instructor reason.");
    }
  }
  return { source, instructorRecorded, hasDetails, hasLocations, sourceCoverage, ruleFacts,
    summary: (instructorRecorded ? copy.earlier : copy.automatic) + criterionCoverageSuffix(sourceCoverage),
    explanationLabel: instructorRecorded ? copy.earlierExplanation : copy.explanation,
    missingExplanation: copy.missing, relationship: relationship.join(" ") };
}
