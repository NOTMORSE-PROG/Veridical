"""F6.3 child protocol. Parent owns size, time and source-admission limits."""

import json
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version

_STARTED = time.monotonic()

import numpy as np  # noqa: E402

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
