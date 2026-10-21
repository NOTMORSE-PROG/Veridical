import type { FigureReadingOut } from "../api/types";
import { instructorAnchor } from "../domain/sourceAnchor";
import { ActionLink } from "../ui/ActionLink";

const REASONS: Record<string, string> = {
  not_requested: "Image reading was not requested.",
  selection_limit: "This image was outside the reading limit for this check.",
  source_or_relevance_unavailable: "No supported, relevant image region was selected.",
  source_mismatch: "The saved reading does not match this source image.",
  invalid_response: "The image reading could not be validated.",
  uncertain_reading: "The image could not be read reliably. Its contents remain unassessed.",
  no_assessable_figure: "No assessable chart, table, diagram or implementation screenshot was identified. Its contents remain unassessed.",
  support_unconfirmed: "The visual support check did not confirm this reading. Its contents remain unassessed.",
  service_unavailable: "The image-reading service was unavailable. The original image remains available.",
  quota_exhausted: "AI capacity was unavailable. The original image remains available.",
};

export function FigureReadingDetails({ reading }: { reading?: FigureReadingOut | null }) {
  if (!reading) return <p>Not assessed. Image reading was not recorded for this check.</p>;
  const available = reading.status === "available";
  return <details>
    <summary>Image reading · {available ? "Reading available" : reading.status === "needs_review" ? "Needs visual review" : "Not assessed"}</summary>
    <p>{available ? "A visual reading is available for this image. Check it against the original." : REASONS[reading.reason] ?? "Image contents remain unassessed."}</p>
    {available && <>
      <p><strong>Content type</strong>: {reading.kind?.replaceAll("_", " ")}</p>
      <p><strong>Visible content</strong>: {reading.visible_content}</p>
      <p><strong>Legible labels</strong>: {reading.visible_labels.join("; ")}</p>
      <p><strong>Limits of this reading</strong>: {reading.limitations.join("; ")}</p>
      <p>{reading.support_reason}</p>
    </>}
    {reading.source_caption && <p><strong>Nearby source caption</strong>: {reading.source_caption}{reading.caption_anchor && <> ({instructorAnchor(reading.caption_anchor)})</>}</p>}
    {available && <details><summary>Technical record</summary><p>Schema {reading.schema_version}. Cached responses are revalidated; reuse is not instructor verification.</p><pre className="signal-figure-reading-record">{JSON.stringify(reading.source_binding, null, 2)}</pre></details>}
  </details>;
}

export function FigureReadingsSummary({ readings, checkRunId }: { readings?: FigureReadingOut[] | null; checkRunId: number }) {
  if (!readings?.length) return null;
  const count = readings.filter((item) => item.status === "available").length;
  return <section className="signal-evidence-section" aria-label="Saved image readings">
    <h2>Saved image readings</h2>
    <p>Visual readings are available for {count} of {readings.length} image occurrences. {readings.length - count} image occurrences remain unassessed.</p>
    <p>Visual readings do not establish that statistical values in images were checked, software works, or rubric criteria were met.</p>
    <details><summary>Image reading details</summary>{readings.map((reading) => <div key={reading.image_index}>
      <h3>Source image {reading.image_index + 1}</h3>
      {reading.source_anchor && <ActionLink variant="quiet" to={`/report/${checkRunId}/document?anchor=${encodeURIComponent(reading.source_anchor)}&coverage=images`}>Open {instructorAnchor(reading.source_anchor)}</ActionLink>}
      <FigureReadingDetails reading={reading} />
    </div>)}</details>
  </section>;
}
