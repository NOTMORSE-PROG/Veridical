// V-079: one full PDF/DOCX reader for all authorized Library records.
// Authentication and record visibility are checked by the Library API.
import { BASE_URL } from "../api/client";
import type { ManuscriptViewerOut } from "../api/types";
import { PdfPane } from "../document/PdfPane";
import { DocxPane } from "../document/DocxPane";
import { useLibraryParagraphs } from "./useLibrary";

export interface LibraryDocumentState {
  viewer: ManuscriptViewerOut | undefined;
  isPending: boolean;
  isError: boolean;
  removedAt: string | null;
  onRetry: () => void;
}

function SpinnerNote({ children }: { children: string }) {
  return (
    <p role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-secondary">
      {children}
    </p>
  );
}

function ErrorNote({ children }: { children: string }) {
  return (
    <p role="alert" className="p-4 text-sm text-status-attention-text">
      {children}
    </p>
  );
}

function PurgedNotice({ text }: { text: string }) {
  return (
    <div className="p-6">
      <p className="rounded-lg bg-status-neutral-bg p-4 text-sm text-status-neutral-text">{text}</p>
    </div>
  );
}

function FullDocumentPane({
  manuscriptId,
  documentState,
}: {
  manuscriptId: number;
  documentState: LibraryDocumentState;
}) {
  const { viewer, isPending, isError, onRetry } = documentState;
  const isDocx = viewer?.available === true && viewer.source_format === "docx";
  const {
    data: paragraphsData,
    isPending: paragraphsPending,
    isError: paragraphsError,
    refetch: refetchParagraphs,
  } = useLibraryParagraphs(manuscriptId, isDocx);

  if (isPending) return <SpinnerNote>Loading manuscript.</SpinnerNote>;
  if (isError) {
    return (
      <div className="p-4">
        <ErrorNote>This manuscript couldn't be loaded.</ErrorNote>
        <button type="button" onClick={onRetry} className="mt-2 text-sm font-medium text-link underline">
          Try again
        </button>
      </div>
    );
  }
  if (!viewer) return <ErrorNote>This manuscript's availability could not be checked.</ErrorNote>;
  if (!viewer.available) return <PurgedNotice text={viewer.unavailable_reason ?? "This manuscript can't be viewed."} />;

  if (viewer.source_format === "pdf") {
    return (
      <PdfPane
        fileUrl={`${BASE_URL}/library/${manuscriptId}/document/file`}
        regions={[]}
        flags={[]}
        selectedFlagId={null}
        onSelectFlag={() => {}}
        requestedPage={null}
      />
    );
  }
  if (viewer.source_format === "docx") {
    return (
      <DocxPane
        key={manuscriptId}
        paragraphs={paragraphsData?.paragraphs}
        tables={paragraphsData?.tables}
        images={paragraphsData?.images}
        omittedImageOccurrences={paragraphsData?.omitted_image_occurrences}
        imageBasePath={`/library/${manuscriptId}/document/images`}
        paragraphsPending={paragraphsPending}
        paragraphsError={paragraphsError}
        onRetry={() => refetchParagraphs()}
        regions={[]}
        flags={[]}
        selectedFlagId={null}
        onSelectFlag={() => {}}
        isVisible={true}
      />
    );
  }
  return <PurgedNotice text="This manuscript's source file format isn't supported for viewing." />;
}

export function LibraryContentPane({
  manuscriptId,
  documentState,
}: {
  manuscriptId: number;
  documentState: LibraryDocumentState;
}) {
  if (documentState.removedAt) {
    return <PurgedNotice text="This manuscript is no longer stored in the Library." />;
  }
  return <FullDocumentPane manuscriptId={manuscriptId} documentState={documentState} />;
}
