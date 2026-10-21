"""F5 absence claims require supported identity and citation-style coverage."""

from app.checks.citations.extract import merge_wrapped_lines
from app.ingest.citation_dates import reference_date_key
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.models.enums import CitationParseStatus


def guarded_absence_candidates(candidates, references, body_blocks, settings):
    patterns = load_reference_metadata_patterns(settings.reference_metadata_patterns_file)
    mentions = []
    for block in merge_wrapped_lines(body_blocks):
        for match in patterns.standard_identifier.finditer(block.text):
            mentions.append((match, block))
    leads = []
    possible = set()
    for reference in references:
        for listed in patterns.standard_identifier.finditer(reference.raw_text):
            for mention, block in mentions:
                if (listed.group("series"), listed.group("number")) != (
                    mention.group("series"),
                    mention.group("number"),
                ):
                    continue
                edition = mention.group("edition")
                listed_edition = listed.group("edition") or str(reference.year or "")
                if edition and edition != listed_edition:
                    continue
                possible.add(reference.order_index)
                leads.append(
                    {
                        "reference_order_index": reference.order_index,
                        "raw_mark": mention.group(0),
                        "page": block.page,
                        "paragraph": block.paragraph,
                        "status": "possible_identifier_link",
                        "reason": (
                            "A matching standard identifier is mentioned; edition and "
                            "claim support are unassessed."
                        ),
                    }
                )
                break
            if reference.order_index in possible:
                break
    unsupported = {
        reference.order_index
        for reference in references
        if reference.parse_status != CitationParseStatus.parsed
        or not reference.authors
        or reference_date_key(reference) is None
    }
    unassessed = possible | unsupported
    return ([ref for ref in candidates if ref.order_index not in unassessed], unassessed, leads)
