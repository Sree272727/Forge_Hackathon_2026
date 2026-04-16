"""Authentication service for handling user auth, JWT tokens, and Google OAuth."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.schemas.auth import TokenPayload, TokenResponse
from core.config import settings
from core.exceptions.base import AuthenticationError, ValidationError
from core.logging import get_logger
from core.models.user import AuthProvider, User

logger = get_logger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Google OAuth endpoints
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class AuthService:
    """Service for handling authentication operations."""

    def __init__(self, session: AsyncSession):
        """Initialize auth service with database session."""
        self.session = session

    # Password utilities
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return pwd_context.verify(plain_password, hashed_password)

    # JWT Token utilities
    @staticmethod
    def create_access_token(user_id: str, email: str) -> str:
        """Create a JWT access token."""
        now = datetime.now(timezone.utc)
        expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

        payload = {
            "sub": user_id,
            "email": email,
            "exp": int(expire.timestamp()),
            "iat": int(now.timestamp()),
            "type": "access",
        }

        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    @staticmethod
    def create_refresh_token(user_id: str, email: str) -> str:
        """Create a JWT refresh token."""
        now = datetime.now(timezone.utc)
        expire = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

        payload = {
            "sub": user_id,
            "email": email,
            "exp": int(expire.timestamp()),
            "iat": int(now.timestamp()),
            "type": "refresh",
        }

        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    @staticmethod
    def create_tokens(user_id: str, email: str) -> TokenResponse:
        """Create both access and refresh tokens."""
        access_token = AuthService.create_access_token(user_id, email)
        refresh_token = AuthService.create_refresh_token(user_id, email)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    @staticmethod
    def decode_token(token: str) -> TokenPayload:
        """Decode and validate a JWT token."""
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            print(f"Decoded token payload: {payload}")
            return TokenPayload(**payload)
        except jwt.ExpiredSignatureError as e:
            print(f"Token expired error: {str(e)}")
            raise AuthenticationError("Token has expired") from e
        except jwt.InvalidTokenError as e:
            print(f"Invalid token error: {str(e)}")
            raise AuthenticationError(f"Invalid token: {str(e)}") from e

    @staticmethod
    def verify_access_token(token: str) -> TokenPayload:
        """Verify an access token."""
        payload = AuthService.decode_token(token)
        if payload.type != "access":
            raise AuthenticationError("Invalid token type")
        return payload

    @staticmethod
    def verify_refresh_token(token: str) -> TokenPayload:
        """Verify a refresh token."""
        payload = AuthService.decode_token(token)
        if payload.type != "refresh":
            raise AuthenticationError("Invalid token type")
        return payload

    # User operations
    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email."""
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_user_by_google_id(self, google_id: str) -> Optional[User]:
        """Get a user by Google ID."""
        result = await self.session.execute(select(User).where(User.google_id == google_id))
        return result.scalar_one_or_none()

    async def create_user(
        self,
        email: str,
        password: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        full_name: Optional[str] = None,
        profile_picture: Optional[str] = None,
        auth_provider: str = AuthProvider.LOCAL.value,
        google_id: Optional[str] = None,
        is_verified: bool = False,
    ) -> User:
        """Create a new user."""
        # Check if user already exists
        existing_user = await self.get_user_by_email(email)
        if existing_user:
            raise ValidationError(f"User with email {email} already exists")

        user = User(
            email=email,
            password_hash=self.hash_password(password) if password else None,
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            profile_picture=profile_picture,
            auth_provider=auth_provider,
            google_id=google_id,
            is_verified=is_verified,
        )

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)

        logger.info("User created", user_id=user.id, email=email, provider=auth_provider)
        return user

    async def update_last_login(self, user: User) -> None:
        """Update the last login timestamp for a user."""
        user.last_login_at = datetime.utcnow()
        await self.session.flush()

    # Authentication methods
    async def authenticate_with_password(self, email: str, password: str) -> User:
        """Authenticate a user with email and password."""
        user = await self.get_user_by_email(email)

        if not user:
            raise AuthenticationError("Invalid email or password")

        if not user.password_hash:
            raise AuthenticationError("This account uses social login. Please sign in with Google.")

        if not self.verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid email or password")

        if not user.is_active:
            raise AuthenticationError("Account is disabled")

        await self.update_last_login(user)
        logger.info("User authenticated with password", user_id=user.id, email=email)

        return user

    async def authenticate_with_google_code(self, code: str, redirect_uri: Optional[str] = None) -> User:
        """Authenticate a user with Google OAuth authorization code."""
        # Exchange code for tokens
        token_data = await self._exchange_google_code(code, redirect_uri)
        access_token = token_data.get("access_token")

        if not access_token:
            raise AuthenticationError("Failed to obtain access token from Google")

        # Get user info from Google
        user_info = await self._get_google_user_info(access_token)

        return await self._handle_google_user(user_info)

    async def authenticate_with_google_token(self, id_token: str) -> User:
        """Authenticate a user with Google ID token (for mobile/frontend flows)."""
        # Verify the ID token with Google
        user_info = await self._verify_google_id_token(id_token)

        return await self._handle_google_user(user_info)

    async def _exchange_google_code(self, code: str, redirect_uri: Optional[str] = None) -> dict:
        """Exchange authorization code for Google tokens."""
        final_redirect_uri = redirect_uri or settings.GOOGLE_REDIRECT_URI
        logger.info(
            "Exchanging Google code",
            redirect_uri=final_redirect_uri,
            client_id=settings.GOOGLE_CLIENT_ID[:20] + "...",
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": final_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )

            if response.status_code != 200:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"error": response.text}
                logger.error(
                    "Google token exchange failed",
                    status=response.status_code,
                    error=error_data,
                    redirect_uri=final_redirect_uri,
                )
                error_msg = error_data.get("error_description") or error_data.get("error") or "Failed to authenticate with Google"
                raise AuthenticationError(f"Google authentication failed: {error_msg}")

            return response.json()

    async def _get_google_user_info(self, access_token: str) -> dict:
        """Get user information from Google using access token."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                logger.error("Google user info request failed", status=response.status_code)
                raise AuthenticationError("Failed to get user info from Google")

            return response.json()

    async def _verify_google_id_token(self, id_token: str) -> dict:
        """Verify Google ID token and extract user info."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{GOOGLE_TOKEN_INFO_URL}?id_token={id_token}")

            if response.status_code != 200:
                logger.error("Google ID token verification failed", status=response.status_code)
                raise AuthenticationError("Invalid Google ID token")

            data = response.json()

            # Verify the token is for our app
            if data.get("aud") != settings.GOOGLE_CLIENT_ID:
                raise AuthenticationError("Invalid token audience")

            return data

    async def _handle_google_user(self, user_info: dict) -> User:
        """Handle Google user - create or update user record."""
        google_id = user_info.get("sub")
        email = user_info.get("email")

        if not email:
            raise AuthenticationError("Email not provided by Google")

        # Try to find user by Google ID first
        user = await self.get_user_by_google_id(google_id)

        if not user:
            # Try to find by email
            user = await self.get_user_by_email(email)

            if user:
                # Link Google account to existing user
                user.google_id = google_id
                user.auth_provider = AuthProvider.GOOGLE.value
                if not user.profile_picture:
                    user.profile_picture = user_info.get("picture")
                logger.info("Linked Google account to existing user", user_id=user.id, email=email)
            else:
                # Create new user
                user = await self.create_user(
                    email=email,
                    first_name=user_info.get("given_name"),
                    last_name=user_info.get("family_name"),
                    full_name=user_info.get("name"),
                    profile_picture=user_info.get("picture"),
                    auth_provider=AuthProvider.GOOGLE.value,
                    google_id=google_id,
                    is_verified=user_info.get("email_verified", False),
                )

        if not user.is_active:
            raise AuthenticationError("Account is disabled")

        await self.update_last_login(user)
        logger.info("User authenticated with Google", user_id=user.id, email=email)

        return user

    async def refresh_tokens(self, refresh_token: str) -> TokenResponse:
        """Refresh access token using refresh token."""
        payload = self.verify_refresh_token(refresh_token)

        # Verify user still exists and is active
        user = await self.get_user_by_id(payload.sub)
        if not user:
            raise AuthenticationError("User not found")
        if not user.is_active:
            raise AuthenticationError("Account is disabled")

        return self.create_tokens(user.id, user.email)
