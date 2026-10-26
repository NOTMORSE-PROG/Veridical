import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router";
import type { SharedReportOut } from "../api/types";
import { WeightImportanceTag } from "../components/WeightImportanceTag";
import { AI_REVIEW_BASIS, AI_REVIEW_CONFIRMED_BASIS } from "../demo/preparedAiReview";
import { Button } from "../ui/Button";
import { SignalPublicFlags } from "./SignalReviewSections";
import { SharedEvidenceImages, SharedEvidencePager } from "./SharedEvidenceImages";
import { associateSharedEvidence, sharedGradeLabel } from "./sharedEvidence";

export function SharedReportContent({ data, token }: { data: SharedReportOut; token: string }) {
  const [params, setParams] = useSearchParams();
  const section = params.get("section") === "findings" ? "findings" : "grades";
  const { report, flags } = data;
  const evidence = associateSharedEvidence(data.evidence_images ?? [], report, flags);
  const chosen = report.results.find(row => String(row.criterion_id) === params.get("criterion")) ?? report.results[0];
  const chosenId = chosen?.criterion_id;
  const focusRequested = useRef(false);
  useEffect(() => {
    if (!focusRequested.current) return;
    document.getElementById(section === "findings" ? "flags-heading" : chosenId !== undefined ? "shared-selected-grade" : "shared-grades-heading")?.focus();
    focusRequested.current = false;
  }, [section, chosenId]);
  function select(key: string, value: string) {
    focusRequested.current = true;
    const next = new URLSearchParams(params); next.set(key, value); setParams(next);
  }
  return <>
    <nav className="signal-control-cluster" aria-label="Shared report sections"><Button variant={section === "grades" ? "brand" : "secondary"} aria-pressed={section === "grades"} onClick={() => select("section", "grades")}>Grades</Button><Button variant={section === "findings" ? "brand" : "secondary"} aria-pressed={section === "findings"} onClick={() => select("section", "findings")}>Findings</Button></nav>
    {section === "grades" ? <section className="signal-review-section" aria-labelledby="shared-grades-heading">
      <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Recorded grades</p><h2 id="shared-grades-heading" tabIndex={-1}>Grades and supporting evidence</h2></div><span>{report.results.length} criteria</span></div>
      <p className="signal-review-section__intro">Choose a grade to read its explanation and the original manuscript pages that support it.</p>
      <nav className="signal-shared-grade-choices" aria-label="Choose a criterion">{report.results.map(row => <button key={row.criterion_id} type="button" aria-pressed={chosen?.criterion_id === row.criterion_id} onClick={() => select("criterion", String(row.criterion_id))}><strong>{row.text}</strong><span>{sharedGradeLabel(row)}</span></button>)}</nav>
      {chosen ? <article key={chosen.criterion_id} className="signal-shared-selected-grade" aria-labelledby="shared-selected-grade">
        <header><h3 id="shared-selected-grade" tabIndex={-1}>{chosen.text}</h3><p><strong>Recorded grade: {sharedGradeLabel(chosen)}</strong></p><p>Importance: <WeightImportanceTag importance={chosen.weight_importance} /></p><p className="signal-field-hint">{chosen.resolved ? "Grade confirmed or changed by the teacher." : chosen.basis === AI_REVIEW_BASIS ? "AI review prepared in this session." : chosen.basis === AI_REVIEW_CONFIRMED_BASIS ? "Teacher's grade following an AI review." : "The report's recorded assessment."}</p></header>
        <div className="signal-shared-packet">
          <div className="signal-shared-packet__explanation"><h4>Why this grade</h4><p>{chosen.reasoning || chosen.reason || "No additional explanation was shared."}</p>{chosen.reasoning && chosen.reason && chosen.reason !== chosen.reasoning && <p>{chosen.reason}</p>}
            <details><summary>All saved quotations</summary>{chosen.evidence.length ? chosen.evidence.map((item, index) => <blockquote key={`${index}:${item.anchor}`}><span>“{item.quote}”</span><cite>{item.anchor}</cite></blockquote>) : <p>No source quotation was saved for this criterion.</p>}</details>
          </div>
          <SharedEvidencePager key={`${token}:criterion:${chosen.criterion_id}`} token={token} images={evidence.criteria.get(chosen.criterion_id) ?? []} />
        </div>
      </article> : <p className="signal-review-section__intro">No grades were shared in this report.</p>}
    </section> : <SignalPublicFlags flags={flags} originalityScope={report.originality_scope} renderEvidence={flag => <div className="signal-shared-packet"><div className="signal-shared-packet__explanation"><h4>What needs checking</h4><p>{flag.review_explanation || "No additional explanation was shared. Compare the saved quotation with its original source before drawing a conclusion."}</p><p><strong>Recorded action:</strong> {flag.confirmed_citation_source ? "Source confirmed by the teacher" : flag.overridden ? "Dismissed by the teacher" : "Open for teacher review"}</p><p className="signal-field-hint">This is a read-only record. Findings are review prompts, not separate grades.</p></div><SharedEvidencePager key={`${token}:flag:${flag.id}`} token={token} images={evidence.findings.get(flag.id) ?? []} /></div>} />}
    {evidence.additional.length > 0 && <details className="signal-shared-additional"><summary>Additional shared evidence ({evidence.additional.length})</summary><p>These source images were shared without a matching grade or finding identity. They are retained here without assigning them to an item.</p><SharedEvidenceImages token={token} images={evidence.additional} /></details>}
  </>;
}
