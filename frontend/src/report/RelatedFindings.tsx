// BUG-516: an automatic grade can sit beside findings about the same pages
// (an "Exemplary" references grade beside open citation findings). Say
// that they are on the same pages, that the grade did not use them, and
// whether readiness counts them, so the two never read as a contradiction.
import type { EvidenceItem, FlagSummaryOut } from "../api/types";
import { countsForReadiness } from "../domain/readinessExclusion";
import { RELATED_FINDINGS_COPY, relatedFindings } from "../domain/relatedFindings";
import { instructorAnchor } from "../domain/sourceAnchor";
import { ActionLink } from "../ui/ActionLink";

export function RelatedFindings({ evidence, flags, findingsTo, criterionText }: {
  evidence: EvidenceItem[]; flags: FlagSummaryOut[]; findingsTo: string; criterionText: string;
}) {
  const related = relatedFindings(evidence, flags);
  if (related.length === 0) return null;
  const pages = [...new Set(related.map((flag) => instructorAnchor(flag.page_anchor)))].join(", ");
  const counted = related.filter(countsForReadiness).length;
  return <div className="signal-related-findings">
    <p>{RELATED_FINDINGS_COPY.located(related.length, pages)}</p>
    <p>{RELATED_FINDINGS_COPY.notUsed}{" "}
      {counted === related.length ? RELATED_FINDINGS_COPY.allCounted(related.length)
        : counted === 0 ? RELATED_FINDINGS_COPY.noneCounted(related.length)
          : RELATED_FINDINGS_COPY.someCounted(counted)}</p>
    <ActionLink to={findingsTo} variant="secondary" aria-label={`${RELATED_FINDINGS_COPY.inspect} for ${criterionText}`}>{RELATED_FINDINGS_COPY.inspect}</ActionLink>
  </div>;
}
