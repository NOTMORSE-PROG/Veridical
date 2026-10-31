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


def _filler(prefix: str, count: int) -> list[str]:
    # Letter-only words: the tokenizer drops digits.
    letters = "abcdefghijklmnopqrstuvwxyz"
    return [f"{prefix}{a}{b}" for a in letters for b in letters][:count]


def test_one_long_shared_run_confirms_when_the_shingle_share_is_low():
    """The run test alone: two long passages share one 13-word run."""
    run = _filler("run", 13)
    own = " ".join(_filler("own", 60) + run)
    other = " ".join(run + _filler("oth", 60))
    assert shingle_overlap(own, other, 5) < 0.5
    assert shares_wording(own, other, _settings())
    assert not shares_wording(own, other, _settings(reuse_confirm_run_words=50))


def _segments(sep_prefix: str, count: int, size: int) -> tuple[list[list[str]], str]:
    segments = [_filler(f"s{chr(97 + k)}", size) for k in range(count)]
    seps = _filler(sep_prefix, count)
    text = " ".join(" ".join(seg) + " " + sep for seg, sep in zip(segments, seps, strict=True))
    return segments, text


def test_a_high_shingle_share_confirms_without_a_long_run():
    """The share test alone: many 10-word runs, none as long as 12 words."""
    _, own = _segments("xa", 6, 10)
    _, other = _segments("xb", 6, 10)
    assert longest_common_run(own, other) < 12
    assert shingle_overlap(own, other, 5) >= 0.5
    assert shares_wording(own, other, _settings())
    assert not shares_wording(own, other, _settings(reuse_confirm_shingle_share=1.0))


def test_the_share_is_taken_over_the_shorter_passage():
    """A short passage whose shingles all recur in a much longer one."""
    _, own = _segments("xa", 4, 10)
    _, body = _segments("xb", 4, 10)
    other = body + " " + " ".join(_filler("pad", 300))
    assert longest_common_run(own, other) < 12
    assert shares_wording(own, other, _settings())


def test_a_fragment_too_short_for_the_share_test_needs_a_long_run():
    fragment = "System should provide functionality to give personalized results"
    assert shingle_overlap(fragment, REQUIREMENTS_A, 5) == 1.0
    assert not shares_wording(fragment, REQUIREMENTS_A, _settings())
    assert shares_wording(fragment, REQUIREMENTS_A, _settings(reuse_confirm_share_min_words=5))


def test_lightly_edited_text_keeps_enough_shared_wording():
    edited = COPIED.replace("respondents", "participants").replace("easy", "simple")
    assert shares_wording(COPIED, edited, _settings())


def test_thresholds_are_settings():
    assert shingle_overlap(COPIED, COPIED, 5) == 1.0
    strict = _settings(reuse_confirm_run_words=500, reuse_confirm_shingle_share=1.0)
    own = "Unrelated words first. " + COPIED
    assert not shares_wording(own, COPIED + " and more words after it", strict)
