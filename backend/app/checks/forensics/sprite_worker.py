"""F6.3 child protocol. Parent owns size, time and source-admission limits."""

import json
import os
import sys
import time
import types
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version

_STARTED = time.monotonic()

# pysprite loads pandas, matplotlib and seaborn (and, through seaborn, scipy)
# for its GRIM summaries and plots. The SPRITE search uses none of them, yet
# loading them was nearly all of this process's start-up time and memory:
# about 13 s and 140 MiB per row on the free host. Stand-ins take their
# place. Any use of one raises, so an unexpected use ends as a library error
# and an unassessed row, never a quiet result. A library that is already
# loaded is left alone.
_NOT_LOADED = ("pandas", "matplotlib", "matplotlib.pyplot", "seaborn", "scipy")


class _NotLoaded(types.ModuleType):
    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise RuntimeError(f"{self.__name__} is not loaded in the SPRITE worker")


def _stand_in_for_unused_libraries() -> None:
    stand_ins = {name: _NotLoaded(name) for name in _NOT_LOADED}
    # `import matplotlib.pyplot as plt` reads the submodule off its parent.
    stand_ins["matplotlib"].pyplot = stand_ins["matplotlib.pyplot"]
    for name, module in stand_ins.items():
        sys.modules.setdefault(name, module)


_stand_in_for_unused_libraries()

import numpy as np  # noqa: E402
import pysprite  # noqa: E402,F401

from app.checks.forensics.grim import sprite_check  # noqa: E402


def main() -> None:
    task = json.load(sys.stdin)
    seed = task.pop("seed")
    np.random.seed(seed)
    search_started = time.monotonic()
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        result = sprite_check(**task)
    response = {
        "status": result.status,
        "library_version": version("pysprite"),
        "seed": seed,
        "worker_startup_seconds": search_started - _STARTED,
        "worker_search_seconds": time.monotonic() - search_started,
    }
    if result.possible:
        response["distribution"] = [float(value) for value in result.distribution]
    print(json.dumps(response, allow_nan=False))


if __name__ == "__main__":
    main()
