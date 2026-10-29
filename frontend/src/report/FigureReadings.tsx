import type { FigureReadingOut } from "../api/types";
import { instructorAnchor } from "../domain/sourceAnchor";
import { useId, useState } from "react";
import { LIST_PAGE_SIZE } from "../config/ui";
import { ActionLink } from "../ui/ActionLink";
import { EmptyState } from "../ui/EmptyState";
import { Pagination, pageOf } from "../ui/Pagination";

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
    <summary>{available ? "Show what was read" : "Why this was not read"}</summary>
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

type ReadingFilter = "available" | "unassessed" | "all";

function readingStatus(reading: FigureReadingOut) {
  return reading.status === "available" ? { label: "Reading available", tone: "available" }
    : reading.status === "needs_review" ? { label: "Needs visual review", tone: "review" } : { label: "Not assessed", tone: "unassessed" };
}

export function FigureReadingsSummary({ readings, checkRunId }: { readings?: FigureReadingOut[] | null; checkRunId: number }) {
  const count = readings?.filter((item) => item.status === "available").length ?? 0;
  const [filter, setFilter] = useState<ReadingFilter>(count > 0 ? "available" : "all");
  const [page, setPage] = useState(1);
  const headingId = useId();
  if (!readings?.length) return null;
  const shown = readings.filter((item) => filter === "all" || (filter === "available") === (item.status === "available"));
  const current = Math.min(page, Math.max(1, Math.ceil(shown.length / LIST_PAGE_SIZE.imageReadings)));
  const choose = (next: ReadingFilter) => { setFilter(next); setPage(1); };
  return <section className="signal-review-section signal-figure-readings" aria-labelledby={headingId}>
    <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Image evidence</p><h2 id={headingId}>Saved image readings</h2></div><span>{count} of {readings.length} read</span></div>
    <p className="signal-review-section__intro">Visual readings are available for {count} of {readings.length} image occurrences; {readings.length - count} remain unassessed. Visual readings do not establish that statistical values in images were checked, software works, or rubric criteria were met.</p>
    <div className="signal-review-section__body">
      <div className="signal-segmented" role="group" aria-label="Show image readings">
        {([["available", `Readings available (${count})`], ["unassessed", `Not assessed (${readings.length - count})`], ["all", `All (${readings.length})`]] as const).map(([value, label]) =>
          <button key={value} type="button" aria-pressed={filter === value} onClick={() => choose(value)}>{label}</button>)}
      </div>
      {shown.length === 0 ? <EmptyState title={filter === "available" ? "No image readings available" : "No unassessed images"}><p>{filter === "available" ? "No image in this check has a saved visual reading." : "Every image occurrence has a saved reading."}</p></EmptyState>
        : <ol className="signal-figure-reading-grid">{pageOf(shown, current, LIST_PAGE_SIZE.imageReadings).map((reading) => {
          const status = readingStatus(reading);
          return <li key={reading.image_index} className="signal-figure-reading-card">
            <div className="signal-figure-reading-card__top"><h3>Image {reading.image_index + 1}</h3><span className={`signal-figure-reading-status signal-figure-reading-status--${status.tone}`}>{status.label}</span></div>
            {reading.source_anchor ? <ActionLink variant="secondary" to={`/report/${checkRunId}/document?anchor=${encodeURIComponent(reading.source_anchor)}`}>Open {instructorAnchor(reading.source_anchor)}</ActionLink>
              : <p className="signal-field-hint">No source location was recorded.</p>}
            <FigureReadingDetails reading={reading} />
          </li>;
        })}</ol>}
      <Pagination label="Image reading pages" page={current} pageSize={LIST_PAGE_SIZE.imageReadings} total={shown.length} onPage={setPage} />
    </div>
  </section>;
}
