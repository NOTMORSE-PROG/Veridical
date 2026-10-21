import type { FlagOut } from "../api/types";

export const CITATION_COMPARISON: NonNullable<FlagOut["citation_metadata_comparison"]> = {
  schema_version: "doi-metadata-v1", provider: "crossref",
  cited: { doi: "10.1234/control", title: "Cited study title", year: 2026, first_author: "Reyes, J." },
  registered: { doi: "10.1234/control", title: "Registered study title", year: 2013, first_author: "Kucsko" },
  differing_fields: ["title", "year", "first author"], title_status: "differs", unassessed_reason: null,
  policy_version: "doi-title-nfc-si-v1", policy_sha256: "abc-saved-policy-hash",
  doi_source_url: "https://doi.org/10.1234%2Fcontrol",
  provider_record_url: "https://api.crossref.org/works/10.1234%2Fcontrol",
};
