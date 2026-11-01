"""A plant's objective set aside as a restatement is reached through the
original statement of the same objective, and only through that one."""

from collections import Counter

from tools.accuracy.f4_cases import states_objective

RESTATEMENT = (
    "The third objective was to engineer and characterize a versatile, low-power, "
    "multi-modal animal-borne platform (CollarID) to overcome the limits of trackers."
)
ORIGINAL = (
    "To engineer and characterize a versatile, low-power, multi-modal animal- borne "
    "platform (CollarID) for diverse wildlife monitoring. This objective addresses tracking."
)
OTHER = (
    "To design, deploy, and evaluate a novel, head-worn wearable platform (AirSpecs) "
    "capable of sensing the indoor environment."
)
VOCAB = Counter(
    w.lower() for text in (RESTATEMENT, ORIGINAL, OTHER) for w in text.replace("-", " ").split()
)


def test_the_original_statement_of_the_same_objective_states_it():
    assert states_objective(ORIGINAL, RESTATEMENT, VOCAB)


def test_another_objective_that_shares_a_few_words_does_not():
    # Both are "platform" objectives; sharing that word is not stating it.
    assert not states_objective(OTHER, RESTATEMENT, VOCAB)


def test_a_sentence_sharing_under_half_of_the_core_does_not_state_it():
    # Four of the restatement's nine core words is a related objective, not this one.
    assert not states_objective(
        "To engineer a versatile multi sensor platform for birds.", RESTATEMENT, VOCAB
    )
