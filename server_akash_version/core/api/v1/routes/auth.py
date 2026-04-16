"""Authentication API routes."""

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.schemas.auth import AuthResponse, GoogleAuthRequest, RefreshTokenRequest, TokenResponse, UserResponse
from core.config import settings
from core.database.session import get_db_session
from core.exceptions.base import AuthenticationError
from core.logging import get_logger
from core.services.auth import AuthService

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


async def get_auth_service(session: AsyncSession = Depends(get_db_session)) -> AuthService:
    """Dependency to get auth service."""
    return AuthService(session)


@router.get("/google/url")
async def get_google_auth_url(redirect_uri: Optional[str] = None) -> dict:
    """Get the Google OAuth login URL.

    Args:
        redirect_uri: Optional custom redirect URI. Defaults to configured GOOGLE_REDIRECT_URI.

    Returns:
        dict with the Google OAuth login URL

    """
    final_redirect_uri = redirect_uri or settings.GOOGLE_REDIRECT_URI

    google_auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={quote(final_redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope=openid%20email%20profile"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    return {"url": google_auth_url, "redirect_uri": final_redirect_uri}


@router.post("/google/callback", response_model=AuthResponse)
async def google_callback(
    request: GoogleAuthRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    """Exchange Google authorization code for JWT tokens.

    Args:
        request: Contains the authorization code and redirect_uri used in the OAuth flow

    Returns:
        AuthResponse with user details and JWT tokens

    """
    user = await auth_service.authenticate_with_google_code(
        code=request.code,
        redirect_uri=request.redirect_uri,
    )

    tokens = AuthService.create_tokens(user.id, user.email)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        tokens=tokens,
        message="Google authentication successful",
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    request: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Refresh access/refresh tokens using a valid refresh token."""
    payload = AuthService.verify_refresh_token(request.refresh_token)
    user = await auth_service.get_user_by_id(payload.sub)

    if not user or not user.is_active:
        raise AuthenticationError("Invalid refresh token")

    return AuthService.create_tokens(user.id, user.email)
