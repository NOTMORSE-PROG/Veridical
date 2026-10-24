// F7, screens 4h/4w/4l: saved corpus preparation facts, never today's Library.
import type { OriginalityScope } from "../api/types";

export function LibraryPassageScope({ summary }: { summary: OriginalityScope["library_passage_indexing"] }) {
  if (!summary || summary.version !== "v1"
    || [summary.partial, summary.unavailable, summary.not_recorded].some((flag) => typeof flag !== "boolean")) {
    return <p className="signal-originality-scope">Passage coverage for this comparison was not recorded.</p>;
  }
  const sentences = [
    summary.partial ? "Passage matching was limited for some Library records available when this comparison began. Other extracted passages were not available for individual matching." : null,
    summary.unavailable ? "Some Library records available when this comparison began had no usable passage preparation for individual matching." : null,
    summary.not_recorded ? "Passage coverage was not recorded for some Library records available when this comparison began. Do not assume that every extracted passage was searchable." : null,
  ].filter(Boolean);
  if (!sentences.length) return null;
  return <section className="signal-originality-scope">
    <h3>Passage comparison limits</h3>
    {sentences.map((text) => <p key={text}>{text}</p>)}
    <p>Full-document and section comparisons have separate evidence. A missing match does not establish originality.</p>
  </section>;
}
