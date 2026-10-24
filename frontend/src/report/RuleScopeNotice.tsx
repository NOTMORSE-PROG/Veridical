import type { ResultRowCommon } from "../api/types";
import { RULE_SCOPE_QUALIFICATION, unconfirmedAutomaticRuleRows } from "../domain/ruleScope";
import { WorkflowLink } from "../routing/WorkflowLink";
import { useLocation } from "react-router";
import { SAVED_CRITERION_PARAM } from "./savedJudgment";
import { focusReportSection } from "./reportSectionNavigation";

export function RuleScopeNotice({ earlier = false }: { earlier?: boolean }) {
  return <p className="signal-rule-scope">{earlier && <strong>Earlier rule record. </strong>}{RULE_SCOPE_QUALIFICATION}</p>;
}

export function RuleScopeSummary({ rows, checkRunId, publicView = false, linkToRecords = true }: {
  rows: ResultRowCommon[]; checkRunId: number; publicView?: boolean; linkToRecords?: boolean;
}) {
  const location = useLocation();
  const count = unconfirmedAutomaticRuleRows(rows, publicView).length;
  if (!count) return null;
  const search = new URLSearchParams(location.search);
  if (!publicView) search.set(SAVED_CRITERION_PARAM, "all");
  return <p className="signal-rule-scope">
    Complete-requirement coverage is unconfirmed for {count} recorded rule {count === 1 ? "result" : "results"}. Review these records before relying on this band.{" "}
    {linkToRecords && <WorkflowLink to={{ pathname: publicView ? location.pathname : `/report/${checkRunId}`, search: search.toString(), hash: "criteria-results-heading" }} onClick={event => focusReportSection(event, location, "criteria-results-heading")}>Review criterion records</WorkflowLink>}
  </p>;
}
