"""Source-owned causal explanations, without judging whether they are adequate."""

import re

from app.checks.agreement.context import PairContext
from app.checks.agreement.cues import AgreementCues
from app.checks.agreement.extract import Statement, _sentence_spans
from app.checks.injection import detect_injection_signal


def reported_resource_explanation(
    outcome: Statement,
    context: PairContext,
    cues: AgreementCues,
    *,
    verdict: str,
    attainment: str,
    intent_text: str = "",
) -> dict | None:
    policy = cues.source_explanation_policy
    if not policy or verdict not in ("unmet", "partial") or attainment != verdict:
        return None
    source = " ".join((outcome.text, context.text()))
    if detect_injection_signal(" ".join((intent_text, source))).suspected or any(
        re.search(pattern, source, re.IGNORECASE) for pattern in policy["veto_patterns"]
    ):
        return None
    spans = list(_sentence_spans(outcome.text))
    if len(spans) != 1:
        return None

    def terms(text: str) -> list[str]:
        return [
            policy["target_term_aliases"].get(term, term)
            for term in re.findall(policy["target_term_pattern"], text.casefold())
            if term not in policy["target_stop_terms"]
        ]

    for start, end in spans:
        sentence = outcome.text[start:end].strip()
        for pattern in policy["causal_patterns"]:
            for connector in re.finditer(pattern, sentence, re.IGNORECASE):
                before, after = sentence[: connector.start()], sentence[connector.end() :]
                gaps = [
                    match
                    for pattern in policy["nonachievement_patterns"]
                    for match in re.finditer(pattern, before, re.IGNORECASE)
                ]
                if len(gaps) != 1:
                    continue
                target = before[gaps[0].end() :]
                if any(
                    re.search(policy["clause_boundary_pattern"], text, re.IGNORECASE)
                    for text in (target, after)
                ):
                    continue
                target_terms, intent_terms = terms(target), terms(intent_text)
                if len(set(target_terms)) < policy["min_target_shared_terms"] or not any(
                    intent_terms[index : index + len(target_terms)] == target_terms
                    for index in range(len(intent_terms) - len(target_terms) + 1)
                ):
                    continue
                constraint = next(
                    (
                        match
                        for pattern in policy["constraint_patterns"]
                        if (match := re.fullmatch(pattern, after, re.IGNORECASE)) is not None
                    ),
                    None,
                )
                if constraint is None:
                    continue
                # Only a negation inside the matched constraint predicate can
                # assert unavailability. Extra negatives still deny the cause.
                allowed_negation = (
                    constraint.span("constraint_negation")
                    if "constraint_negation" in constraint.re.groupindex
                    else (-1, -1)
                )
                if any(
                    match.span() != allowed_negation
                    for match in re.finditer(
                        policy["constraint_denial_pattern"], after, re.IGNORECASE
                    )
                ):
                    continue
                return {
                    "method": policy["version"],
                    "basis": "limitation",
                    "source_quote": sentence,
                    "source_anchor": outcome.anchor,
                    "cause_quote": after.strip(),
                    "label": policy["label"],
                    "adequacy_assessed": False,
                }
    return None
