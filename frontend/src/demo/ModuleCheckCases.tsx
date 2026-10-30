import { useRef, useState } from "react";
import { useParams, useLocation } from "react-router";
import type { FlagRegionOut } from "../api/types";
import { PdfPane } from "../document/PdfPane";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import cases from "./moduleCases.json";
import { isWorkspaceSample } from "./workspaceFixture";
import './moduleExamples.css';

type Document = typeof cases.documents[number];
type Source = typeof cases.statistics.checked_values[number]["source"];

function SourceReader({ document, source, identity }: { document: Document; source: Source; identity: number }) {
  const valid = source.boxes_origin === "bottom-left" && source.source_sha256 === document.source_sha256 && source.page >= 1 && source.page <= document.pdf_pages && source.boxes.every(box => box.length === 4 && box.every(Number.isFinite));
  const region: FlagRegionOut = { flag_id: -identity, kind: source.precision === "page_bbox" && source.boxes.length ? "page_bbox" : "page_only", page: source.page, end_page: null, bbox: source.boxes[0] as [number, number, number, number] ?? null, all_bboxes: source.boxes as [number, number, number, number][], paragraph: null, index: null };
  return <section className="signal-module-case-source" aria-label={`Original source: ${document.title}`}>
    <header><strong>{document.title}</strong><span>Original PDF · {document.pdf_pages} pages</span><p>{source.precision === "page_bbox" ? source.highlighted_quote === source.quote ? "The saved quotation is boxed on this original page." : source.precision_note : "This is the recorded page. No exact passage is marked."}</p></header>
    {valid ? <div className="signal-module-case-reader"><PdfPane fileUrl={document.pdf_asset} regions={[region]} flags={[]} selectedFlagId={region.flag_id} onSelectFlag={() => {}} requestedPage={source.page} requestedPageAttempt={identity} focusSelection={false} /></div> : <Alert title="This source could not be verified" tone="error">No unrelated page is shown.</Alert>}
    <p className="signal-field-hint">{document.authors.join(" · ")} ({document.year}). <a href={document.publisher_url} target="_blank" rel="noreferrer">Published source</a> · <a href={document.licence_url} target="_blank" rel="noreferrer">{document.licence_short}</a></p>
  </section>;
}

export function ModuleCheckCasePage() {
  const { kind } = useParams<{ kind: string }>();
  const location = useLocation();
  const returnPath = typeof location.state?.returnTo === 'string' && /^\/report\/1(?:\/document)?\?/.test(location.state.returnTo)
    ? location.state.returnTo : `/report/1?review_step=findings&module=${kind === 'statistics' ? 'statistical_forensics' : 'originality_reuse'}#integrity-signals`;
  const [selected, setSelected] = useState(0);
  const [sourceAttempt, setSourceAttempt] = useState(0);
  const [pairAttempt, setPairAttempt] = useState(0);
  const [pairIndex, setPairIndex] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const statistics = kind === "statistics";
  const validKind = statistics || kind === "reuse";
  useRouteFocus("Completed manuscript check - VERIDICAL", heading);
  if (!isWorkspaceSample() || !validKind) return <Alert title="This check record is unavailable" tone="info"><ActionLink to="/dashboard">Return to Review Desk</ActionLink></Alert>;
  const record = statistics ? cases.statistics : cases.reuse;
  const documentId = statistics ? cases.statistics.document_id : cases.reuse.checked_document_id;
  const document = cases.documents.find(item => item.id === documentId)!;
  const library = cases.documents.find(item => item.id === cases.reuse.library_document_id)!;
  const stat = cases.statistics.checked_values[selected];
  const pair = cases.reuse.pairs[pairIndex];
  const excerpts = pair.source_excerpt_cards;
  return <div className="signal-route signal-page-flow signal-module-case">
    <header className="signal-route-header"><div><p className="signal-eyebrow">Check record</p><h1 ref={heading} tabIndex={-1}>{statistics ? "Statistical results checked" : "Related passages to review"}</h1><p className="signal-route-header__intro">{document.title}</p><p>{document.authors.join(" · ")} ({document.year})</p></div></header>
    <nav aria-label="Check record navigation"><ActionLink to={returnPath} variant="quiet">Back to review findings</ActionLink></nav>
    <section className="signal-module-case-summary"><h2>{statistics ? "All 3 extracted comparisons are consistent" : "Similar descriptions of the same checking tool"}</h2><p>{statistics ? "The original PDF was read and its three supported chi-square results were recalculated. Each recalculated p-value satisfies the bound printed in the paper. No inconsistency was recorded for these three results." : cases.reuse.relationship.explanation}</p><p className="signal-field-hint">Results apply to the manuscript named above. Review each source before drawing a conclusion.</p></section>
    {statistics ? <>
      <section className="signal-module-case-values" aria-labelledby="statistical-values-heading"><h2 id="statistical-values-heading">Reported values and recalculation</h2><div className="signal-table-scroll"><table><thead><tr><th>Source</th><th>Reported test</th><th>Reported p</th><th>Recalculated p</th><th>Result</th></tr></thead><tbody>{cases.statistics.checked_values.map((entry, index) => <tr key={entry.id}><td><button type="button" className="signal-evidence-source-link" aria-pressed={selected === index} onClick={() => { setSelected(index); setSourceAttempt(value => value + 1); }}>Read PDF page {entry.pdf_page}, result {index + 1}</button></td><td>Chi-square {entry.statistic}; df {entry.df}</td><td>p {entry.reported_comparison} {entry.reported_p}</td><td>{entry.computed_p.toFixed(8)}</td><td>Consistent</td></tr>)}</tbody></table></div><p>Choose a source link to locate that result in the original manuscript below.</p></section>
      <blockquote>{stat.source_quote}</blockquote><SourceReader document={document} source={stat.source} identity={100 + selected + sourceAttempt * 10} />
      <p className="signal-field-hint">{cases.statistics.scope_note}</p>
    </> : <>
      <section className="signal-module-case-summary"><h2>Why the relationship matters</h2><p>The newer paper cites the earlier study, and several authors are shared. Read the selected passages in context. The saved similarity result concerns the full passages; the shorter quotations locate the specific sentences on their original pages.</p><p>Checked manuscript: {document.title}. Library source: {library.title}.</p></section>
      <section className="signal-module-case-summary"><h2>Saved comparison records</h2><p>The check saved {cases.reuse.n_saved_findings} review prompts: a whole-document lead, a section lead, and two passage comparisons. These are related papers, so the relationship needs to be considered when reviewing every lead.</p><ul>{cases.reuse.saved_finding_scopes.map(item => <li key={item.saved_flag_id}><strong>{item.scope === "whole_document" ? "Whole paper" : item.scope === "chapter" ? "Saved section" : "Passage"}:</strong> {item.scope === "whole_document" ? "A broad comparison of the complete paper." : item.scope === "chapter" ? "A comparison of the saved section. Use the passage pairs below to inspect specific source text." : `A saved source comparison at ${item.page_anchor}.`}</li>)}</ul></section>
      <nav className="signal-control-cluster" aria-label="Choose a saved passage comparison">{cases.reuse.pairs.map((item, index) => <button key={item.id} type="button" className="signal-button signal-button--secondary" aria-pressed={pairIndex === index} onClick={() => { setPairIndex(index); setPairAttempt(value => value + 1); }}>{item.title}</button>)}</nav>
      <section className="signal-module-case-summary"><h2>{pair.title}</h2><p>{pair.explanation}</p><p className="signal-field-hint">{pair.similarity_scope}</p></section>
      <div className="signal-module-case-pair">{excerpts.map((entry, index) => <div key={entry.file}><h2>{index === 0 ? "Checked manuscript" : "Library source"}</h2><blockquote>“{entry.quote}”</blockquote><p className="signal-field-hint">This excerpt is on PDF page {entry.pdf_page}. The full compared passage begins on PDF page {index === 0 ? pair.checked_full_chunk.page : pair.library_full_chunk.page}.</p><SourceReader document={index === 0 ? document : library} source={entry.source} identity={200 + pairIndex * 2 + index + pairAttempt * 10} /></div>)}</div>
      <details className="signal-module-case-passages"><summary>Read the full passages used for this comparison</summary><div><section><h3>Checked manuscript passage</h3><p>{pair.checked_full_chunk.text}</p></section><section><h3>Library passage</h3><p>{pair.library_full_chunk.text}</p></section></div></details>
    </>}
    <details className="signal-module-case-method"><summary>Check method and sources</summary><p>{record.basis}</p><p>{statistics ? cases.statistics.scope_note : cases.reuse.scope_note}</p>{!statistics && <p>The saved comparison used {cases.reuse.checked_passage_count} manuscript passages and {cases.reuse.library_passage_count} Library passages. Both saved passage comparisons are available above; the selected pair is shown with its original sources. The local embedding model was {cases.reuse.model.repo}, revision {cases.reuse.model.commit_hash}. It did not call an external grading provider.</p>}<ul>{(statistics ? [document] : [document, library]).map(item => <li key={item.id}>{item.title}. DOI {item.doi}. Original PDF SHA-256: {item.source_sha256}. <a href={item.pdf_asset} download>Download original PDF</a></li>)}</ul></details>
    <div className="signal-control-cluster"><ActionLink to="/dashboard" variant="quiet">Return to Review Desk</ActionLink></div>
  </div>;
}
