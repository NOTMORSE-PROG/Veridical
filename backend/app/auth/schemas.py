from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    # 8 = OWASP's own minimum baseline (same security-engineering-constant
    # class as security.py's hasher params, not a business threshold an
    # instructor would ever configure -- see that module's own docstring).
    new_password: str = Field(min_length=8)


class InstructorOut(BaseModel):
    id: int
    email: str
    display_name: str
    onboarding_dismissed_at: datetime | None = None

    model_config = {"from_attributes": True}
