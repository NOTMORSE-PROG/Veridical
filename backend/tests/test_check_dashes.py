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
    for key, text in json.loads(path.read_text(encoding="utf-8")).items():
        assert "—" not in text and "–" not in text, key
