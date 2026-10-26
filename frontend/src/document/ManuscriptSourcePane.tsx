// F8, screens 4i/4w: one source presentation shared by evidence and manuscript review.
import { useLayoutEffect, useState, type ComponentProps, type ReactNode } from "react";
import type { FlagOut, FlagRegionOut, FlagSummaryOut } from "../api/types";
import { BASE_URL } from "../api/client";
import { useMe } from "../auth/useAuth";
import { localDate } from "../domain/localDate";
import { useFlags, useManuscriptParagraphs, useManuscriptViewer } from "../report/useReport";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { DocxPane } from "./DocxPane";
import { PdfSourceReader } from "./PdfSourceReader";
import { findingNumberByFlagId, buildReviewFindings } from "./reviewFindings";
import { comparisonLeadCollection } from "../report/comparisonLeadCollection";
import { regionPrecision } from "./regionCopy";
import type { AgreementStatementRole } from "../flags/AgreementComparison";

type ViewerRead = Pick<ReturnType<typeof useManuscriptViewer>, "data" | "isPending" | "isError" | "refetch">;
type ParagraphRead = Pick<ReturnType<typeof useManuscriptParagraphs>, "data" | "isPending" | "isError" | "refetch">;
type SourceProps = Pick<ComponentProps<typeof PdfSourceReader>, "regions" | "flags" | "selectedFlagId" | "onSelectFlag" | "requestedPage" | "requestedPageAttempt" | "findingNumbers" | "comparisonLeads" | "focusSelection" | "presentation"> & {
  run: number;
  actor: number | undefined;
  visible: boolean;
  viewerQuery: ViewerRead;
  paragraphsQuery: ParagraphRead;
  requestedParagraph?: number | null;
  onPlaceRequestedParagraph?: ComponentProps<typeof DocxPane>["onPlaceRequestedParagraph"];
  requestedParagraphAttempt?: number;
  scrollInParent?: boolean;
};

function SourceState({ children, filename }: { children: ReactNode; filename?: string | null }) {
  return <div className="signal-document-source">
    <div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>{filename ?? "Stored source"}</span></div></div>
    <div className="signal-document-scroll"><section className="signal-document-paper-state">{children}</section></div>
  </div>;
}

export function ManuscriptSourcePane({ run, actor, visible, viewerQuery, paragraphsQuery, requestedParagraph = null, onPlaceRequestedParagraph, requestedParagraphAttempt, scrollInParent = false, ...selection }: SourceProps) {
  const viewer = viewerQuery.data;
  if (viewerQuery.isPending) return <SourceState><section role="status" aria-live="polite" aria-busy="true" className="signal-document-pdf-skeleton"><span>Loading full manuscript.</span><i /><i /><i /></section></SourceState>;
  if (viewerQuery.isError || !viewer) return <SourceState><Alert title="The manuscript could not be loaded" tone="error" role="alert"><p>VERIDICAL could not load the stored source view. The Review pane loads separately and may also need to be retried.</p><Button variant="secondary" onClick={() => { void viewerQuery.refetch(); }}>Try source again</Button></Alert></SourceState>;
  if (!viewer.available) return <SourceState filename={viewer.original_filename}><div className="signal-document-unavailable"><h2>Source manuscript unavailable</h2><p>{viewer.purged_at ? `The stored source file was deleted on ${localDate(viewer.purged_at)} and can no longer be opened.` : viewer.unavailable_reason}</p><p>You can still review the recorded evidence and readiness details, but VERIDICAL cannot show the full manuscript.</p><ActionLink to={`/report/${run}`} variant="secondary">Open full readiness report</ActionLink></div></SourceState>;
  if (viewer.source_format === "pdf") return <PdfSourceReader key={`${actor ?? "unknown"}:${run}`} run={run} manuscript={viewer.manuscript_id} actor={actor} visible={visible} fileUrl={`${BASE_URL}/check-runs/${run}/document/file`} {...selection} />;
  if (viewer.source_format === "docx") return <DocxPane key={run} paragraphs={paragraphsQuery.data?.paragraphs} tables={paragraphsQuery.data?.tables} images={paragraphsQuery.data?.images} omittedImageOccurrences={paragraphsQuery.data?.omitted_image_occurrences} nativeMath={paragraphsQuery.data?.native_math} imageBasePath={`/check-runs/${run}/document/images`} paragraphsPending={paragraphsQuery.isPending} paragraphsError={paragraphsQuery.isError} onRetry={() => { void paragraphsQuery.refetch(); }} regions={selection.regions} flags={selection.flags} selectedFlagId={selection.selectedFlagId} onSelectFlag={selection.onSelectFlag} isVisible={visible} focusSelection={selection.focusSelection} findingNumbers={selection.findingNumbers} comparisonLeads={selection.comparisonLeads} requestedParagraph={requestedParagraph} onPlaceRequestedParagraph={onPlaceRequestedParagraph} requestedParagraphAttempt={requestedParagraphAttempt} scrollInParent={scrollInParent} />;
  return <SourceState filename={viewer.original_filename}><Alert title="This manuscript format cannot be displayed here" tone="warning"><p>Review the saved evidence and source locations, or open the full readiness report.</p><ActionLink to={`/report/${run}`} variant="secondary">Open full readiness report</ActionLink></Alert></SourceState>;
}

/** A saved anchor is a coarse reading target, never new passage geometry. */
function savedSourceTarget(anchor: string | null): { page: number | null; paragraph: number | null } {
  const page = anchor?.trim().match(/^(?:page|p\.)\s*(\d+)$/i);
  const paragraph = anchor?.trim().match(/^(?:¶|\u00c2¶|paragraph\s+)(\d+)$/i);
  const parsedPage = page ? Number(page[1]) : null;
  const parsedParagraph = paragraph ? Number(paragraph[1]) : null;
  return {
    page: parsedPage !== null && Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : null,
    paragraph: parsedParagraph !== null && Number.isSafeInteger(parsedParagraph) ? parsedParagraph : null,
  };
}

export function OwnedManuscriptSourcePane({ flag, visible = true, anchor, attempt, selectedStatement, onSelectFlag }: {
  flag: FlagOut; visible?: boolean; anchor?: string | null; attempt?: number; selectedStatement?: AgreementStatementRole; onSelectFlag: (id: number) => void;
}) {
  const viewerQuery = useManuscriptViewer(flag.check_run_id);
  const paragraphsQuery = useManuscriptParagraphs(flag.check_run_id, viewerQuery.data?.available === true && viewerQuery.data.source_format === "docx");
  const flagsQuery = useFlags(flag.check_run_id);
  const { data: actor } = useMe();
  const [params, setParams] = useState(() => new URLSearchParams());
  const viewer = viewerQuery.data;
  const ordinary = viewer?.regions.find(region => region.flag_id === flag.id) ?? null;
  const hasCommand = anchor !== undefined;
  const target = savedSourceTarget(anchor ?? null);
  const coarse: FlagRegionOut | null = !hasCommand ? null : viewer?.source_format === "pdf" && target.page !== null && (viewer.page_count === null || target.page <= viewer.page_count)
    ? { flag_id: flag.id, kind: "page_only", page: target.page, end_page: null, bbox: null, all_bboxes: [], paragraph: null, index: null }
    : viewer?.source_format === "docx" && target.paragraph !== null && [
      ...(paragraphsQuery.data?.paragraphs ?? []), ...(paragraphsQuery.data?.tables ?? []), ...(paragraphsQuery.data?.images ?? []), ...(paragraphsQuery.data?.native_math?.containers ?? []),
    ].some(item => item.paragraph === target.paragraph)
      ? { flag_id: flag.id, kind: "paragraph_only", page: null, end_page: null, bbox: null, all_bboxes: [], paragraph: target.paragraph, index: null } : null;
  const selected = hasCommand ? coarse : ordinary;
  const sourceTargetReady = !viewerQuery.isPending && (viewer?.source_format !== "docx" || !paragraphsQuery.isPending);
  useLayoutEffect(() => {
    if (attempt === undefined) return;
    setParams(current => {
      const next = new URLSearchParams(current);
      next.delete("page");
      if (next.get("source") === "page_text" && selected?.page) next.set("text_page", String(selected.page));
      else next.delete("text_page");
      return next.toString() === current.toString() ? current : next;
    });
  }, [attempt, selected?.page]);
  const precision = regionPrecision(selected, viewer?.available === false ? viewer.purged_at ? "deleted" : "unavailable" : undefined);
  const flags: FlagSummaryOut[] = flagsQuery.data ?? [{ ...flag, problem_kind: null }];
  const comparison = comparisonLeadCollection(flag.check_run_id, flagsQuery);
  return <div className="signal-owned-source">
    {!flagsQuery.data && !flagsQuery.isPending && <p role="status">Finding navigation could not be loaded. Showing the saved location for Record #{flag.id} without other finding markers. <Button variant="quiet" onClick={() => { void flagsQuery.refetch(); }}>Retry finding navigation</Button></p>}
    <p className="signal-document-precision-note" role="status">{selectedStatement && <strong>{selectedStatement === "objective" ? "Statement A selected. " : selectedStatement === "result" ? "Statement B selected. " : "Recorded statement selected. "}</strong>}<strong>{precision.label}.</strong> {precision.helper}{hasCommand && !coarse && viewer?.available && sourceTargetReady && " The selected statement’s recorded location could not be placed. Inspect the manuscript manually."}</p>
    <ManuscriptSourcePane run={flag.check_run_id} actor={actor?.id} visible={visible} viewerQuery={viewerQuery} paragraphsQuery={paragraphsQuery}
      regions={hasCommand || !flagsQuery.data ? [] : viewer?.regions ?? []} flags={flags} selectedFlagId={selected ? flag.id : null} onSelectFlag={onSelectFlag}
      requestedPage={selected?.page ?? null} requestedPageAttempt={attempt} requestedParagraph={selected?.paragraph ?? null} requestedParagraphAttempt={attempt} focusSelection={false}
      findingNumbers={findingNumberByFlagId(buildReviewFindings(flags))} comparisonLeads={comparison.snapshot}
      presentation={{ params, change: setParams }} />
  </div>;
}
