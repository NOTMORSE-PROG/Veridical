// V-066 (screen 4w): the content half of a single library record --
// reused component-for-component by both Detail (one column) and Compare
// (two columns), per `ui-designer`'s spec §6/§8. Three modes, decided by
// data the caller already has (`isOwn`) or that each fetch honestly
// reports (`available`/`purged_at`), never guessed:
//
// - own, not purged: the SAME `PdfPane`/`DocxPane` V-065's report viewer
//   uses, pointed at the manuscript-scoped (not check-run-scoped) document
//   endpoints, with no flags overlay -- a library record isn't a check run.
// - not own, not purged: the bounded excerpt (Q2's ruling) -- never the
//   full document, never a live read of the other account's file.
// - purged (either ownership): one honest notice, no content pane at all.
import { BASE_URL } from "../api/client";
import { PassageBlock } from "../document/PassagePairPanel";
import { PdfPane } from "../document/PdfPane";
import { DocxPane } from "../document/DocxPane";
import { useLibraryDocument, useLibraryExcerpt, useLibraryParagraphs } from "./useLibrary";

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

function OwnDocumentPane({ manuscriptId }: { manuscriptId: number }) {
  const { data: viewer, isPending, isError, refetch } = useLibraryDocument(manuscriptId, true);
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
        <button type="button" onClick={() => refetch()} className="mt-2 text-sm font-medium text-link underline">
          Try again
        </button>
      </div>
    );
  }
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
        paragraphs={paragraphsData?.paragraphs}
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

function ExcerptPane({ manuscriptId }: { manuscriptId: number }) {
  const { data, isPending, isError, refetch } = useLibraryExcerpt(manuscriptId, true);

  if (isPending) return <SpinnerNote>Loading excerpt.</SpinnerNote>;
  if (isError) {
    return (
      <div className="p-4">
        <ErrorNote>This excerpt couldn't be loaded.</ErrorNote>
        <button type="button" onClick={() => refetch()} className="mt-2 text-sm font-medium text-link underline">
          Try again
        </button>
      </div>
    );
  }
  if (data.purged_at) return <PurgedNotice text={data.limitations} />;

  return (
    // newcomer finding (V-066 review, 2026-08-23): this pane used to have
    // no explicit height of its own, so `overflow-y-auto` never actually
    // engaged -- it just grew to its full content height inside Compare's
    // two-column grid, which is what produced the overlap (see
    // LibraryCompare.tsx's own fix note). `h-full min-h-0` gives it the
    // bounded height its `overflow-y-auto` needs to mean anything.
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto p-4">
      <p className="rounded-md bg-status-neutral-bg px-3 py-2 text-sm text-status-neutral-text">
        This manuscript belongs to another instructor's account. VERIDICAL shows a bounded excerpt
        from each chapter, not the full document, to protect that instructor's manuscript. This is
        the same limit VERIDICAL applies everywhere it compares your manuscript against the shared
        library.
      </p>
      {data.chapters.length === 0 && (
        <p className="text-sm text-ink-secondary">No chapter excerpt is available for this manuscript.</p>
      )}
      {data.chapters.map((chapter) => (
        <div key={chapter.chapter_index} className="flex flex-col gap-1.5">
          <h3 className="text-sm font-bold text-ink">{chapter.title}</h3>
          {chapter.excerpt ? (
            <>
              <PassageBlock
                label="Stored excerpt"
                before={chapter.context_before}
                excerpt={chapter.excerpt}
                after={chapter.context_after}
              />
              <p className="text-xs text-ink-tertiary">Stored excerpt, not the full document.</p>
            </>
          ) : (
            <p className="text-xs text-ink-tertiary">No excerpt available for this chapter.</p>
          )}
        </div>
      ))}
      <p className="text-xs text-ink-tertiary">{data.limitations}</p>
    </div>
  );
}

export function LibraryContentPane({ manuscriptId, isOwn }: { manuscriptId: number; isOwn: boolean }) {
  return isOwn ? (
    <OwnDocumentPane manuscriptId={manuscriptId} />
  ) : (
    <ExcerptPane manuscriptId={manuscriptId} />
  );
}
