import type { PublicEvidenceImageOut, PublicReportOut, FlagSummaryOut, ResultRowCommon } from "../api/types";

export function sharedGradeLabel(row: ResultRowCommon): string {
  if (row.outcome === "not_applicable") return "Excluded from readiness";
  if (row.outcome === "unverifiable") return "Could not assess";
  if (row.outcome === "api_down") return "Not assessed: service unavailable";
  if (row.outcome === "quota_exhausted") return "Not assessed: capacity reached";
  if (row.outcome !== "passed" && row.outcome !== "failed") return "Needs review";
  return row.level?.name ?? (row.outcome === "passed" ? "Meets criterion" : "Does not meet criterion");
}

export function associateSharedEvidence(images: readonly PublicEvidenceImageOut[], report: PublicReportOut, flags: readonly FlagSummaryOut[]) {
  const criteria = new Map<number, PublicEvidenceImageOut[]>();
  const findings = new Map<number, PublicEvidenceImageOut[]>();
  const additional: PublicEvidenceImageOut[] = [];
  for (const image of images) {
    const subject = image.subject;
    if (subject?.kind === "criterion" && Number.isSafeInteger(subject.id)) {
      const row = report.results.find(item => item.criterion_id === subject.id);
      if (row?.evidence.some(item => item.quote === image.quote && item.anchor === image.anchor)) {
        criteria.set(subject.id, [...criteria.get(subject.id) ?? [], image]); continue;
      }
    }
    if (subject?.kind === "flag" && Number.isSafeInteger(subject.id) && flags.some(flag => flag.id === subject.id)) {
      findings.set(subject.id, [...findings.get(subject.id) ?? [], image]); continue;
    }
    additional.push(image);
  }
  return { criteria, findings, additional };
}
