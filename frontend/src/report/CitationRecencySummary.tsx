import type { CitationRecencySummaryOut } from "../api/types";
import { CITATION_RECENCY_COPY, citationRecencyNote } from "../domain/citationRecency";

// V-092: how many cited sources fall outside this format's recency window,
// beside the citation check's other notes. The counts and whether they count
// toward readiness are shown; how they were counted is one step away.
// Nothing shows for runs that predate the note.
export function CitationRecencySummary({ recency }: { recency?: CitationRecencySummaryOut | null }) {
  const note = citationRecencyNote(recency);
  if (!note) return null;
  return <div className="signal-review-section__intro signal-citation-recency">
    <p><strong>{note.title}.</strong>{note.summary.map((line) => <span key={line}> {line}</span>)}</p>
    {note.details.length > 0 && <details>
      <summary>{CITATION_RECENCY_COPY.details}</summary>
      {note.details.map((line) => <p key={line}>{line}</p>)}
    </details>}
  </div>;
}
