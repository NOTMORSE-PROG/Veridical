from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class InstructorOut(BaseModel):
    id: int
    email: str
    display_name: str

    model_config = {"from_attributes": True}
