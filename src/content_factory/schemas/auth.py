from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    client_id: str = Field(max_length=200)
    client_secret: str = Field(max_length=500)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
