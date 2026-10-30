import type { CitationRecencySummaryOut } from "../api/types";
import { citationRecencyNote } from "../domain/citationRecency";

// V-092: how many cited sources fall outside this format's recency window,
// beside the citation check's other notes. Nothing shows for runs that
// predate the note.
export function CitationRecencySummary({ recency }: { recency?: CitationRecencySummaryOut | null }) {
  const note = citationRecencyNote(recency);
  if (!note) return null;
  return <p className="signal-review-section__intro signal-citation-recency"><strong>{note.title}.</strong>{note.lines.map((line) => <span key={line}> {line}</span>)}</p>;
}
