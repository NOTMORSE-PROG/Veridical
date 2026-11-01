"""The dash gate reads every user-facing string, including wording kept in a
file full of patterns, and exempts only the pattern arguments themselves."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _gate():
    spec = importlib.util.spec_from_file_location(
        "check_dashes", REPO / "tools" / "check_dashes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wording_beside_patterns_is_still_checked(tmp_path):
    source = tmp_path / "extract.py"
    source.write_text(
        "import re\n"
        'PAGES = re.compile(r"pp\\. \\d+\\u2013\\d+")\n'
        'CLEAN = name.strip(" -\\u2013\\u2014")\n'
        'WORDING = "a style this checker didn\'t recognize \\u2014 please check manually."\n',
        encoding="utf-8",
    )
    hits = _gate().scan_python(source)
    # The pattern and the strip set are input; only the wording is output.
    assert [line for line, _ in hits] == [4]
    assert "please check manually" in hits[0][1]


def test_no_file_is_exempt_as_a_whole():
    assert set() == _gate().ALLOW_REGEX_FILES


def test_linkage_wording_data_has_no_dashes():
    path = REPO / "backend" / "app" / "checks" / "citations" / "data" / "linkage_wording.json"
    wording = json.loads(path.read_text(encoding="utf-8"))
    for key, text in wording.items():
        # The replacement table holds the old wording on purpose.
        if isinstance(text, str):
            assert "—" not in text and "–" not in text, key


def test_a_finding_saved_with_the_old_wording_is_shown_in_todays():
    from app.flags.service import _shown_reasoning

    old = (
        "Reference list entry 'Reyes, J.' (2023) does not appear to be cited anywhere in the "
        "body text. Possible unused reference, or an in-text citation style this checker "
        "didn't recognize — please check manually."
    )
    shown = _shown_reasoning("citation_integrity", {"reason": old}, "Reyes, J. (2023).")
    assert shown.endswith("didn't recognize. Please check manually.")
    assert "—" not in shown


def test_a_quote_of_the_manuscript_is_never_rewritten():
    from app.flags.service import _shown_reasoning

    quoted = "the relationship between cognitive functioning—in particular, executive control"
    assert _shown_reasoning("internal_agreement", {"reason": quoted}, "x") == quoted
