import { useLocation } from "react-router";
import type { FlagOut } from "../api/types";
import { instructorAnchor } from "../domain/sourceAnchor";
import { agreementEvidenceType } from "../domain/evidenceDisplay";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";

export type AgreementStatementRole = "objective" | "result" | "statement";

/** Candidate roles are unverified; lexical overlap is not agreement evidence. */
export function AgreementComparison({ flag, findingNumber, onSource, activeRole }: { flag: FlagOut; findingNumber?: number; onSource?: (anchor: string | null, role: AgreementStatementRole) => void; activeRole?: AgreementStatementRole }) {
  const location = useLocation();
  if (flag.check_kind !== "internal_agreement") return null;
  const pair = flag.agreement_pair;
  const unmatched = flag.ai_verdict_summary === "agreement_unmatched_intent";
  const statements: { role: AgreementStatementRole; label: string; text: string; anchor: string | null }[] = pair ? [
    { role: "objective", label: "Statement A. Selected as an objective", text: pair.intent_text, anchor: pair.intent_anchor },
    { role: "result", label: "Statement B. Selected as a result", text: pair.outcome_text, anchor: pair.outcome_anchor },
  ] : [{ role: "statement", label: unmatched ? "Statement selected as an objective" : "Recorded statement", text: flag.evidence_excerpt, anchor: flag.page_anchor }];

  return <section className="signal-copy-flow" aria-label="Internal agreement evidence">
    <h3>{agreementEvidenceType(flag)}</h3>
    {pair ? <p>These statements were selected for comparison. Their roles as this study's objective and result have not been verified.</p>
      : <p>{unmatched ? "The check did not record a paired result for this statement. This does not establish that the manuscript contains no result." : "This saved finding does not include both statements needed for comparison. Inspect the manuscript manually."}</p>}
    {flag.affects_readiness === false && <p>This review lead does not change readiness.</p>}
    <div className="signal-agreement-statements">{statements.map((statement) => {
      const sourcePath = reviewDeskDestination(`/report/${flag.check_run_id}/document?finding=${flag.id}&anchor=${encodeURIComponent(statement.anchor ?? "")}`, location);
      const controlId = `agreement-${flag.id}-${statement.role}-source`;
      const label = `Open ${statement.role} location: ${instructorAnchor(statement.anchor ?? "")}`;
      return <div key={statement.role} className="signal-attached-flow" data-active={activeRole === statement.role}>
        <h4>{statement.label}</h4>
        {activeRole === statement.role && <p className="signal-save-state">Selected for source inspection</p>}
        <blockquote className="signal-evidence-quote"><span>“{statement.text}”</span>{statement.anchor?.trim() && <cite>{instructorAnchor(statement.anchor)}</cite>}</blockquote>
        {statement.anchor?.trim() ? onSource ? <Button id={controlId} variant="secondary" onClick={() => onSource(statement.anchor, statement.role)}>Show {statement.role === "objective" ? "statement A" : statement.role === "result" ? "statement B" : "recorded statement"} in manuscript</Button>
          : <ActionLink id={controlId} to={sourcePath} variant="quiet" aria-label={`${label}${findingNumber ? `. Finding ${findingNumber}` : ""}`} onClick={() => rememberRouteReturnFocus(location.pathname + location.search, sourcePath, controlId)}>{label}</ActionLink>
          : <p>No manuscript location was recorded for this statement.</p>}
      </div>;
    })}</div>
  </section>;
}
