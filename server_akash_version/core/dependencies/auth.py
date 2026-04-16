"""Authentication dependencies for protected endpoints."""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_db_session
from core.exceptions.base import AuthenticationError
from core.models.user import User
from core.services.auth import AuthService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Get current authenticated user from Bearer token."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Missing or invalid authorization header")

    payload = AuthService.verify_access_token(credentials.credentials)
    auth_service = AuthService(session)
    user = await auth_service.get_user_by_id(payload.sub)

    if not user:
        raise AuthenticationError("Authenticated user not found")
    if not user.is_active:
        raise AuthenticationError("User account is inactive")

    return user
