from pydantic import BaseModel, EmailStr, Field


class RequestCodeIn(BaseModel):
    # EmailStr rejects anything that isn't a well-formed email address.
    email: EmailStr


class VerifyCodeIn(BaseModel):
    email: EmailStr
    # Exactly six digits.
    code: str = Field(pattern=r"^\s*\d{6}\s*$")


class MessageOut(BaseModel):
    message: str


class CodeSentOut(MessageOut):
    # A number the frontend can show directly ("expires in 10 minutes").
    expires_in_minutes: int
