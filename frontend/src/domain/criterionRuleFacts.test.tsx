import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { criterionRuleFacts } from "./criterionRuleFacts";
import { RuleFactsDetails } from "../report/RuleFactsDetails";
import { criterionAssessmentDetails } from "./criterionAssessment";
import type { CriterionResultOut } from "../api/types";

afterEach(cleanup);
it("renders actual saved headings as text and preserves duplicate order without markup", () => {
  const facts = criterionRuleFacts({ rule_id: "section_order", checked_sections: ["<script>alert(1)</script>", "Repeated", "Repeated"] })!;
  const { container } = render(<RuleFactsDetails facts={facts} earlier />);
  expect(screen.getByRole("heading", { name: "Earlier recorded rule facts" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem").map(li => li.textContent)).toEqual(["<script>alert(1)</script>", "Repeated", "Repeated"]);
  expect(container.querySelector("script")).toBeNull();
});
it.each([
  [{ rule_id: "required_section_present", target: "abstract", matched_title: "ABSTRACT" }, 'The saved heading lookup matched "ABSTRACT" for "abstract".'],
  [{ rule_id: "required_section_present", target: "glossary" }, 'The saved heading lookup searched for "glossary".'],
  [{ rule_id: "page_limit", actual_count: 10, expected: { kind: "range", low: 9.5, high: 10.5 } }, "Requirement used by this check: between 9.5 and 10.5, inclusive pages."],
  [{ rule_id: "reference_count_min_max", actual_count: 0, expected: { kind: "exact", low: 0 } }, "Recorded reference-list count: 0 entries."],
  [{ rule_id: "table_formatting_presence", total_tables: 2, captioned: 1 }, "The saved table inventory records captions for 1 of 2 tables."],
  [{ rule_id: "margins_spacing", checked_pages_or_sections: 3 }, "The saved margin check covered 3 pages or document sections."],
  [{ rule_id: "margins_spacing", side: "left", actual_pts: 50.25 }, "Recorded left margin: 50.25 points."],
  [{ rule_id: "citation_style_sniff", threshold_relation: "below" }, "The saved pattern comparison was below its recorded threshold."],
])("renders only the saved narrow observation %#", (raw, expected) => {
  expect(criterionRuleFacts(raw)?.paragraphs).toContain(expected);
});
it.each([
  null, {}, [], { rule_id: "unknown" },
  { rule_id: "page_limit", actual_count: true, expected: { kind: "max", low: 100 } },
  { rule_id: "page_limit", actual_count: 0, expected: { kind: "max", low: 100 } },
  { rule_id: "page_limit", actual_count: 10, expected: { kind: "range", low: 9 } },
  { rule_id: "page_limit", actual_count: 10, expected: { kind: "max", low: Infinity } },
  { rule_id: "section_order", checked_sections: ["A", " "] },
  { rule_id: "table_formatting_presence", total_tables: 1, captioned: 2 },
  { rule_id: "margins_spacing", checked_pages_or_sections: 1, side: "left", actual_pts: 10 },
  { rule_id: "margins_spacing", side: "left", actual_pts: NaN },
  { rule_id: "citation_style_sniff", threshold_relation: "passed" },
])("does not turn unsupported data into an observation %#", raw => {
  expect(criterionRuleFacts(raw)).toBeUndefined();
});
it("counts valid rule facts as details and retains public earlier-assessment provenance", () => {
  const row = { type: "structural", kind: "structural", basis: null, evidence: [], reasoning: null, reason: null,
    rule_facts: { rule_id: "required_section_present", target: "glossary", matched_title: null } } as unknown as CriterionResultOut;
  expect(criterionAssessmentDetails(row, false).hasDetails).toBe(true);
  expect(criterionAssessmentDetails({ ...row, resolved: true }, true).instructorRecorded).toBe(true);
  expect(criterionAssessmentDetails({ ...row, kind: "semantic", basis: "structural-alignment" }, false).ruleFacts).toBeUndefined();
  expect(criterionAssessmentDetails({ ...row, resolution: { type: "mark_pass", reason: "Private reason", ai_majority_verdict: null } }, true).instructorRecorded).toBe(false);
});
