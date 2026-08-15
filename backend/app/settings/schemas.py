from datetime import datetime

from pydantic import BaseModel


class ThresholdsOut(BaseModel):
    ready_min_score: float
    not_ready_max_score: float
    escalation_agreement_threshold: float
    # F8.9 (instructor-configurable thresholds) is still pending adviser
    # input (FEATURES.md §10 Q2) -- always True today; carried as a real
    # field (not a hardcoded UI string) so the day F8.9 ships, this flips
    # to reality instead of needing a second place remembered and edited.
    editable: bool = False


class PromptVersionOut(BaseModel):
    prompt_type: str
    prompt_version: str
    model: str
    # The real audit-log row this was read from -- "as of" honesty, not a
    # static claim (a version could change between page loads).
    observed_at: datetime


class SettingsOut(BaseModel):
    thresholds: ThresholdsOut
    prompt_versions: list[PromptVersionOut]
