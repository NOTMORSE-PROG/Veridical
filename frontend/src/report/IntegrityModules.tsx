import type { OriginalityScope as Scope, ReportOut } from "../api/types";
import { isWorkspaceSample } from "../demo/workspaceFixture";
import { CHECK_KIND_ORDER } from "../domain/checkKind";
import { ActionLink } from "../ui/ActionLink";
import { OriginalityScope } from "./OriginalityScope";
import { coverageItems } from "./reportCoverage";
import { integrityModuleLabel, type IntegrityModuleCount } from "./integrityModuleState";

export function IntegrityModuleNavigation({ modules, selected, onSelect, disabled = false }: {
  modules: readonly IntegrityModuleCount[];
  selected: string;
  onSelect: (kind: string) => void;
  disabled?: boolean;
}) {
  return <nav className="signal-integrity-modules" aria-label="Manuscript check modules">
    <p className="signal-field-hint">Choose a check to review its findings and source evidence.</p>
    <div role="group" aria-label="Choose a check module">{modules.map(module => <button type="button" key={module.kind} aria-pressed={selected === module.kind} disabled={disabled} onClick={() => onSelect(module.kind)}>
      <strong>{module.label}</strong><span>{module.findings} recorded finding{module.findings === 1 ? "" : "s"}, {module.open} open</span>
    </button>)}</div>
    {disabled && <p className="signal-field-hint">Save your private note or close the open action before changing checks.</p>}
  </nav>;
}

export function IntegrityModuleSummary({ kind, report, originalityScope, showAddAction = false }: {
  kind: string;
  report?: ReportOut;
  originalityScope?: Scope | null;
  showAddAction?: boolean;
}) {
  const prepared = isWorkspaceSample() && CHECK_KIND_ORDER.some(known => known === kind);
  const coverage = report ? coverageItems(report).filter(item => item.key === kind) : [];
  const sampleStatus = kind === "statistical_forensics" ? "Not applicable" : kind === "originality_reuse" ? "Not run" : "Prepared source observations";
  return <header className="signal-integrity-module-summary">
    <p className="signal-section-kicker">Selected check</p>
    <h3 id="integrity-module-heading" tabIndex={-1}>{integrityModuleLabel(kind)}</h3>
    {prepared ? <><strong>{sampleStatus}</strong><p>{kind === "statistical_forensics"
      ? "This software implementation report does not supply an inferential-statistics result for this prepared review. No statistical judgment is recorded."
      : kind === "originality_reuse"
        ? "Originality comparison was not run for this prepared review. No similarity judgment or matching passage pair is recorded."
        : "These are prepared observations from the original manuscript, not results of a live automated check."}</p></>
      : <p className="signal-field-hint">This section shows the saved findings for this check. Finding counts do not establish that the check completed or passed.</p>}
    {coverage.length > 0 && <div className="signal-integrity-module-limits"><strong>Recorded check limits</strong>{coverage.map(item => <div key={item.key}><p>{item.detail}</p>{item.subIssues?.map(issue => <p key={issue}>{issue}</p>)}{item.action && <ActionLink to={item.action.to} variant="quiet" aria-label={item.action.ariaLabel}>{item.action.label}</ActionLink>}</div>)}</div>}
    {kind === "originality_reuse" && <OriginalityScope scope={originalityScope} showAddAction={showAddAction} />}
    {!prepared && kind === "originality_reuse" && !originalityScope && <p className="signal-field-hint">The comparison scope was not recorded.</p>}
  </header>;
}
