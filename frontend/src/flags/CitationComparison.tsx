// V-081 F5, screens 4i/4w: saved registry evidence, never a live reconstruction.
import { useId } from "react";
import type { FlagOut } from "../api/types";
import { isCitationMetadataFinding } from "../domain/findingMethod";
import { Alert } from "../ui/Alert";

const FIELDS = [
  ["doi", "DOI", "doi"], ["title", "Title", "title"],
  ["year", "Year", "year"], ["first_author", "First author", "first author"],
] as const;
const PROVIDERS: Record<string, string> = { crossref: "Crossref", datacite: "DataCite", s2: "Semantic Scholar" };

function safeExternal(value: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : undefined;
  } catch { return undefined; }
}

export function CitationComparison({ flag }: { flag: FlagOut }) {
  const headingId = useId();
  if (!isCitationMetadataFinding(flag)) return null;
  const evidence = flag.citation_metadata_comparison;
  if (!evidence) return <Alert title="Provider comparison was not saved" tone="warning">This historical finding does not include the provider metadata used for comparison. Its original reference and recorded explanation remain available. Run a new check to create a new evidence record, or verify the source independently.</Alert>;
  const provider = PROVIDERS[evidence.provider] ?? "Not recorded";
  const marked = FIELDS.filter(([, , key]) => evidence.differing_fields.includes(key)).map(([, label]) => label);
  const doiUrl = safeExternal(evidence.doi_source_url);
  const recordUrl = safeExternal(evidence.provider_record_url);
  return <section className="signal-reasoning signal-copy-flow signal-citation-comparison" aria-labelledby={headingId}>
    <h3 id={headingId}>Recorded citation metadata comparison</h3>
    <p>The check recorded differences between this reference and metadata returned for its DOI. Compare both records before deciding what needs correction. Source existence does not establish support for a manuscript claim.</p>
    <p>Fields marked different: {marked.join(", ") || "Not recorded"}.</p>
    <table aria-labelledby={headingId}>
      <thead><tr><th scope="col">Field</th><th scope="col">In manuscript reference</th><th scope="col">Returned by {provider}</th></tr></thead>
      <tbody>{FIELDS.map(([key, label, difference]) => <tr key={key}>
        <th scope="row">{label}{evidence.differing_fields.includes(difference) && <span>: Marked different</span>}</th>
        <td>{evidence.cited[key] ?? "Not recorded"}</td><td>{evidence.registered[key] ?? "Not recorded"}</td>
      </tr>)}</tbody>
    </table>
    <p><strong>Recorded provider:</strong> {provider}</p>
    {(doiUrl || recordUrl) && <p>
      {doiUrl && <a href={doiUrl} target="_blank" rel="noopener noreferrer">Open DOI source (opens in a new tab)</a>}
      {doiUrl && recordUrl && " · "}
      {recordUrl && <a href={recordUrl} target="_blank" rel="noopener noreferrer">Open {provider} record (opens in a new tab)</a>}
    </p>}
    <p className="signal-field-hint">These links open current external records. They do not change the saved comparison above.</p>
    <details><summary>Comparison provenance</summary>
      <dl>{[
        ["Provider identity", evidence.provider], ["Lookup DOI", evidence.cited.doi],
        ["Source record URL", evidence.provider_record_url], ["Retrieval time", null],
        ["Comparison policy", evidence.policy_version], ["Policy SHA256", evidence.policy_sha256],
        ["Snapshot schema", evidence.schema_version], ["Original differing fields", evidence.differing_fields.join(", ")],
        ["Title comparison", evidence.title_status], ["Unassessed reason", evidence.unassessed_reason],
      ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || "Not recorded"}</dd></div>)}</dl>
    </details>
  </section>;
}
