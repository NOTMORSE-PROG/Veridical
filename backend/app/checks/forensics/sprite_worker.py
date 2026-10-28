"""F6.3 child protocol. Parent owns size, time and source-admission limits."""

import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version

import numpy as np

from app.checks.forensics.grim import sprite_check


def main() -> None:
    task = json.load(sys.stdin)
    seed = task.pop("seed")
    np.random.seed(seed)
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        result = sprite_check(**task)
    response = {"status": result.status, "library_version": version("pysprite"), "seed": seed}
    if result.possible:
        response["distribution"] = [float(value) for value in result.distribution]
    print(json.dumps(response, allow_nan=False))


if __name__ == "__main__":
    main()
