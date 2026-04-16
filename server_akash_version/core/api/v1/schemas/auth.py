"""Authentication schemas for request/response validation."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserResponse(BaseModel):
    """Schema for user response."""

    id: str
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    profile_picture: Optional[str] = None
    auth_provider: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """Schema for JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    """Schema for JWT token payload."""

    sub: str
    email: str
    exp: int
    iat: int
    type: str


class GoogleAuthRequest(BaseModel):
    """Schema for Google OAuth callback with authorization code."""

    code: str
    redirect_uri: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    """Schema for refreshing access tokens using a refresh token."""

    refresh_token: str


class AuthResponse(BaseModel):
    """Schema for authentication response with user and tokens."""

    user: UserResponse
    tokens: TokenResponse
    message: str = "Authentication successful"
