import { useId, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { positivePage, usePdfPageText, type PdfPageText } from "./usePdfPageText";
import "./PdfPageTextPane.css";

const READING_AID = "Reading aid, not original evidence. Text can be missing or out of order. Graphics, diagrams, tables, formulas and layout may be absent or changed. This view does not run a new assessment. Verify evidence in the original PDF.";

function unavailableCopy(data: PdfPageText) {
  switch (data.reason) {
    case "reading_order_not_recorded": return ["Saved page text is unavailable", "The saved record does not establish a reliable reading order for this PDF page. Inspect the original PDF. This does not mean the page has no text or other content. The recorded assessment has not been changed."];
    case "reading_order_unresolved": return ["Page text cannot be shown reliably", "The saved text for this page could not be put in a reliable reading order. Inspect the original PDF. This does not mean the page has no text or other content. The recorded assessment has not been changed."];
    case "source_binding_unverified": return ["Saved page text could not be verified", "The saved text could not be matched reliably to the original PDF. Use the original PDF if it is available. The recorded assessment has not been changed."];
    case "page_out_of_range": return ["That PDF page is not available", `This PDF has ${data.page_count} pages. Choose a page from 1 to ${data.page_count}.`];
    case "resource_limit": return ["Saved page text is unavailable", "This page could not be verified within the reading-aid limit. Inspect the original PDF. The recorded assessment has not been changed."];
    case "source_cache_unavailable": return ["Stored source is unavailable for Page text", "The local source copy needed for this reading aid is unavailable. Try the original PDF, then try Page text again if the source opens."];
    default: return ["Saved page text is unavailable", "The saved extraction needed for this reading aid is unavailable. Use the original PDF if it is available."];
  }
}

export function PdfPageTextPane({ run, manuscript, actor, page, visible, attempt, selectedPage, exactSelection, onPage, onOriginal, onHeading }: {
  run: number; manuscript: number; actor: number | undefined; page: number | null;
  visible: boolean; selectedPage: number | null; exactSelection: boolean;
  attempt: number;
  onPage: (page: number) => void; onOriginal: (page: number | null) => void;
  onHeading: (element: HTMLElement) => void;
}) {
  const query = usePdfPageText(run, manuscript, actor, page, visible);
  const data = query.error || !query.isSuccess ? undefined : query.data;
  const total = data?.page_count ?? null;
  const [input, setInput] = useState(page === null ? "" : String(page));
  const [invalid, setInvalid] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const errorId = useId();
  const lastPlacement = useRef({ page, attempt });
  useLayoutEffect(() => {
    setInput(page === null ? "" : String(page)); setInvalid(false);
    if (visible && heading.current) onHeading(heading.current);
    if (scroll.current && (lastPlacement.current.page !== page || lastPlacement.current.attempt !== attempt)) scroll.current.scrollTop = 0;
    lastPlacement.current = { page, attempt };
  }, [page, visible, attempt, onHeading]);
  const failure = query.error instanceof ApiError ? query.error : null;
  const privateDenied = failure?.status === 401 || failure?.status === 403 || failure?.status === 404;
  const sourceGone = failure?.status === 410;
  const inputError = total === null ? "Choose a whole PDF page number greater than zero." : `Choose a whole PDF page number from 1 to ${total}.`;
  function commit() {
    const target = positivePage(input);
    if (target === null || (total !== null && target > total)) {
      setInvalid(true); field.current?.focus(); return;
    }
    setInvalid(false); onPage(target);
  }
  const loading = page !== null && query.isPending && !query.error;
  const confirmed = data && total !== null && Number.isSafeInteger(total) && total > 0 && data.reason !== "page_out_of_range";
  const recovery = <Button variant="secondary" onClick={() => onOriginal(confirmed ? page : null)}>View original PDF{confirmed ? ` p. ${page}` : ""}</Button>;
  const copy = data?.state === "unavailable" ? unavailableCopy(data) : null;
  return <div className="signal-document-source signal-document-page-text">
    <div className="signal-document-toolbar">
      <div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>Page text · Reading aid</span></div>
      {!privateDenied && !sourceGone && <div className="signal-document-toolbar__controls" role="group" aria-label="Page text controls">
        <Button variant="quiet" aria-disabled={page === null || page <= 1} onClick={() => { if (page !== null && page > 1) onPage(page - 1); }}>Previous</Button>
        <label>PDF page <input ref={field} type="text" inputMode="numeric" value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commit(); } }} aria-invalid={invalid || undefined} aria-describedby={invalid ? errorId : undefined} /></label>
        <Button variant="quiet" onClick={commit}>Go</Button>
        <Button variant="quiet" aria-disabled={page === null || total === null || page >= total} onClick={() => { if (page !== null && total !== null && page < total) onPage(page + 1); }}>Next</Button>
        <span>{confirmed ? `Page ${page} of ${total}` : page === null ? "Page not selected" : `Requested PDF p. ${page}`}</span>
        {recovery}
      </div>}
    </div>
    {invalid && <p id={errorId} role="alert">{inputError}</p>}
    <div ref={scroll} className="signal-document-page-text__scroll">
      <article aria-busy={loading}>
        <h2 ref={heading} tabIndex={-1}>{data?.state === "available" || data?.state === "no_selectable_text" ? `Extracted text from PDF p. ${page}` : page === null ? "Page text" : `Page text for requested PDF p. ${page}`}</h2>
        <p>{READING_AID}</p>
        {exactSelection && <p>The selected passage mark appears only in the original PDF.</p>}
        {!privateDenied && !sourceGone && <p>The PDF opens at Fit width.</p>}
        {selectedPage !== null && selectedPage !== page && !privateDenied && !sourceGone && <p>Selected source: PDF p. {selectedPage}. Reading text: PDF p. {page ?? "not selected"}. <Button variant="quiet" onClick={() => onOriginal(selectedPage)}>Return to selected PDF p. {selectedPage}</Button></p>}
        <p role="status">{loading ? `Loading saved text for PDF p. ${page}.` : data?.state === "available" || data?.state === "no_selectable_text" ? `Page text loaded for PDF p. ${page}.` : ""}</p>
        {page === null && <Alert title="Choose a PDF page" tone="info"><p>{inputError}</p></Alert>}
        {privateDenied ? <Alert title={failure.status === 401 ? "Your session ended" : "This source is not available"} tone="warning"><p>{failure.status === 401 ? "Sign in again to reopen private manuscript text." : "Return to Review Desk to open a manuscript available to your account."}</p>{failure.status === 401 ? <ActionLink to="/signin">Sign in again</ActionLink> : <ReviewDeskReturnLink>Return to Review Desk</ReviewDeskReturnLink>}</Alert>
          : sourceGone ? <Alert title="Source manuscript was deleted" tone="warning"><p>The stored source was deleted, so Page text and the original PDF cannot be opened. The recorded report can be opened separately.</p><ActionLink to={`/report/${run}`}>Open recorded report</ActionLink></Alert>
            : query.error ? <Alert title={failure?.status === 429 || failure?.status === 503 ? "Page text is busy" : "Page text could not be loaded"} tone="warning"><p>{failure?.status === 429 || failure?.status === 503 ? "The reading aid cannot accept another request right now. Try again shortly, or use the original PDF if it is available." : "Try Page text again, or use the original PDF if it is available."}</p><Button variant="secondary" onClick={() => { void query.refetch(); }}>Try page text again</Button></Alert>
              : copy ? <Alert title={copy[0]} tone="warning"><p>{copy[1]}</p></Alert>
                : data?.state === "no_selectable_text" ? <p>No text was extracted from this PDF page. Inspect the original PDF; it may contain images or other content.</p>
                  : data?.state === "available" && <>
                    {(data.omitted_block_count ?? 0) > 0 && <Alert title="Page text is incomplete" tone="warning"><p>Some saved text blocks were omitted by the reading-aid limit. Use the original PDF for the rest of this page.</p><p>{data.omitted_block_count} saved blocks omitted.</p></Alert>}
                    {data.blocks.length === 0 && <p>No saved text block could be shown within the reading-aid limit. Inspect the original PDF.</p>}
                    {data.blocks.map((block, index) => <p className="signal-document-page-text__block" key={index}>{block.text}</p>)}
                  </>}
        {!privateDenied && !sourceGone && <footer>{recovery}<Button variant="quiet" aria-disabled={page === null || total === null || page >= total} onClick={() => { if (page !== null && total !== null && page < total) onPage(page + 1); }}>Next page</Button></footer>}
      </article>
    </div>
  </div>;
}
