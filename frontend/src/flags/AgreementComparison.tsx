import { useLocation } from "react-router";
import type { FlagOut } from "../api/types";
import { instructorAnchor } from "../domain/sourceAnchor";
import { agreementEvidenceType } from "../domain/evidenceDisplay";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { ActionLink } from "../ui/ActionLink";

/** Candidate roles are unverified; lexical overlap is not agreement evidence. */
export function AgreementComparison({ flag, findingNumber }: { flag: FlagOut; findingNumber?: number }) {
  const location = useLocation();
  if (flag.check_kind !== "internal_agreement") return null;
  const pair = flag.agreement_pair;
  const unmatched = flag.ai_verdict_summary === "agreement_unmatched_intent";
  const statements = pair ? [
    { role: "objective", label: "Statement selected as an objective", text: pair.intent_text, anchor: pair.intent_anchor },
    { role: "result", label: "Statement selected as a result", text: pair.outcome_text, anchor: pair.outcome_anchor },
  ] : [{ role: "statement", label: unmatched ? "Statement selected as an objective" : "Recorded statement", text: flag.evidence_excerpt, anchor: flag.page_anchor }];

  return <section className="signal-copy-flow" aria-label="Internal agreement evidence">
    <h3>{agreementEvidenceType(flag)}</h3>
    {pair ? <p>These statements were selected for comparison. Their roles as this study's objective and result have not been verified.</p>
      : <p>{unmatched ? "The check did not record a paired result for this statement. This does not establish that the manuscript contains no result." : "This saved finding does not include both statements needed for comparison. Inspect the manuscript manually."}</p>}
    {flag.affects_readiness === false && <p>This review lead does not change readiness.</p>}
    {statements.map((statement) => {
      const sourcePath = reviewDeskDestination(`/report/${flag.check_run_id}/document?finding=${flag.id}&anchor=${encodeURIComponent(statement.anchor ?? "")}`, location);
      const controlId = `agreement-${flag.id}-${statement.role}-source`;
      const label = `Open ${statement.role} location: ${instructorAnchor(statement.anchor ?? "")}`;
      return <div key={statement.role} className="signal-attached-flow">
        <h4>{statement.label}</h4>
        <blockquote className="signal-evidence-quote"><span>“{statement.text}”</span>{statement.anchor?.trim() && <cite>{instructorAnchor(statement.anchor)}</cite>}</blockquote>
        {statement.anchor?.trim() ? <ActionLink id={controlId} to={sourcePath} variant="quiet" aria-label={`${label}${findingNumber ? `. Finding ${findingNumber}` : ""}`} onClick={() => rememberRouteReturnFocus(location.pathname + location.search, sourcePath, controlId)}>{label}</ActionLink>
          : <p>No manuscript location was recorded for this statement.</p>}
      </div>;
    })}
  </section>;
}
