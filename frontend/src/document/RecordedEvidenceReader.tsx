// F8/4w: a content-bound reader; changing a grade retires marks, not the source.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api, BASE_URL } from "../api/client";
import { useAuthOwner } from "../auth/queryOwner";
import type { EvidenceLocation, EvidenceSourceRequest } from "../report/evidenceTypes";
import { useEvidenceDocument, useEvidenceLocation } from "../report/useEvidenceSource";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { DocxPane } from "./DocxPane";
import { PdfPane } from "./PdfPane";

export function RecordedEvidenceReader({ run, request, current, visible, attempt, onPlaced, canPlace, onManual }: {
  run: number;
  request: EvidenceSourceRequest;
  current: boolean;
  visible: boolean;
  attempt: number;
  onPlaced?: (element: HTMLElement) => void;
  canPlace: () => boolean;
  onManual: () => void;
}) {
  const owner = useAuthOwner();
  const mounted = useRef(false);
  useLayoutEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const query = useEvidenceLocation(run, request.snapshotId, request.reference.reference_id, current, attempt);
  const location = current && query.isSuccess && query.fetchStatus === "idle" ? query.data : undefined;
  const [stored, setStored] = useState<{ owner: typeof owner; location: EvidenceLocation }>();
  const retained = stored?.owner.isCurrent() ? stored.location : undefined;
  useEffect(() => {
    if (location?.source_identity && location.source_format && owner.isCurrent()) setStored({ owner, location });
  }, [location, owner]);
  const identity = retained?.source_identity;
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const imageQueue = useRef<Promise<unknown>>(Promise.resolve());
  const imageLoads = useRef(new Set<AbortController>());
  const imageGeneration = useRef(0);
  const boundDocument = useEvidenceDocument(run, identity, retained?.source_format === "docx");
  const loading = useRef<AbortController | undefined>(undefined);
  useEffect(() => {
    const loads = imageLoads.current;
    return () => {
      loading.current?.abort();
      imageGeneration.current += 1;
      loads.forEach(controller => controller.abort());
      loads.clear();
    };
  }, [identity, run, owner]);
  const loadPdf = useCallback(async () => {
    if (!identity || !mounted.current || !owner.isCurrent()) throw new ApiError(409, "conflict", "The source reader changed.");
    loading.current?.abort();
    const controller = new AbortController();
    loading.current = controller;
    const data = await api.getSourceBytes(`/check-runs/${run}/evidence/source-file?source_identity=${identity}`,
      identity, { signal: controller.signal });
    if (!owner.isCurrent() || controller.signal.aborted) throw new ApiError(409, "conflict", "The source reader changed.");
    return data;
  }, [run, identity, owner]);
  const loadImage = useCallback((path: string) => {
    const generation = imageGeneration.current;
    const read = async () => {
      if (!identity || !mounted.current || generation !== imageGeneration.current || currentIdentity.current !== identity || !owner.isCurrent()) throw new ApiError(409, "conflict", "The source reader changed.");
      const controller = new AbortController();
      imageLoads.current.add(controller);
      try {
        const data = await api.getSourceImage(path, identity, { signal: controller.signal });
        if (currentIdentity.current !== identity || !owner.isCurrent() || controller.signal.aborted) throw new ApiError(409, "conflict", "The source reader changed.");
        return data;
      } finally { imageLoads.current.delete(controller); }
    };
    const next = imageQueue.current.then(read, read);
    imageQueue.current = next.catch(() => undefined);
    return next;
  }, [identity, owner]);
  const placed = location?.source_identity === identity ? location?.placement : undefined;
  const onPdfPlaced = useCallback((_page: number, element: HTMLElement) => onPlaced?.(element), [onPlaced]);
  const requesting = current && query.isFetching;
  return <div className="signal-recorded-reader">
    <div className="signal-recorded-reader__context">
      <p role="status">{requesting ? "Locating the recorded passage in its source copy."
        : location ? location.caption : current && query.isError ? "This recorded location could not be verified."
          : "Source marks are cleared. Select a current recorded location to place them again."}</p>
      {current && query.isError && <Button variant="quiet" onClick={() => { void query.refetch(); }}>Retry recorded location</Button>}
      <Button variant="quiet" onClick={onManual}>Continue in full manuscript</Button>
    </div>
    {!retained && !requesting && !query.isError && <p>No source page or paragraph is available for this recorded passage. Read the saved excerpt and inspect the manuscript manually.</p>}
    {retained?.source_format === "pdf" && identity && <PdfPane
      fileUrl={`${BASE_URL}/check-runs/${run}/evidence/source-file?source_identity=${identity}`} loadFile={loadPdf}
      regions={[]} flags={[]} selectedFlagId={null} onSelectFlag={() => {}} focusSelection={false}
      requestedPage={visible ? placed?.page ?? null : null} requestedPageAttempt={attempt}
      onPlaceRequestedPage={visible ? onPdfPlaced : undefined} canPlaceRequestedPage={canPlace} recordedEvidence={placed} />}
    {retained?.source_format === "docx" && identity && <>
      {boundDocument.isError && <Alert title="This extracted source copy could not be verified" tone="warning"><Button variant="quiet" onClick={() => { void boundDocument.refetch(); }}>Retry extracted source</Button></Alert>}
      <DocxPane key={identity} paragraphs={boundDocument.data?.document.paragraphs} tables={boundDocument.data?.document.tables}
        images={boundDocument.data?.document.images} omittedImageOccurrences={boundDocument.data?.document.omitted_image_occurrences}
        nativeMath={boundDocument.data?.document.native_math} imageBasePath={`/check-runs/${run}/evidence/source-images`}
        sourceQuery={`source_identity=${identity}`} loadImage={loadImage}
        paragraphsPending={boundDocument.isPending} paragraphsError={boundDocument.isError} onRetry={() => { void boundDocument.refetch(); }}
        regions={[]} flags={[]} selectedFlagId={null} onSelectFlag={() => {}} isVisible={visible} focusSelection={false}
        requestedParagraph={placed?.paragraph ?? null} requestedParagraphAttempt={attempt}
        onPlaceRequestedParagraph={visible ? onPlaced : undefined} />
    </>}
  </div>;
}
