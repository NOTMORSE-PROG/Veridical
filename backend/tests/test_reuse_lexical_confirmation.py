"""BUG-620: a similarity match is raised only when the passages share wording.

The texts below are the cases that motivated the rule: an approval sheet that
matched an agreement's signature block at 0.935, and two unrelated
requirements lists that matched at 0.855. Both share no wording."""

from app.checks.reuse.lexical import longest_common_run, shares_wording, shingle_overlap
from app.config import Settings

APPROVAL_SHEET = (
    "APPROVAL SHEET This research entitled Destamp: A Mobile Application for Generating "
    "Budget-Based Itineraries to Enhance Travel Planning prepared and submitted by the "
    "proponents has been evaluated and accepted by the Panel of Evaluators. Project Study "
    "Panelists: Signature ________ Date ________ Project Study Adviser: Signature ________ "
    "Date ________ Project Study Coordinator: Dean, College of Engineering: Signature"
)
AGREEMENT_SIGNATURES = (
    "15. Agreement By signing this agreement, both parties confirm that they have read, "
    "understood, and agreed to the terms and conditions stated above. FOR COMPANY C - "
    "PROVIDER Name: ________ Position: ________ Signature: ________ Date: ________ FOR "
    "COMPANY D - SUBSCRIBER Name: ________ Position: ________ Signature: ________ Date:"
)
REQUIREMENTS_A = (
    "System should provide functionality to keep track order details through a chat "
    "feature. System should provide functionality to give personalized results using a "
    "recommendation engine. Non-functional Requirements Efficiency System should minimize "
    "network requests as much as possible and optimize the use of software packages."
)
REQUIREMENTS_B = (
    "Usability The system shall provide a uniform look and feel between all the mobile "
    "application parts. The app should feature a user-friendly and intuitive interface that "
    "allows users to navigate and access various functionalities effortlessly. The system "
    "should use English as its language and include informative error messages."
)
COPIED = (
    "The respondents were asked to rate the usability of the prototype after completing "
    "five tasks, and most of them reported that the navigation was easy to learn."
)


def _settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_same_genre_text_without_shared_wording_is_not_confirmed():
    settings = _settings()
    assert not shares_wording(APPROVAL_SHEET, AGREEMENT_SIGNATURES, settings)
    assert not shares_wording(REQUIREMENTS_A, REQUIREMENTS_B, settings)


def test_a_short_copy_inside_a_longer_passage_is_confirmed_by_its_run():
    settings = _settings()
    own = "Some surrounding words of the host thesis come first. " + COPIED
    other = COPIED + " The source then goes on to discuss something else entirely here."
    assert longest_common_run(own, other) >= settings.reuse_confirm_run_words
    assert shares_wording(own, other, settings)


def test_lightly_edited_text_keeps_enough_shared_wording():
    edited = COPIED.replace("respondents", "participants").replace("easy", "simple")
    assert shares_wording(COPIED, edited, _settings())


def test_thresholds_are_settings():
    assert shingle_overlap(COPIED, COPIED, 5) == 1.0
    strict = _settings(reuse_confirm_run_words=500, reuse_confirm_shingle_share=1.0)
    own = "Unrelated words first. " + COPIED
    assert not shares_wording(own, COPIED + " and more words after it", strict)
