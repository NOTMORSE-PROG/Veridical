"""The SPRITE worker answers exactly as before without loading pysprite's
plotting libraries, and any use of one ends as an unassessed row."""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

import app
from app.checks.forensics.service import _compute_forensics_findings
from app.models.enums import ResultOutcome
from tests.test_forensics_sprite_pipeline import extraction

STOOD_IN = ("pandas", "matplotlib", "matplotlib.pyplot", "seaborn", "scipy")
WORKER = [sys.executable, "-m", "app.checks.forensics.sprite_worker"]
# The same worker with the real libraries already loaded, which the worker
# leaves in place: this is how it ran before the stand-ins. It refuses to run
# unless pysprite really did get the real pandas.
WITH_LIBRARIES = [
    sys.executable,
    "-c",
    "import sys, runpy, pandas, matplotlib.pyplot, seaborn, scipy.stats\n"
    "worker = runpy.run_module('app.checks.forensics.sprite_worker', run_name='baseline')\n"
    "assert sys.modules['pysprite.pysprite'].pd is pandas\n"
    "worker['main']()\n",
]
TIMING = ("worker_startup_seconds", "worker_search_seconds")


def _task(n, mean, sd, *, low=1, high=5, items=1, mean_prec=2, sd_prec=2):
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "min_val": low,
        "max_val": high,
        "n_items": items,
        "mean_prec": mean_prec,
        "sd_prec": sd_prec,
        "seed": 0,
        "init_method": "minvar",
        "max_iter": 2000,
    }


# Each case: the task, the status, and for a found candidate its answers as
# (value, how many) pairs, so a change that alters both runs alike still shows.
CASES = {
    # The row production has run (n=10, every answer 3).
    "declared item, every answer the same": (
        _task(10, 3.0, 0.0),
        "candidate_found",
        [(3.0, 10)],
    ),
    "sample SD on a 1-5 item": (
        _task(10, 3.0, 1.05),
        "candidate_found",
        [(1.0, 1), (2.0, 1), (3.0, 6), (4.0, 1), (5.0, 1)],
    ),
    "survey mean and SD": (
        _task(30, 3.47, 1.04),
        "candidate_found",
        [(1.0, 2), (3.0, 17), (4.0, 5), (5.0, 6)],
    ),
    # These two need real search steps, not just the starting distribution.
    "fifty answers": (
        _task(50, 3.46, 1.13),
        "candidate_found",
        [(1.0, 5), (3.0, 23), (4.0, 12), (5.0, 10)],
    ),
    "averaged three-item scale": (
        _task(30, 3.21, 0.74, items=3),
        "candidate_found",
        [(4 / 3, 2), (3.0, 12), (10 / 3, 10), (11 / 3, 1), (5.0, 2)],
    ),
    # 3.33 cannot come from ten whole answers (33/10 or 34/10): GRIM fails,
    # and pysprite refuses the input.
    "mean no ten answers can give": (_task(10, 3.33, 1.0), "library_error", None),
    # Half ones and half fives give the largest SD on 1-5, about 2.11, so
    # pysprite refuses 2.5 outright.
    "SD larger than the scale allows": (_task(10, 3.0, 2.5), "library_error", None),
    # Inside that range, but no ten whole answers averaging 3 give an SD that
    # rounds to 2.05 (every combination checked), so the search finds none.
    "SD no ten answers can give": (_task(10, 3.0, 2.05), "not_found_within_budget", None),
}


def _run(command, task):
    completed = subprocess.run(
        command,
        input=json.dumps(task),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=120,
        check=True,
    )
    response = json.loads(completed.stdout)
    for field in TIMING:
        response.pop(field)
    return response


@pytest.mark.parametrize("name", CASES)
def test_the_worker_answers_exactly_as_it_did_with_the_libraries_loaded(name):
    task, status, answers = CASES[name]
    lean = _run(WORKER, task)
    full = _run(WITH_LIBRARIES, task)
    assert json.dumps(lean, sort_keys=True) == json.dumps(full, sort_keys=True)
    assert lean["status"] == status
    found = sorted(Counter(lean.get("distribution") or []).items())
    assert found == (answers or [])


def test_no_app_module_imports_the_worker():
    """Importing the worker stands in for whole libraries in that process, so
    it must only ever run as a process of its own."""
    root = Path(app.__file__).parent
    pattern = re.compile(r"import\s+app\.checks\.forensics\.sprite_worker|import\s+sprite_worker")
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.name != "sprite_worker.py" and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_worker_does_not_load_the_plotting_libraries():
    probe = (
        "import json, sys\n"
        "import app.checks.forensics.sprite_worker as worker\n"
        "names = " + repr(STOOD_IN) + "\n"
        "print(json.dumps({\n"
        "    'real': [n for n in names if not isinstance(sys.modules[n], worker._NotLoaded)],\n"
        "    'scipy.stats': 'scipy.stats' in sys.modules,\n"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], text=True, stdout=subprocess.PIPE, timeout=60, check=True
    )
    assert json.loads(completed.stdout) == {"real": [], "scipy.stats": False}


# Runs the real worker after making pysprite's search touch one library and
# then report a valid candidate for the test row. Only a stand-in that raises
# turns that into a library error; with the real library loaded, the row
# would come back compatible.
TOUCH = (
    "import importlib, sys\n"
    "import app.checks.forensics.sprite_worker as worker\n"
    "import pysprite\n"
    "module, _, attribute = sys.argv[1].rpartition('.')\n"
    "def search(*args, **kwargs):\n"
    "    getattr(importlib.import_module(module), attribute)\n"
    "    return 'Success', [3] * 10, 0.0\n"
    "pysprite.Sprite.find_possible_distribution = search\n"
    "worker.main()\n"
)
TOUCHED = [
    "pandas.DataFrame",
    "matplotlib.pyplot.subplots",
    "matplotlib.colors",
    "seaborn.heatmap",
    "scipy.stats",
]


@pytest.mark.parametrize("target", TOUCHED)
def test_any_use_of_a_stand_in_leaves_the_row_unassessed_and_escalates(target):
    run = subprocess.run

    def touching_worker(args, **kwargs):
        return run([sys.executable, "-c", TOUCH, target], **kwargs)

    with patch("app.checks.forensics.sprite.subprocess.run", side_effect=touching_worker):
        result = _compute_forensics_findings(extraction())
    receipt = result.reconstruction
    assert receipt["rows"][0]["state"] == "library_error"
    assert receipt["compatible_rows"] == 0 and receipt["unassessed_rows"] == 1
    assert result.outcome == ResultOutcome.escalated
    assert result.flag_drafts == []
