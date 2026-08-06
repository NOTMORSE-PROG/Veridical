from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class InstructorOut(BaseModel):
    id: int
    email: str
    display_name: str
    onboarding_dismissed_at: datetime | None = None

    model_config = {"from_attributes": True}
