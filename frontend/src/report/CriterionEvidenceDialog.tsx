import { useLayoutEffect, useRef, useState } from "react";
import type { EvidenceItem, ReportOut } from "../api/types";
import { BASE_URL } from "../api/client";
import { CRITERION_ANCHOR_REGION_ID } from "../config/ui";
import { criterionSource, evidenceRegion } from "../demo/sourceEvidence";
import { isWorkspaceSample } from "../demo/workspaceFixture";
import { criterionAnchorRegion } from "../document/criterionAnchor";
import { DocxPane } from "../document/DocxPane";
import { PdfPane } from "../document/PdfPane";
import { regionPrecision } from "../document/regionCopy";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useManuscriptParagraphs, useManuscriptViewer } from "./useReport";

export interface CriterionEvidenceSelection { criterionId: number; evidence: EvidenceItem; index: number }

export function CriterionEvidenceDialog({ report, selection, onClose }: {
  report: ReportOut; selection: CriterionEvidenceSelection; onClose: () => void;
}) {
  const source = useManuscriptViewer(report.check_run_id);
  const [readerAttempt, setReaderAttempt] = useState(0);
  const viewer = source.data;
  const paragraphs = useManuscriptParagraphs(report.check_run_id, viewer?.available === true && viewer.source_format === "docx");
  const criterion = report.results.find(row => row.criterion_id === selection.criterionId);
  const recorded = criterion?.evidence[selection.index];
  const matches = recorded?.quote === selection.evidence.quote && recorded?.anchor === selection.evidence.anchor;
  const sameSource = viewer?.available === true && viewer.manuscript_id === report.manuscript_id;
  const anchor = criterionAnchorRegion(selection.evidence.anchor);
  const coarse = matches && sameSource && anchor && (anchor.page !== null
    ? viewer.page_count !== null && anchor.page <= viewer.page_count
    : paragraphs.data?.paragraphs.some(item => item.paragraph === anchor.paragraph)) ? anchor : null;
  const saved = isWorkspaceSample() && matches ? criterionSource(selection.criterionId, selection.evidence) : undefined;
  const region = saved && sameSource ? evidenceRegion(saved, {
    run: report.check_run_id, manuscript: viewer.manuscript_id, sourceHash: viewer.source_sha256,
    kind: "criterion", subjectId: selection.criterionId, role: "criterion_evidence", index: selection.index,
    quote: selection.evidence.quote, anchor: selection.evidence.anchor,
  }, CRITERION_ANCHOR_REGION_ID) : isWorkspaceSample() ? null : coarse;
  const precision = regionPrecision(region);
  const reader = useRef<HTMLDivElement>(null);
  const revealed = useRef(false);
  const placed = region !== null;
  useLayoutEffect(() => {
    if (placed && reader.current && !revealed.current) {
      revealed.current = true;
      reader.current.scrollIntoView({ block: "nearest" });
    }
  }, [placed]);
  return <Dialog title="Original manuscript passage" wide onClose={onClose} actions={<>{region && <Button variant="quiet" onClick={() => setReaderAttempt(value => value + 1)}>Reload manuscript</Button>}<Button variant="secondary" onClick={onClose}>Close manuscript</Button></>}>
    <div className="signal-criterion-source-dialog" data-workspace-origin="review">
      <h3>{criterion?.text ?? "Selected criterion"}</h3>
      <blockquote><p>“{selection.evidence.quote}”</p><cite>{selection.evidence.anchor}</cite></blockquote>
      <p className="signal-field-hint">Your grade and comment stay in the review form. Close this manuscript to continue grading.</p>
      {source.isPending ? <p role="status">Loading the original manuscript…</p>
        : source.isError ? <Alert title="Could not load the manuscript" tone="error"><Button variant="secondary" onClick={() => source.refetch()}>Try manuscript again</Button></Alert>
          : sameSource && viewer.source_format === "docx" && paragraphs.isPending ? <p role="status">Loading the original manuscript text…</p>
            : sameSource && viewer.source_format === "docx" && paragraphs.isError ? <Alert title="Could not load the manuscript text" tone="error"><Button variant="secondary" onClick={() => paragraphs.refetch()}>Try manuscript text again</Button></Alert>
          : !region || !viewer ? <Alert title="This passage could not be placed" tone="info">The saved quotation stays above. {viewer?.unavailable_reason ?? "A matching source location could not be verified. No unrelated page is shown."}</Alert>
            : <><p><strong>{precision.label}.</strong> {precision.helper}</p><div ref={reader} key={readerAttempt} className="signal-criterion-source-reader">
              {viewer.source_format === "pdf" ? <PdfPane fileUrl={`${BASE_URL}/check-runs/${report.check_run_id}/document/file`} regions={[region]} flags={[]} selectedFlagId={region.flag_id} onSelectFlag={() => {}} requestedPage={region.page} focusSelection={false} isVisible />
                : viewer.source_format === "docx" ? <DocxPane paragraphs={paragraphs.data?.paragraphs} paragraphsPending={paragraphs.isPending} paragraphsError={paragraphs.isError} onRetry={() => paragraphs.refetch()} regions={[region]} flags={[]} selectedFlagId={region.flag_id} onSelectFlag={() => {}} requestedParagraph={region.paragraph} isVisible />
                  : <p>This stored format cannot be displayed here.</p>}
            </div></>}
    </div>
  </Dialog>;
}
