// BUG-676: what one integrity check left out, on the adviser's status card:
// the reason, the pages, and counts of what it did not check. Read from the
// shared link's own coverage record; nothing is worked out here.
import { useState } from "react";
import type { PublicCoverageOut } from "../api/types";
import { pageRanges } from "../domain/criterionSourceCoverage";
import { Button } from "../ui/Button";
import { PageRanges } from "./PageRanges";
import { coverageCopy } from "./reportCoverage";

// Past this many runs of pages, the list sits behind a button.
const PAGE_RUNS_INLINE = 8;

type CountField = "n_images_unchecked" | "n_tables_unchecked" | "n_equations_unchecked" | "n_references_unverified"
  | "n_citation_links_unchecked" | "n_claims_unchecked" | "n_skipped_service" | "n_skipped_capacity" | "n_skipped_unreadable";

const COUNTS: [CountField, string][] = [
  ["n_images_unchecked", "Images not checked"],
  ["n_tables_unchecked", "Tables not checked"],
  ["n_equations_unchecked", "Equations not checked"],
  ["n_references_unverified", "References not verified"],
  ["n_citation_links_unchecked", "Citation links not checked"],
  ["n_claims_unchecked", "Cited claims not checked"],
  ["n_skipped_service", "Skipped, service unavailable"],
  ["n_skipped_capacity", "Skipped, AI capacity used up"],
  ["n_skipped_unreadable", "Skipped, could not be read"],
];

const pagesLabel = (pages: number[]) => pages.length === 1 ? "Page" : "Pages";

function PageList({ id, pages, count }: { id: string; pages: number[]; count: number }) {
  const [open, setOpen] = useState(false);
  if (!pages.length) return <span>{count} {count === 1 ? "page" : "pages"}. This link does not say which.</span>;
  if (pageRanges(pages).split(", ").length <= PAGE_RUNS_INLINE) return <span>{pagesLabel(pages)} <PageRanges pages={pages} /></span>;
  return <span className="signal-shared-left-out__pages">
    <span>{pages.length} pages</span>
    <Button variant="quiet" className="signal-shared-left-out__toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>{open ? "Hide" : "Show"} page numbers</Button>
    <span id={id} hidden={!open} className="signal-shared-left-out__list">{pagesLabel(pages)} <PageRanges pages={pages} /></span>
  </span>;
}

export function SharedLeftOut({ coverage, all, originalityNotCompared }: {
  coverage: PublicCoverageOut;
  /** Every check's record, to say when they all left out the same pages. */
  all: PublicCoverageOut[];
  /** Originality compared no text at all; its own block says why. */
  originalityNotCompared: boolean;
}) {
  const why = originalityNotCompared ? undefined : coverage.coverage_reason ? coverageCopy(coverage.coverage_reason) : undefined;
  const notRead = coverage.n_pages_not_read > 0 || coverage.pages_not_read.length > 0;
  const notCompared = coverage.pages_not_compared.length > 0 || Boolean(coverage.not_compared_from);
  const counts = COUNTS.filter(([field]) => coverage[field] > 0);
  const listed = all.filter(item => item.pages_not_read.length > 0);
  const samePages = listed.length > 1 && coverage.pages_not_read.length > 0
    && listed.every(item => item.pages_not_read.join() === coverage.pages_not_read.join());
  const headingId = `shared-left-out-heading-${coverage.check_kind}`;
  return <div className="signal-shared-module-status__left-out" role="group" aria-labelledby={headingId}>
    <h4 id={headingId} className="signal-shared-module-status__subhead">Not covered by this check</h4>
    {why || notRead || notCompared || counts.length
      ? <dl className="signal-fact-rows signal-shared-left-out">
        {why && <div><dt>Why</dt><dd>{why}</dd></div>}
        {notRead && <div><dt>Pages not read</dt><dd>
          <PageList id={`shared-left-out-pages-${coverage.check_kind}`} pages={coverage.pages_not_read} count={coverage.n_pages_not_read} />
          {samePages && <span className="signal-field-hint">The same pages were left out by every check.</span>}
          {originalityNotCompared && <span className="signal-field-hint">None of the text was compared, so these pages did not change this result.</span>}
        </dd></div>}
        {notCompared && <div><dt>Pages not compared</dt><dd>{coverage.pages_not_compared.length
          ? <span>{pagesLabel(coverage.pages_not_compared)} <PageRanges pages={coverage.pages_not_compared} /></span>
          : `From ${coverage.not_compared_from} to the end`}</dd></div>}
        {counts.map(([field, label]) => <div key={field}><dt>{label}</dt><dd className="signal-shared-left-out__count">{coverage[field]}</dd></div>)}
      </dl>
      : <p>This link does not record which parts this check left out.</p>}
    <p className="signal-field-hint">Nothing listed here was checked. It is not treated as passed, and it is not a finding.</p>
  </div>;
}
