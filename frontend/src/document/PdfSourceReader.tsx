import { useCallback, useLayoutEffect, useRef, useState, type ComponentProps } from "react";
import { useLocation, useSearchParams } from "react-router";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { PdfPageTextPane } from "./PdfPageTextPane";
import { PdfPane } from "./PdfPane";
import { clearPdfReading, positivePage } from "./usePdfPageText";

export function PdfSourceReader({ run, manuscript, actor, visible, ...pdfProps }: ComponentProps<typeof PdfPane> & {
  run: number; manuscript: number; actor: number | undefined; visible: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const textMode = params.get("source") === "page_text";
  const textPage = positivePage(params.get("text_page"));
  const browsingPage = positivePage(params.get("page"));
  const [observed, setObserved] = useState<number | null>(null);
  const [textAttempt, setTextAttempt] = useState(0);
  const continuation = useRef<FocusContinuation | null>(null);
  const destination = useRef<"text" | "pdf" | null>(null);
  const intendedSearch = useRef<string | null>(null);
  const search = params.toString();
  useLayoutEffect(() => {
    if (continuation.current && intendedSearch.current !== search) {
      continuation.current.dispose(); continuation.current = null; destination.current = null;
    }
  }, [search]);
  useLayoutEffect(() => {
    if (!visible) { continuation.current?.dispose(); continuation.current = null; destination.current = null; }
    return () => { continuation.current?.dispose(); continuation.current = null; destination.current = null; };
  }, [run, actor, visible]);
  const onTextHeading = useCallback((element: HTMLElement) => {
    if (!visible || destination.current !== "text") return;
    continuation.current?.focus(element, { preventScroll: true });
    continuation.current = null; destination.current = null;
  }, [visible]);
  const onPdfPage = useCallback((_page: number, element: HTMLElement) => {
    if (!visible || destination.current !== "pdf") return;
    continuation.current?.focus(element, { preventScroll: true });
    continuation.current = null; destination.current = null;
  }, [visible]);
  function change(mode: "text" | "pdf", page: number | null) {
    setObserved(null);
    continuation.current?.dispose();
    continuation.current = visible ? claimFocusContinuation() : null;
    destination.current = mode;
    const next = new URLSearchParams(params);
    clearPdfReading(next);
    next.delete("pane");
    if (mode === "text") {
      next.set("source", "page_text");
      if (page !== null) next.set("text_page", String(page));
    } else if (page !== null) next.set("page", String(page));
    intendedSearch.current = next.toString();
    if (mode === "text") setTextAttempt((attempt) => attempt + 1);
    setParams(next, { state: location.state });
  }
  return textMode ? <PdfPageTextPane run={run} manuscript={manuscript} actor={actor} page={textPage} visible={visible}
    selectedPage={pdfProps.requestedPage} exactSelection={pdfProps.regions.some((region) => region.flag_id === pdfProps.selectedFlagId && region.kind === "page_bbox")}
    attempt={textAttempt} onPage={(page) => change("text", page)} onOriginal={(page) => change("pdf", page)} onHeading={onTextHeading} />
    : <PdfPane {...pdfProps} requestedPage={browsingPage ?? pdfProps.requestedPage}
      focusSelection={pdfProps.focusSelection && browsingPage === null}
      onObservedPage={setObserved} onPlaceRequestedPage={onPdfPage}
      onReadText={() => change("text", observed ?? browsingPage ?? pdfProps.requestedPage ?? 1)} />;
}
