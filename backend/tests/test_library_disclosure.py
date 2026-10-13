"""V-077 (D-028): the PDF export's originality disclosure names what was
compared. Pure function, no database."""

from app.report.export import _archive_disclosure


def test_library_zero_is_never_worded_as_a_clean_result():
    text = _archive_disclosure(0, "library")
    assert "no Library records were available to compare against" in text
    assert "not evidence of originality" in text


def test_library_count_names_the_library_and_its_limit():
    assert "compared against 1 Library record " in _archive_disclosure(1, "library")
    text = _archive_disclosure(12, "library")
    assert "compared against 12 Library records" in text
    assert "not in the Library were not compared" in text


def test_a_pre_library_result_is_labelled_as_such():
    text = _archive_disclosure(5, None)
    assert "before the Library became a separate collection" in text


def test_a_check_that_did_not_run_says_so():
    assert "did not run" in _archive_disclosure(None, "library")
