import { useManuscriptViewer } from "./useReport";
import type { BoundSourceEvidence, EvidenceItem } from "../api/types";
import { criterionEvidenceGuide } from "../demo/criterionEvidenceGuide";
import { criterionSource, evidenceFor } from "../demo/sourceEvidence";
import { isWorkspaceSample, sampleCriteria } from "../demo/workspaceFixture";
import { criterionAnchorRegion } from "../document/criterionAnchor";
import { organizationSourceCorrection } from "../demo/preparedAiReview";

interface Props {
  checkRunId: number;
  criterionId: number;
  title: string;
  reasoning: string | null;
  evidence: readonly EvidenceItem[];
  reasoningId?: string;
  onOpenEvidence?: (index: number) => void;
  previews?: boolean;
  selectedIndex?: number;
}

export function CriterionEvidencePacket({ checkRunId, criterionId, title, reasoning, evidence, reasoningId, onOpenEvidence, previews = true, selectedIndex }: Props) {
  const guide = isWorkspaceSample() ? criterionEvidenceGuide(checkRunId, criterionId, title, evidence) : null;
  const knownEvidence = evidenceFor("criterion", criterionId);
  const correction = isWorkspaceSample() && checkRunId === 1 && title === sampleCriteria.find(item => item.id === criterionId)?.text && evidence.length > 0 && evidence.every((entry, index) => entry.quote === knownEvidence[index]?.quote && entry.anchor === knownEvidence[index]?.anchor) ? organizationSourceCorrection(criterionId, reasoning) : null;
  const groups = guide ?? [{ title: "Saved passages for this criterion", explanation: "Read the explanation together with these saved passages. No more specific relationship was recorded between individual passages and parts of the explanation.", indexes: evidence.map((_, index) => index) }];
  const selected = selectedIndex === undefined ? undefined : groups.find(group => group.indexes.includes(selectedIndex));
  return <section className="signal-criterion-packet" aria-label={`Evidence and explanation for ${title}`}>
    <div className="signal-resolution-context-note"><h4>Assessment and explanation</h4><p id={reasoningId}>{reasoning || "No additional explanation was recorded for this criterion."}</p>{correction && <p><strong>{correction}</strong> The saved assessment above is retained as recorded.</p>}</div>
    <p className="signal-field-hint">Read these passages together to check how the assessment follows from the manuscript. Source links keep the explanation with its original pages.</p>
    {groups.filter(group => !selected || group === selected).map(group => {
      const previewIndexes = guide && selectedIndex !== undefined ? [selectedIndex, ...(group.previewIndexes ?? []).filter(index => index !== selectedIndex)].slice(0, 2) : group.previewIndexes;
      return <section className="signal-criterion-packet__group" key={group.title}>
      <header><h4>{group.title}</h4><p>{group.explanation}</p>{group.note && <p className="signal-field-hint">{group.note}</p>}</header>
      <div className="signal-criterion-packet__quotations">{group.indexes.map(index => {
        const entry = evidence[index];
        const source = guide ? criterionSource(criterionId, entry) : undefined;
        const placeable = criterionAnchorRegion(entry.anchor) !== null;
        return <figure key={`${index}:${entry.anchor}`} data-selected={selectedIndex === index || undefined}>
          <figcaption><strong>{source?.label ?? `Saved passage ${index + 1}`}</strong><span>{entry.anchor}</span></figcaption>
          <blockquote>“{entry.quote}”</blockquote>
          {onOpenEvidence && placeable ? <button type="button" className="signal-evidence-source-link" onClick={() => onOpenEvidence(index)}>{source?.precision === "page_bbox" ? `Read highlighted passage on page ${source.page}` : source?.page ? `Open page ${source.page} (page location only)` : `Read saved passage: ${entry.anchor}`}</button> : !placeable && <p className="signal-field-hint">This saved location cannot be placed. Inspect the manuscript manually.</p>}
        </figure>;
      })}</div>
      {guide && previews && previewIndexes && <CriterionSourcePreviews checkRunId={checkRunId} entries={previewIndexes.flatMap(index => { const source = criterionSource(criterionId, evidence[index]); return source ? [{ source, index }] : []; })} onOpenEvidence={onOpenEvidence} />}
    </section>; })}
    {!evidence.length && <p>No source quotation was saved for this criterion.</p>}
  </section>;
}

function CriterionSourcePreviews({ checkRunId, entries, onOpenEvidence }: { checkRunId: number; entries: { source: BoundSourceEvidence; index: number }[]; onOpenEvidence?: (index: number) => void }) {
  const viewer = useManuscriptViewer(checkRunId);
  if (viewer.isPending) return <p role="status">Loading the original source pages...</p>;
  if (!viewer.data?.available || entries.some(({ source }) => source.check_run_id !== checkRunId || source.manuscript_id !== viewer.data.manuscript_id || source.source_sha256 !== viewer.data.source_sha256)) return <p className="signal-field-hint">Original page images are unavailable for this record. The saved passages remain above.</p>;
  return <details className="signal-criterion-packet__preview" open><summary>Original source pages for this comparison</summary><div>{entries.map(({ source, index }) => source.image_id ? <figure key={source.evidence_id}><figcaption>{source.label} · PDF page {source.page}</figcaption><img loading="lazy" width={source.image_width ?? undefined} height={source.image_height ?? undefined} src={`/sample-assets/evidence/${source.image_id}.png`} alt={`Original manuscript page ${source.page}. ${source.label}. ${source.precision_note}`} />{onOpenEvidence && <button type="button" className="signal-evidence-source-link" onClick={() => onOpenEvidence(index)}>Read this source with its explanation</button>}</figure> : null)}</div></details>;
}
