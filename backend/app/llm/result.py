"""Internal generation provenance, kept outside the model's JSON (F1.3/F8)."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GenerationResult:
    """The original parsed payload and optional provider-reported version.

    Payload shape validation still belongs to the existing prompt-specific
    validators. A valid JSON nonobject must not become a transport retry here.
    """

    payload: Any
    model_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_version, str) or not self.model_version.strip():
            object.__setattr__(self, "model_version", None)


def generation_result(value: Any) -> GenerationResult:
    """Accept existing scripted transports without inferring metadata from JSON."""
    return value if isinstance(value, GenerationResult) else GenerationResult(payload=value)
