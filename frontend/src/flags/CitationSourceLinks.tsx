import { useId } from "react";
import type { FlagOut } from "../api/types";
import { isCitationMetadataFinding } from "../domain/findingMethod";

type Finding = Pick<FlagOut, "check_kind" | "ai_verdict_summary" | "citation_source_locator">;

function navigableUrl(raw: string): string | undefined {
  if (Array.from(raw).some(char => /\s/u.test(char) || char === "\\"
    || char.codePointAt(0)! < 32 || char.codePointAt(0) === 127)) return undefined;
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}

export function CitationSourceLinks({ flag }: { flag: Finding }) {
  const heading = useId();
  const saved = flag.citation_source_locator;
  const claimFinding = ["claim_possibly_unsupported", "claim_support_cannot_determine", "claim_support_injection_suspected"].includes(flag.ai_verdict_summary ?? "");
  const unavailable = flag.check_kind === "citation_integrity" && claimFinding ?
    <section className="signal-reasoning signal-citation-source-links" aria-labelledby={heading}>
      <h3 id={heading}>Check the cited source</h3>
      <p>No saved source link is available for this finding. Find the cited work in the manuscript's reference list and open it independently to compare the claim with the source. A new check can create a new source-link record; this finding remains unchanged.</p>
    </section> : null;
  if (flag.check_kind !== "citation_integrity" || isCitationMetadataFinding(flag)
    || !saved) return unavailable;
  const lookup = saved.basis === "recorded_source_key";
  if (lookup ? saved.order_index !== null || saved.links.some(link => link.kind !== "doi_recorded_lookup")
    : saved.basis !== "saved_reference" || !Number.isSafeInteger(saved.order_index) || saved.order_index < 0
      || saved.links.some(link => link.kind !== "doi_as_cited" && link.kind !== "url_as_cited")) return unavailable;
  const links = saved.links.flatMap(link => {
    const url = navigableUrl(link.url);
    return url ? [{ ...link, url }] : [];
  });
  if (!links.length) return unavailable;
  return <section className="signal-reasoning signal-citation-source-links" aria-labelledby={heading}>
    <h3 id={heading}>{lookup ? "Saved source lookup" : "Source links from this reference"}</h3>
    <ul>{links.map(link => <li key={`${link.kind}:${link.url}`}>
      <strong>{link.kind === "doi_recorded_lookup" ? "DOI used in the saved lookup" : link.kind === "doi_as_cited" ? "DOI as cited" : "URL as cited"}</strong>
      <a href={link.url} target="_blank" rel="noopener noreferrer">{link.url} <span>(opens in a new tab)</span></a>
    </li>)}</ul>
    <p className="signal-field-hint">{lookup
      ? "This identifier was used in the recorded lookup. Opening its current external destination does not update the saved result or confirm the source."
      : "Saved with this reference. Opening a link does not confirm the source or its support for a claim."}</p>
  </section>;
}
