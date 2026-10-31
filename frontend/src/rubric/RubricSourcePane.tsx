import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api, BASE_URL } from "../api/client";
import type { DocumentParagraphsOut, Rubric } from "../api/types";
import { useMe } from "../auth/useAuth";
import { DocxPane } from "../document/DocxPane";
import { Button } from "../ui/Button";
import type { SourceTarget } from "./sourcePage";

const TARGET_HEADINGS: Record<SourceTarget["kind"], string> = {
  cell: "Find this cell",
  row: "Find this row",
  compare: "Compare this wording",
  format: "Compare the criteria with this page",
};

const PdfPane = lazy(() => import("../document/PdfPane").then((module) => ({ default: module.PdfPane })));

type FormatVersion = Pick<Rubric, "id" | "version" | "rubric_family_id">;

interface FormatDocument {
  rubric_id: number;
  rubric_family_id: string;
  version: number;
  available: boolean;
  source_format: "pdf" | "docx" | null;
  source_sha256: string | null;
  unavailable_reason: "not_available_locally" | null;
  reconstruction: "fresh_local_source" | "original_pdf" | null;
}

function documentMatches(value: unknown, rubric: FormatVersion): value is FormatDocument {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<FormatDocument>;
  if (row.rubric_id !== rubric.id || row.rubric_family_id !== rubric.rubric_family_id || row.version !== rubric.version) return false;
  return row.available === false
    ? row.unavailable_reason === "not_available_locally" && row.source_format === null && row.source_sha256 === null
    : row.available === true && typeof row.source_sha256 === "string" && /^[0-9a-f]{64}$/.test(row.source_sha256)
      && ((row.source_format === "docx" && row.reconstruction === "fresh_local_source")
        || (row.source_format === "pdf" && row.reconstruction === "original_pdf"));
}

export function RubricSourcePane({ rubric, isVisible, onUploadNewVersion, requestedPage = null, requestedPageAttempt, requestedTarget = null, focusTarget = false, onPagesAvailable }: {
  rubric: FormatVersion;
  isVisible: boolean;
  onUploadNewVersion: () => void;
  requestedPage?: number | null;
  requestedPageAttempt?: number;
  // What to find on the requested page, shown above it.
  requestedTarget?: SourceTarget | null;
  // In tabs the original replaces the criteria, so focus moves to the target.
  focusTarget?: boolean;
  // True for a stored PDF original, false when there are no pages to show,
  // null while that is not known yet.
  onPagesAvailable?: (available: boolean | null) => void;
}) {
  const { data: instructor } = useMe();
  const owner = instructor?.id ?? null;
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  const lifetime = useRef(0);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  const targetRef = useRef<HTMLDivElement>(null);
  const document = useQuery({
    queryKey: ["rubric-document", owner, rubric.id, rubric.rubric_family_id, rubric.version],
    enabled: owner !== null && isVisible,
    queryFn: async ({ signal }) => {
      const value = await api.get<unknown>(`/rubrics/${rubric.id}/document`, { signal });
      if (!documentMatches(value, rubric)) throw new Error("The source response did not identify this format version.");
      return value;
    },
    retry: false,
    // A version's original is immutable through the application. Keep its
    // verified reading identity while moving between the two review panes.
    staleTime: Infinity,
  });
  const metadata = document.data;
  const pagesAvailable = document.isError ? false
    : !metadata ? null
      : metadata.available && metadata.source_format === "pdf";
  useEffect(() => { onPagesAvailable?.(pagesAvailable); }, [onPagesAvailable, pagesAvailable]);
  const targetShown = Boolean(requestedTarget && requestedPage !== null && metadata?.available && metadata.source_format === "pdf");
  // The target appears once the original has loaded, which can be after the
  // pane opened; bring it into view then.
  useEffect(() => {
    if (!targetShown || !targetRef.current) return;
    targetRef.current.scrollIntoView({ block: "nearest" });
    if (focusTarget) targetRef.current.focus({ preventScroll: true });
  }, [targetShown, requestedPageAttempt, focusTarget]);
  const hash = metadata?.available ? metadata.source_sha256 : null;
  const downloadIdentity = `${owner}:${rubric.id}:${rubric.version}:${hash ?? ""}`;
  const identityRef = useRef(downloadIdentity);
  const sourceReads = useRef<Promise<void>>(Promise.resolve());
  if (identityRef.current !== downloadIdentity) {
    identityRef.current = downloadIdentity;
    lifetime.current += 1;
    sourceReads.current = Promise.resolve();
  }
  useEffect(() => () => { lifetime.current += 1; }, []);
  useEffect(() => {
    setDownloading(false);
    setDownloadError(false);
  }, [downloadIdentity]);
  const readSource = useCallback(<T,>(operation: () => Promise<T>) => {
    const identity = downloadIdentity;
    const generation = lifetime.current;
    const stillCurrent = () => identityRef.current === identity && lifetime.current === generation;
    // These endpoints share bounded preparation admission. Keep this pane's
    // preview, image and original-file requests from competing with each other.
    const read = sourceReads.current.then(async () => {
      if (!stillCurrent()) throw new Error("The format reading context changed.");
      const value = await operation();
      if (!stillCurrent()) throw new Error("The format reading context changed.");
      return value;
    });
    sourceReads.current = read.then(() => undefined, () => undefined);
    return read;
  }, [downloadIdentity]);
  const readSourceBlob = useCallback((path: string) => readSource(() => api.getBlob(path)), [readSource]);
  const sourceQuery = hash ? new URLSearchParams({ source_sha256: hash }).toString() : "";
  const paragraphs = useQuery({
    queryKey: ["rubric-source-paragraphs", owner, rubric.id, rubric.version, hash],
    enabled: owner !== null && isVisible && !document.isFetching && !document.isError
      && metadata?.available === true && metadata.source_format === "docx",
    queryFn: async ({ signal }) => {
      const value = await readSource(() => api.get<unknown>(`/rubrics/${rubric.id}/document/paragraphs?${sourceQuery}`, { signal }));
      if (!documentMatches(value, rubric) || value.source_sha256 !== hash) throw new Error("The reading view did not match this source.");
      const content = (value as FormatDocument & { content?: DocumentParagraphsOut }).content;
      if (!content || !Array.isArray(content.paragraphs) || !Array.isArray(content.tables) || !Array.isArray(content.images)) throw new Error("The reading view could not be read.");
      return content;
    },
    retry: false,
    staleTime: Infinity,
  });
  const loadPdfSource = useCallback(async () => {
    const blob = await readSourceBlob(`/rubrics/${rubric.id}/document/file?${sourceQuery}`);
    if (blob.type !== "application/pdf") throw new Error("The original PDF could not be read.");
    return new Uint8Array(await blob.arrayBuffer());
  }, [readSourceBlob, rubric.id, sourceQuery]);

  async function downloadOriginal() {
    if (!metadata?.available || !hash || owner === null || downloading) return;
    const current = lifetime.current;
    const actor = owner;
    const identity = downloadIdentity;
    setDownloading(true);
    setDownloadError(false);
    try {
      const blob = await readSourceBlob(`/rubrics/${rubric.id}/document/original?${sourceQuery}`);
      if (lifetime.current !== current || ownerRef.current !== actor || identityRef.current !== identity) return;
      const expectedMime = metadata.source_format === "pdf" ? "application/pdf" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
      if (blob.type !== expectedMime) throw new Error("Unexpected original-file response.");
      const url = URL.createObjectURL(blob);
      try {
        const link = window.document.createElement("a");
        link.href = url;
        link.download = `required-format-${rubric.id}-v${rubric.version}.${metadata.source_format}`;
        link.click();
      } finally { URL.revokeObjectURL(url); }
    } catch {
      if (lifetime.current === current && ownerRef.current === actor && identityRef.current === identity) setDownloadError(true);
    } finally {
      if (lifetime.current === current && ownerRef.current === actor && identityRef.current === identity) setDownloading(false);
    }
  }

  if (document.isPending) return <p role="status" aria-busy="true">Loading original format.</p>;
  if (document.isError || !metadata) return <div className="signal-format-source-state" role="alert">
    <h2>The original format could not be opened</h2>
    <p>{document.error instanceof ApiError && document.error.status === 404
      ? "The source for this format version could not be found. Your prepared criteria remain available."
      : "The source reading view is unavailable. Your prepared criteria remain available."}</p>
    <Button variant="secondary" onClick={() => void document.refetch()}>Try again</Button>
  </div>;
  if (!metadata.available) return <div className="signal-format-source-state">
    <h2>The original file is unavailable locally</h2>
    <p>The stored original for this version could not be found. Its prepared criteria remain available.</p>
    <Button variant="secondary" onClick={onUploadNewVersion}>Upload new version</Button>
  </div>;

  return <div className="signal-format-source">
    <div className="signal-format-source__actions">
      <span>Version {rubric.version}</span>
      <Button variant="secondary" busy={downloading} onClick={() => void downloadOriginal()}>Download original</Button>
    </div>
    {downloadError && <p role="alert">The original could not be downloaded. Try again; your criteria have not changed.</p>}
    {metadata.source_format === "pdf" && requestedTarget && requestedPage !== null
      && <div key={requestedPageAttempt} ref={targetRef} className="signal-source-target" tabIndex={-1} role="status">
        <p><strong>{TARGET_HEADINGS[requestedTarget.kind]}</strong>
          {" "}· {requestedTarget.location}</p>
        {requestedTarget.text && <blockquote>{requestedTarget.text}</blockquote>}
      </div>}
    {metadata.source_format === "pdf"
      ? <Suspense fallback={<p role="status">Loading original format.</p>}><PdfPane key={`${owner}:${rubric.id}:${hash}`} documentKind="required_format"
          fileUrl={`${BASE_URL}/rubrics/${rubric.id}/document/file?${sourceQuery}`}
          loadFile={loadPdfSource}
          regions={[]} flags={[]} selectedFlagId={null} onSelectFlag={() => {}} requestedPage={requestedPage}
          requestedPageAttempt={requestedPageAttempt} focusSelection={false} /></Suspense>
      : <DocxPane key={`${owner}:${rubric.id}:${hash}`} documentKind="required_format"
          paragraphs={paragraphs.data?.paragraphs} tables={paragraphs.data?.tables} images={paragraphs.data?.images}
          nativeMath={paragraphs.data?.native_math} omittedImageOccurrences={paragraphs.data?.omitted_image_occurrences}
          paragraphsPending={paragraphs.isPending} paragraphsError={paragraphs.isError}
          onRetry={() => void paragraphs.refetch()} regions={[]} flags={[]} selectedFlagId={null}
          onSelectFlag={() => {}} isVisible={isVisible} focusSelection={false}
          imageBasePath={`/rubrics/${rubric.id}/document/images`} sourceQuery={sourceQuery} loadImage={readSourceBlob} />}
  </div>;
}
