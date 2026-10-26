import { jsPDF } from "jspdf";
import type { BoundSourceEvidence, CriterionResultOut, FlagOut, ReportOut } from "../api/types";
import { DECISION_LABEL } from "../domain/decisionTone";
import { READINESS_LABEL } from "../domain/readinessTone";
import { CHECK_KIND_ORDER, humanize } from "../domain/checkKind";
import { problemLabel } from "../domain/problemLabel";
import { integrityModuleLabel } from "../report/integrityModuleState";
import { evidenceFor, evidenceSourceHash, flagSourcePagePair } from "./sourceEvidence";
import { sampleFlags, sampleReport, WORKSPACE_SAMPLE } from "./workspaceFixture";
import { PLAIN_CRITERION_REASONS, PLAIN_FINDING_REASONS } from "./workspacePdfCopy";
import { AI_REVIEW_BASIS, AI_REVIEW_DESCRIPTION, hasPreparedAiGrades } from "./preparedAiReview";

// Print dimensions are separate from the browser layout. Keep text readable
// instead of scaling the entire interface into a page-sized screenshot.
const PRINT = {
  margin: 18, width: 174, bottom: 276, top: 26, line: 5.1,
  ink: "#242424", muted: "#555555", blue: "#005eb8", border: "#c8c5bb",
  paper: "#f4f2eb", accent: "#ffc400", danger: "#9b2525", success: "#24643a",
} as const;

type Destination = { label: string; page: number; parent?: string };
type PdfInput = { report: ReportOut; flags: FlagOut[]; fileAvailable: boolean; fetchAsset: typeof fetch };

export function pdfGrade(row: CriterionResultOut): string {
  if (row.outcome === "not_applicable") return "Excluded from readiness";
  if (row.outcome !== "passed" && row.outcome !== "failed") return "Not graded";
  if (row.level) return row.level.name;
  if (row.outcome === "passed") return "Meets the criterion";
  if (row.outcome === "failed") return "Does not meet the criterion";
  return "Not graded";
}

export function pdfFindingState(flag: FlagOut): string {
  return flag.confirmed_citation_source ? "Source confirmed by teacher" : flag.overridden ? "Dismissed by teacher" : "Open - needs teacher review";
}

export function pdfCoverSummary(report: Pick<ReportOut, "decision" | "status" | "results">) {
  if (report.decision) return { label: "INSTRUCTOR DECISION", value: DECISION_LABEL[report.decision] };
  if (hasPreparedAiGrades(report)) return { label: "READINESS RESULT", value: READINESS_LABEL[report.status] };
  return { label: "INSTRUCTOR DECISION", value: "No decision recorded" };
}

export async function buildWorkspacePdf({ report, flags, fileAvailable, fetchAsset }: PdfInput): Promise<Blob> {
  if (report.check_run_id !== sampleReport.check_run_id || report.manuscript_id !== sampleReport.manuscript_id || evidenceSourceHash !== WORKSPACE_SAMPLE.sha256) throw new Error("The prepared source does not match this report.");
  if (report.llm_mode !== "manual" || flags.some(flag => flag.llm_mode !== "manual")) throw new Error("This export only supports the prepared document review. Its review mode does not match.");
  if (!Object.hasOwn(READINESS_LABEL, report.status) || (report.decision !== null && !Object.hasOwn(DECISION_LABEL, report.decision))) throw new Error("The saved readiness or teacher decision is not recognized.");
  const criterionEvidence = new Map<number, readonly BoundSourceEvidence[]>();
  for (const row of report.results) {
    const entries = evidenceFor("criterion", row.criterion_id);
    if (entries.some(entry => row.evidence[entry.evidence_index]?.quote !== entry.quote || row.evidence[entry.evidence_index]?.anchor !== entry.anchor)) throw new Error("The criterion's saved evidence does not match its source images.");
    criterionEvidence.set(row.criterion_id, entries);
  }
  const findingEvidence = new Map<number, readonly BoundSourceEvidence[]>();
  for (const flag of flags) {
    if (flag.check_run_id !== report.check_run_id) throw new Error("A finding belongs to another report.");
    const entries = evidenceFor("flag", flag.id);
    const bound = flagSourcePagePair(flag.source_evidence ?? [], { run: report.check_run_id, manuscript: report.manuscript_id, sourceHash: evidenceSourceHash, flagId: flag.id });
    if (entries.length && (bound.length !== entries.length || !entries.every(entry => bound.some(saved => entry.evidence_id === saved.evidence_id)) || !entries.some(entry => entry.anchor === flag.page_anchor && entry.quote.includes(flag.evidence_excerpt)))) throw new Error("The finding's saved evidence does not match its source images.");
    findingEvidence.set(flag.id, entries);
  }
  const pdf = new jsPDF({ unit: "mm", format: "a4", compress: true });
  pdf.setProperties({ title: `${WORKSPACE_SAMPLE.title} - Manuscript review`, subject: "Instructor decision, grades and manuscript evidence", author: "VERIDICAL" });
  pdf.setLanguage("en-US");
  pdf.setDisplayMode("fullwidth", "continuous", "UseOutlines");
  const destinations = new Map<string, Destination>();
  const gradeLinks: { id: string; page: number; y: number; height: number }[] = [];
  const images = new Map<string, Uint8Array>();
  let y = PRINT.top as number;
  let context = "Manuscript review";
  const text = (value: string) => value.replace(/[\u2018\u2019]/g, "'").replace(/[\u201c\u201d]/g, '"').replace(/[\u2010-\u2015]/g, "-").replace(/\u00a0/g, " ");
  const font = (size = 10.5, bold = false, color: string = PRINT.ink) => { pdf.setFont("helvetica", bold ? "bold" : "normal"); pdf.setFontSize(size); pdf.setTextColor(color); };
  function frame() {
    font(8, true, PRINT.muted); pdf.text("VERIDICAL / INSTRUCTOR COPY", PRINT.margin, 12);
    pdf.setDrawColor(PRINT.border); pdf.line(PRINT.margin, 17, 192, 17);
  }
  function nextPage() {
    pdf.addPage(); y = PRINT.top; frame();
    font(9, true, PRINT.muted);
    const lines: string[] = pdf.splitTextToSize(text(`${context} (continued)`), PRINT.width);
    pdf.text(lines, PRINT.margin, y); y += lines.length * PRINT.line + 4;
  }
  function ensure(height: number) { if (y + height > PRINT.bottom) nextPage(); }
  function write(value: string, options: { size?: number; bold?: boolean; color?: string; after?: number } = {}) {
    const { size = 10.5, bold = false, color = PRINT.ink, after = 3 } = options;
    font(size, bold, color);
    const lines: string[] = pdf.splitTextToSize(text(value), PRINT.width);
    const lineHeight = size * 0.49;
    for (const line of lines) { ensure(lineHeight); font(size, bold, color); pdf.text(line, PRINT.margin, y); y += lineHeight; }
    y += after;
  }
  function label(value: string) { ensure(16); write(value, { size: 10, bold: true, after: 2 }); }
  function start(id: string, title: string, parent?: string) {
    pdf.addPage(); y = PRINT.top; context = title; frame();
    destinations.set(id, { label: title, page: pdf.getNumberOfPages(), parent });
    write(title, { size: 17, bold: true, after: 5 });
  }
  function note(title: string, value: string) { label(title); write(value); }
  async function imageFor(entry: BoundSourceEvidence) {
    if (entry.check_run_id !== report.check_run_id || entry.manuscript_id !== report.manuscript_id || entry.source_sha256 !== evidenceSourceHash || !entry.image_id || !entry.image_sha256) throw new Error("A source image is not bound to this report.");
    const cached = images.get(entry.image_id);
    if (cached) return cached;
    const source = await fetchAsset(`/sample-assets/evidence/${entry.image_id}.png`, { credentials: "omit" });
    if (!source.ok) throw new Error("A manuscript evidence image could not be loaded.");
    const buffer = await source.arrayBuffer();
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", buffer)), byte => byte.toString(16).padStart(2, "0")).join("");
    if (hash !== entry.image_sha256) throw new Error("The source image checksum does not match its evidence record.");
    const bytes = new Uint8Array(buffer); images.set(entry.image_id, bytes); return bytes;
  }
  async function evidence(entries: readonly BoundSourceEvidence[]) {
    for (const [index, entry] of entries.entries()) {
      if (index || PRINT.bottom - y < 130) nextPage();
      label(`Evidence ${index + 1} of ${entries.length}: ${entry.label}`);
      write(`Original manuscript: PDF page ${entry.page} of ${WORKSPACE_SAMPLE.pageCount}`, { size: 9, color: PRINT.muted });
      write(`"${entry.quote}"`);
      if (!fileAvailable) { write("Original page image unavailable: the manuscript file was removed from this browser record."); continue; }
      const bytes = await imageFor(entry);
      write(entry.precision === "page_bbox" ? "The red boxes mark the exact quotation on the original page." : "This shows the original page. An exact quotation position is not marked.", { size: 9, color: PRINT.muted });
      if (PRINT.bottom - y < 95) { nextPage(); write(`Evidence ${index + 1}: original manuscript page ${entry.page}`, { size: 10, bold: true }); }
      const dimensions = pdf.getImageProperties(bytes);
      const imageHeight = Math.min(PRINT.bottom - y - 4, PRINT.width * dimensions.height / dimensions.width);
      const imageWidth = imageHeight * dimensions.width / dimensions.height;
      const imageX = (210 - imageWidth) / 2;
      pdf.setDrawColor(PRINT.border); pdf.rect(imageX, y, imageWidth, imageHeight);
      pdf.addImage(bytes, "PNG", imageX, y, imageWidth, imageHeight, entry.image_id ?? undefined, "FAST");
      y += imageHeight + 6;
    }
  }

  // The first page answers whose work this is and what the teacher decided.
  frame();
  write("Manuscript review", { size: 11, bold: true, color: PRINT.muted, after: 5 });
  write(WORKSPACE_SAMPLE.title, { size: 24, bold: true, after: 5 });
  label("Members / manuscript authors");
  write(WORKSPACE_SAMPLE.authors.join("  /  "), { size: 11, after: 7 });
  font(20, true);
  const showReviewResult = !report.decision && hasPreparedAiGrades(report);
  const summary = pdfCoverSummary(report);
  const verdict = summary.value;
  const verdictLines: string[] = pdf.splitTextToSize(verdict, PRINT.width - 12);
  const cardHeight = 19 + verdictLines.length * 9;
  pdf.setFillColor(PRINT.paper); pdf.setDrawColor(PRINT.border); pdf.roundedRect(PRINT.margin, y - 3, PRINT.width, cardHeight, 2, 2, "FD");
  font(10, true); pdf.text(summary.label, PRINT.margin + 6, y + 4);
  font(20, true, report.decision === "approved" ? PRINT.success : report.decision === "rejected" ? PRINT.danger : PRINT.ink);
  pdf.text(verdictLines, PRINT.margin + 6, y + 14); y += cardHeight + 4;
  if (report.decided_at) write(`Decision saved: ${new Date(report.decided_at).toISOString().replace("T", " ").slice(0, 16)} UTC`, { size: 9, color: PRINT.muted });
  write(showReviewResult ? "Instructor decision: No decision recorded" : `System readiness: ${READINESS_LABEL[report.status]}`, { bold: true });
  write(report.decision ? "The decision above was recorded by the teacher. The readiness band is a separate summary of the saved grades." : "The teacher has not made a final decision. The readiness band does not approve or reject a defense.");
  const ungraded = report.results.filter(row => !["passed", "failed", "not_applicable"].includes(row.outcome)).length;
  const pending = report.pending_review_count ?? ungraded;
  write(`${pending} of ${report.results.length} criteria still need teacher review. ${flags.filter(flag => !flag.overridden).length} of ${flags.length} findings remain open.`, { bold: true });
  note("About this review", hasPreparedAiGrades(report) ? `AI review complete. ${AI_REVIEW_DESCRIPTION} Statistical forensics does not apply to this software report. Originality comparison was not run.` : "Prepared document review. The app's automated grading service was not run. The teacher records the grades and final decision. Statistical forensics does not apply to this software report. Originality comparison was not run.");
  write("Published source: Hamja, Anjum and Maruf (2025). CC BY 4.0. The original manuscript is unchanged.", { size: 9, color: PRINT.muted });
  write("Use the linked contents or PDF bookmarks to jump to a grade, check or source detail.", { size: 10, color: PRINT.blue });
  if (report.decision_note) { context = "Instructor decision note"; note("Teacher's decision note", report.decision_note); }
  const coverLast = pdf.getNumberOfPages();
  start("contents", "Find your way through this report");
  const contentsPage = pdf.getNumberOfPages();
  // Reserve the contents page; exact destinations are filled after pagination.

  start("grades", "Grades at a glance");
  write(hasPreparedAiGrades(report) ? "These are the saved AI-review grades and any later teacher changes. Findings are review prompts, not a second set of grades." : "These are the teacher's saved rubric grades. Findings are review prompts; they are not a second set of grades.");
  for (const row of report.results) {
    ensure(22);
    const top = y;
    label(row.text); write(pdfGrade(row), { color: PRINT.blue, after: 6 });
    gradeLinks.push({ id: `criterion-${row.criterion_id}`, page: pdf.getNumberOfPages(), y: top - 4, height: y - top });
  }
  for (const [index, row] of report.results.entries()) {
    start(`criterion-${row.criterion_id}`, `Grade ${index + 1}: ${row.text}`, "grades");
    context = `${row.text} / ${pdfGrade(row)}`;
    write(`${row.basis === AI_REVIEW_BASIS ? "AI-review grade" : "Teacher's grade"}: ${pdfGrade(row)}`, { bold: true });
    if (row.outcome === "not_applicable") write("This criterion is ungraded and left out of the readiness calculation.");
    const savedReason = row.reasoning || row.reason;
    const preparedReason = sampleReport.results.find(candidate => candidate.criterion_id === row.criterion_id)?.reasoning;
    note("What to check", (savedReason === preparedReason ? PLAIN_CRITERION_REASONS[row.criterion_id] : savedReason) || "Read the evidence and use the rubric to choose a grade.");
    if (row.resolution?.reason) note(row.outcome === "not_applicable" ? "Teacher's reason for excluding this criterion" : "Teacher's comment (private)", row.resolution.reason);
    const bound = criterionEvidence.get(row.criterion_id)!;
    await evidence(bound);
    // Keep any unbound saved quotation visible without inventing an image.
    for (const item of row.evidence) if (!bound.some(entry => entry.quote === item.quote && entry.anchor === item.anchor)) note(`Additional saved evidence: ${item.anchor}`, item.quote);
    if (!bound.length && !row.evidence.length) write("No source evidence was saved for this criterion.");
  }
  const modules: string[] = [...CHECK_KIND_ORDER];
  for (const flag of flags) if (!modules.includes(flag.check_kind)) modules.push(flag.check_kind);
  for (const kind of modules) {
    const moduleFlags = flags.filter(flag => flag.check_kind === kind);
    if (kind === "originality_reuse" && !moduleFlags.length && !flags.some(flag => flag.check_kind === "statistical_forensics") && PRINT.bottom - y > 100) {
      y += 14; context = integrityModuleLabel(kind);
      destinations.set(`module-${kind}`, { label: context, page: pdf.getNumberOfPages() });
      write(context, { size: 17, bold: true, after: 5 });
    } else start(`module-${kind}`, integrityModuleLabel(kind));
    if (kind === "statistical_forensics") note("Not applicable to this prepared review", "This software implementation report has no recorded statistical test for this example. No statistical judgment is made.");
    else if (kind === "originality_reuse") note("Comparison not run", "The manuscript was not compared with other works. No similarity or originality judgment is recorded.");
    else if (!moduleFlags.length) write("No source observations are recorded for this check. This is not a passed check.");
    write(`${moduleFlags.length} recorded ${moduleFlags.length === 1 ? "finding" : "findings"} / ${moduleFlags.filter(flag => !flag.overridden).length} open${moduleFlags.length ? " / Prepared source observations" : ""}`, { size: 9, color: PRINT.muted });
    for (const [moduleIndex, flag] of moduleFlags.entries()) {
      const number = flags.indexOf(flag) + 1;
      const title = problemLabel(flag.ai_verdict_summary) ?? "Check this manuscript statement";
      const heading = `Finding ${number}: ${title}`;
      if (moduleIndex) start(`finding-${flag.id}`, heading, `module-${kind}`);
      else {
        context = heading;
        destinations.set(`finding-${flag.id}`, { label: heading, page: pdf.getNumberOfPages(), parent: `module-${kind}` });
        write(heading, { size: 14, bold: true });
      }
      write(`${integrityModuleLabel(kind)} / ${flag.severity === "med" ? "Medium" : humanize(flag.severity)} severity`, { size: 9, color: PRINT.muted });
      write(`Teacher's action: ${pdfFindingState(flag)}`, { bold: true });
      const preparedReason = sampleFlags.find(candidate => candidate.id === flag.id)?.ai_reasoning;
      note("Why it needs review", (flag.ai_reasoning === preparedReason ? PLAIN_FINDING_REASONS[flag.id] : flag.ai_reasoning) || "Compare the saved evidence with the manuscript before deciding what to do.");
      if (flag.override_reason) note("Teacher's reason", flag.override_reason);
      if (flag.annotation) note("Private teacher note", flag.annotation);
      const bound = findingEvidence.get(flag.id)!;
      if (!bound.some(entry => entry.quote.includes(flag.evidence_excerpt) && entry.anchor === flag.page_anchor)) note(`Saved finding excerpt: ${flag.page_anchor}`, flag.evidence_excerpt);
      await evidence(bound);
    }
  }
  start("details", "Source and review details");
  note("Rubric used for these grades", report.rubric_title ?? "Rubric name not recorded");
  note("Original manuscript", WORKSPACE_SAMPLE.attribution);
  note("Original file", `${WORKSPACE_SAMPLE.filename} / ${WORKSPACE_SAMPLE.pageCount} pages`);
  note("Source", WORKSPACE_SAMPLE.source);
  pdf.textWithLink("Open the published manuscript", PRINT.margin, y, { url: WORKSPACE_SAMPLE.source }); y += 10;
  note("Licence", WORKSPACE_SAMPLE.license);
  note("Original file checksum (SHA-256)", WORKSPACE_SAMPLE.sha256);
  note("How this report was prepared", `${hasPreparedAiGrades(report) ? AI_REVIEW_DESCRIPTION : "The app's automated grading service was not run."} This instructor copy contains the saved grades, source observations and teacher actions. Source images show the unchanged published manuscript, with exact saved quotations boxed only where their positions were verified.`);
  note("Private details", "This instructor PDF can include private comments, reasons and notes. The read-only shared report uses a separate set of permitted fields.");
  write("The PDF has selectable text, page links and bookmarks. It is not a tagged PDF. Use the app report if you need its accessible reading controls.");

  const lastPage = pdf.getNumberOfPages();
  for (const link of gradeLinks) {
    pdf.setPage(link.page);
    pdf.link(PRINT.margin, link.y, PRINT.width, link.height, { pageNumber: destinations.get(link.id)!.page });
  }
  pdf.setPage(contentsPage); y = 43;
  write("Start with the summary, then open the grade or check you want to inspect. Each section keeps its explanation and source pages together.", { after: 7 });
  const majorIds = ["grades", ...modules.map(kind => `module-${kind}`), "details"];
  for (const id of majorIds) {
    const destination = destinations.get(id)!;
    font(11, true, PRINT.blue);
    pdf.textWithLink(`${destination.label}  /  report page ${destination.page}`, PRINT.margin, y, { pageNumber: destination.page }); y += 12;
  }
  write("Red boxes show the exact passage on an original page. The quotation is also repeated as readable text. 'Report page' refers to this export; 'manuscript page' refers to the original paper.");
  const parents = new Map<string, ReturnType<typeof pdf.outline.add>>();
  pdf.outline.add(null, "Summary and instructor decision", { pageNumber: 1 });
  for (const [id, destination] of destinations) parents.set(id, pdf.outline.add(destination.parent ? parents.get(destination.parent) : null, destination.label, { pageNumber: destination.page }));
  for (let index = 1; index <= lastPage; index++) {
    pdf.setPage(index); font(8, false, PRINT.muted);
    pdf.text(`Instructor copy / Report page ${index} of ${lastPage}`, PRINT.margin, 289);
    if (index > coverLast && index !== contentsPage) { font(8, false, PRINT.blue); pdf.textWithLink("Back to contents", 163, 289, { pageNumber: contentsPage }); }
  }
  return pdf.output("blob");
}
