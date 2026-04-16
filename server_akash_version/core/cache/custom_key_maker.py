import inspect
import uuid
from typing import Any, Callable, Optional

from core.cache.base import BaseKeyMaker


class CustomKeyMaker(BaseKeyMaker):
    """Enhanced key maker with tenant context support."""

    async def make(self, function: Callable, prefix: str) -> str:
        """Create cache key for function with tenant context.

        Args:
            function: Function to create key for
            prefix: Cache key prefix

        Returns:
            str: Cache key

        """
        module = inspect.getmodule(function)
        module_name = module.__name__ if module else "unknown"
        path = f"{prefix}::{module_name}.{function.__name__}"
        args = ""

        for arg in inspect.signature(function).parameters.values():
            args += arg.name

        if args:
            return f"{path}.{args}"

        return path

    def make_tenant_key(
        self,
        base_key: str,
        tenant_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        **kwargs: Any,
    ) -> str:
        """Create tenant-scoped cache key.

        Args:
            base_key: Base cache key
            tenant_id: Optional tenant ID for scoping
            user_id: Optional user ID for scoping
            **kwargs: Additional key components

        Returns:
            str: Tenant-scoped cache key

        """
        key_parts = [base_key]

        # Add tenant context if provided
        if tenant_id:
            key_parts.append(f"tenant:{tenant_id}")

        # Add user context if provided
        if user_id:
            key_parts.append(f"user:{user_id}")

        # Add additional context
        for key, value in kwargs.items():
            if value is not None:
                key_parts.append(f"{key}:{value}")

        return ":".join(key_parts)

    def make_auth_cache_key(
        self,
        key_type: str,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        resource_id: Optional[uuid.UUID] = None,
    ) -> str:
        """Create authentication-related cache key.

        Args:
            key_type: Type of auth cache (user, permissions, profile, etc.)
            tenant_id: Tenant ID for scoping
            user_id: Optional user ID
            resource_id: Optional resource ID

        Returns:
            str: Auth cache key

        """
        key_parts = [key_type, f"tenant:{tenant_id}"]

        if user_id:
            key_parts.append(f"user:{user_id}")

        if resource_id:
            key_parts.append(f"resource:{resource_id}")

        return ":".join(key_parts)

    def make_session_key(
        self,
        session_type: str,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: Optional[str] = None,
    ) -> str:
        """Create session-related cache key.

        Args:
            session_type: Type of session (token, blacklist, etc.)
            tenant_id: Tenant ID
            user_id: User ID
            session_id: Optional session identifier

        Returns:
            str: Session cache key

        """
        key_parts = [session_type, f"tenant:{tenant_id}", f"user:{user_id}"]

        if session_id:
            key_parts.append(f"session:{session_id}")

        return ":".join(key_parts)

    def make_permission_key(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        resource_type: Optional[str] = None,
        resource_id: Optional[uuid.UUID] = None,
    ) -> str:
        """Create permission cache key.

        Args:
            tenant_id: Tenant ID
            user_id: User ID
            resource_type: Optional resource type
            resource_id: Optional resource ID

        Returns:
            str: Permission cache key

        """
        key_parts = ["permissions", f"tenant:{tenant_id}", f"user:{user_id}"]

        if resource_type:
            key_parts.append(f"resource_type:{resource_type}")

        if resource_id:
            key_parts.append(f"resource:{resource_id}")

        return ":".join(key_parts)

    def extract_tenant_from_key(self, cache_key: str) -> Optional[str]:
        """Extract tenant ID from cache key.

        Args:
            cache_key: Cache key to parse

        Returns:
            Optional[str]: Tenant ID if found in key

        """
        parts = cache_key.split(":")
        for i, part in enumerate(parts):
            if part == "tenant" and i + 1 < len(parts):
                return parts[i + 1]
        return None

    def extract_user_from_key(self, cache_key: str) -> Optional[str]:
        """Extract user ID from cache key.

        Args:
            cache_key: Cache key to parse

        Returns:
            Optional[str]: User ID if found in key

        """
        parts = cache_key.split(":")
        for i, part in enumerate(parts):
            if part == "user" and i + 1 < len(parts):
                return parts[i + 1]
        return None

    def is_tenant_scoped(self, cache_key: str) -> bool:
        """Check if cache key is tenant-scoped.

        Args:
            cache_key: Cache key to check

        Returns:
            bool: True if key is tenant-scoped

        """
        return "tenant:" in cache_key

    def get_cache_tags_for_tenant(self, tenant_id: uuid.UUID) -> list[str]:
        """Get cache tags for tenant-based invalidation.

        Args:
            tenant_id: Tenant ID

        Returns:
            list[str]: Cache tags for the tenant

        """
        return [
            f"tenant:{tenant_id}",
            f"tenant:{tenant_id}:users",
            f"tenant:{tenant_id}:permissions",
            f"tenant:{tenant_id}:sessions",
        ]

    def get_cache_tags_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
        """Get cache tags for user-based invalidation.

        Args:
            tenant_id: Tenant ID
            user_id: User ID

        Returns:
            list[str]: Cache tags for the user

        """
        return [
            f"tenant:{tenant_id}:user:{user_id}",
            f"tenant:{tenant_id}:user:{user_id}:profile",
            f"tenant:{tenant_id}:user:{user_id}:permissions",
            f"tenant:{tenant_id}:user:{user_id}:sessions",
        ]
