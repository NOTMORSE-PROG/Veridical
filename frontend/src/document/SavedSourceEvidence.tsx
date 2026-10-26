import type { BoundSourceEvidence } from "../api/types";
import { Button } from "../ui/Button";

export function SavedSourceEvidence({ entries, onSelect }: { entries: BoundSourceEvidence[]; onSelect: (entry: BoundSourceEvidence) => void }) {
  return <section className="signal-saved-source-evidence" aria-label="Saved source locations">
    <h3>{entries.some(entry => entry.role === "statement_b") ? "Compare the recorded statements" : "Recorded source evidence"}</h3>
    {entries.some(entry => entry.role === "statement_b") && <p>These are saved statements from the manuscript. Their presence does not establish verified objective/result roles.</p>}
    {entries.map(entry => <article key={entry.evidence_id}>
      <h4>{entry.label}</h4><blockquote><span>“{entry.quote}”</span><cite>{entry.anchor}</cite></blockquote>
      <p className="signal-field-hint">{entry.precision === "page_bbox" ? "Exact passage: the complete quotation is bound to the original source." : entry.precision_note}</p>
      <Button variant="secondary" aria-label={entry.role === "statement_a" || entry.role === "statement_b" ? undefined : `Show ${entry.label} in manuscript, ${entry.anchor}`} onClick={() => onSelect(entry)}>Show {entry.role === "statement_a" ? "statement A" : entry.role === "statement_b" ? "statement B" : "this evidence"} in manuscript</Button>
    </article>)}
  </section>;
}

export function SourcePageComparison({ entries, selectedId, onReadFull }: { entries: BoundSourceEvidence[]; selectedId?: string; onReadFull: () => void }) {
  return <section className="signal-source-page-comparison" aria-label="Original comparison pages">
    <div className="signal-source-page-comparison__toolbar"><strong>Original manuscript pages</strong><Button variant="secondary" onClick={onReadFull}>Read full manuscript</Button></div>
    <div className="signal-source-page-comparison__pages">{entries.map(entry => <figure key={entry.evidence_id} data-selected={selectedId === entry.evidence_id}>
      <figcaption><strong>{entry.label}</strong><span>PDF page {entry.page} · {entry.precision === "page_bbox" ? "Exact quotation boxed" : "Page only; no exact mark"}</span></figcaption>
      {entry.image_id ? <img src={`/sample-assets/evidence/${entry.image_id}.png`} alt={`Original manuscript PDF page ${entry.page}; ${entry.label}. ${entry.precision_note}`} /> : <p>Source page image unavailable.</p>}
    </figure>)}</div>
  </section>;
}
