"""Tenant-aware cache operations with invalidation patterns."""

from __future__ import annotations

import uuid
from typing import Any, List, Optional

from opentelemetry import trace

from core.cache.custom_key_maker import CustomKeyMaker
from core.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)


class TenantCacheManager:
    """Cache manager with tenant context and invalidation support."""

    def __init__(self, cache_backend, key_maker: Optional[CustomKeyMaker] = None) -> None:
        """Initialize tenant cache manager.

        Args:
            cache_backend: Cache backend instance
            key_maker: Custom key maker instance

        """
        self.cache = cache_backend
        self.key_maker = key_maker or CustomKeyMaker()

    async def get_tenant_scoped(
        self,
        base_key: str,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        **kwargs: Any,
    ) -> Optional[Any]:
        """Get value from cache with tenant scoping.

        Args:
            base_key: Base cache key
            tenant_id: Tenant ID for scoping
            user_id: Optional user ID for scoping
            **kwargs: Additional key components

        Returns:
            Optional[Any]: Cached value if found

        """
        with tracer.start_as_current_span("tenant_cache_get") as span:
            cache_key = self.key_maker.make_tenant_key(
                base_key=base_key,
                tenant_id=tenant_id,
                user_id=user_id,
                **kwargs,
            )

            span.set_attribute("cache.key", cache_key)
            span.set_attribute("cache.tenant_id", str(tenant_id))

            try:
                value = await self.cache.get(cache_key)

                span.set_attribute("cache.hit", value is not None)

                if value is not None:
                    logger.debug(
                        "Cache hit",
                        cache_key=cache_key,
                        tenant_id=str(tenant_id),
                        user_id=str(user_id) if user_id else None,
                    )
                else:
                    logger.debug(
                        "Cache miss",
                        cache_key=cache_key,
                        tenant_id=str(tenant_id),
                        user_id=str(user_id) if user_id else None,
                    )

                return value

            except Exception as e:
                logger.error(
                    "Cache get error",
                    cache_key=cache_key,
                    tenant_id=str(tenant_id),
                    error=str(e),
                    exc_info=True,
                )
                span.record_exception(e)
                return None

    async def set_tenant_scoped(
        self,
        base_key: str,
        value: Any,
        tenant_id: uuid.UUID,
        ttl: int = 300,
        user_id: Optional[uuid.UUID] = None,
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> bool:
        """Set value in cache with tenant scoping.

        Args:
            base_key: Base cache key
            value: Value to cache
            tenant_id: Tenant ID for scoping
            ttl: Time to live in seconds
            user_id: Optional user ID for scoping
            tags: Optional cache tags for invalidation
            **kwargs: Additional key components

        Returns:
            bool: True if set successfully

        """
        with tracer.start_as_current_span("tenant_cache_set") as span:
            cache_key = self.key_maker.make_tenant_key(
                base_key=base_key,
                tenant_id=tenant_id,
                user_id=user_id,
                **kwargs,
            )

            span.set_attribute("cache.key", cache_key)
            span.set_attribute("cache.tenant_id", str(tenant_id))
            span.set_attribute("cache.ttl", ttl)

            # Add tenant-based tags for invalidation
            if not tags:
                tags = []
            tags.extend(self.key_maker.get_cache_tags_for_tenant(tenant_id))
            if user_id:
                tags.extend(self.key_maker.get_cache_tags_for_user(tenant_id, user_id))

            try:
                await self.cache.set(
                    key=cache_key,
                    response=value,
                    ttl=ttl,
                )

                span.set_attribute("cache.set_success", True)

                logger.debug(
                    "Cache set successful",
                    cache_key=cache_key,
                    tenant_id=str(tenant_id),
                    user_id=str(user_id) if user_id else None,
                    ttl=ttl,
                    tags=tags,
                )

                return True

            except Exception as e:
                logger.error(
                    "Cache set error",
                    cache_key=cache_key,
                    tenant_id=str(tenant_id),
                    error=str(e),
                    exc_info=True,
                )
                span.set_attribute("cache.set_success", False)
                span.record_exception(e)
                return False

    async def delete_tenant_scoped(
        self,
        base_key: str,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        **kwargs: Any,
    ) -> bool:
        """Delete value from cache with tenant scoping.

        Args:
            base_key: Base cache key
            tenant_id: Tenant ID for scoping
            user_id: Optional user ID for scoping
            **kwargs: Additional key components

        Returns:
            bool: True if deleted successfully

        """
        with tracer.start_as_current_span("tenant_cache_delete") as span:
            cache_key = self.key_maker.make_tenant_key(
                base_key=base_key,
                tenant_id=tenant_id,
                user_id=user_id,
                **kwargs,
            )

            span.set_attribute("cache.key", cache_key)
            span.set_attribute("cache.tenant_id", str(tenant_id))

            try:
                # Note: Assuming cache backend has delete method
                # If not available, implement cache.delete() method
                if hasattr(self.cache, "delete"):
                    success = await self.cache.delete(cache_key)
                else:
                    # Fallback: set with immediate expiration
                    success = await self.cache.set(
                        key=cache_key,
                        response=None,
                        ttl=1,
                    )

                span.set_attribute("cache.delete_success", success)

                logger.debug(
                    "Cache delete",
                    cache_key=cache_key,
                    tenant_id=str(tenant_id),
                    user_id=str(user_id) if user_id else None,
                    success=success,
                )

                return success

            except Exception as e:
                logger.error(
                    "Cache delete error",
                    cache_key=cache_key,
                    tenant_id=str(tenant_id),
                    error=str(e),
                    exc_info=True,
                )
                span.record_exception(e)
                return False

    async def invalidate_tenant_cache(
        self,
        tenant_id: uuid.UUID,
        cache_types: Optional[List[str]] = None,
    ) -> int:
        """Invalidate all cache entries for a tenant.

        Args:
            tenant_id: Tenant ID
            cache_types: Optional list of cache types to invalidate

        Returns:
            int: Number of cache entries invalidated

        """
        with tracer.start_as_current_span("invalidate_tenant_cache") as span:
            span.set_attribute("cache.tenant_id", str(tenant_id))

            if not cache_types:
                cache_types = ["user", "permissions", "profile", "session"]

            span.set_attribute("cache.types", str(cache_types))

            invalidated_count = 0

            try:
                # Generate patterns to invalidate
                patterns_to_invalidate = []
                for cache_type in cache_types:
                    patterns_to_invalidate.append(f"{cache_type}:tenant:{tenant_id}:*")
                    patterns_to_invalidate.append(f"*:tenant:{tenant_id}:*")

                # If cache backend supports pattern deletion
                if hasattr(self.cache, "delete_pattern"):
                    for pattern in patterns_to_invalidate:
                        try:
                            count = await self.cache.delete_pattern(pattern)
                            invalidated_count += count
                        except Exception as e:
                            logger.warning(
                                "Failed to delete cache pattern",
                                pattern=pattern,
                                tenant_id=str(tenant_id),
                                error=str(e),
                            )

                span.set_attribute("cache.invalidated_count", invalidated_count)

                logger.info(
                    "Tenant cache invalidated",
                    tenant_id=str(tenant_id),
                    cache_types=cache_types,
                    invalidated_count=invalidated_count,
                )

                return invalidated_count

            except Exception as e:
                logger.error(
                    "Tenant cache invalidation error",
                    tenant_id=str(tenant_id),
                    error=str(e),
                    exc_info=True,
                )
                span.record_exception(e)
                return 0

    async def invalidate_user_cache(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        cache_types: Optional[List[str]] = None,
    ) -> int:
        """Invalidate all cache entries for a specific user.

        Args:
            tenant_id: Tenant ID
            user_id: User ID
            cache_types: Optional list of cache types to invalidate

        Returns:
            int: Number of cache entries invalidated

        """
        with tracer.start_as_current_span("invalidate_user_cache") as span:
            span.set_attribute("cache.tenant_id", str(tenant_id))
            span.set_attribute("cache.user_id", str(user_id))

            if not cache_types:
                cache_types = ["user", "permissions", "profile", "session"]

            span.set_attribute("cache.types", str(cache_types))

            invalidated_count = 0

            try:
                # Generate patterns to invalidate
                patterns_to_invalidate = []
                for cache_type in cache_types:
                    patterns_to_invalidate.extend(
                        [
                            f"{cache_type}:tenant:{tenant_id}:user:{user_id}*",
                            f"*:tenant:{tenant_id}:user:{user_id}*",
                        ]
                    )

                # If cache backend supports pattern deletion
                if hasattr(self.cache, "delete_pattern"):
                    for pattern in patterns_to_invalidate:
                        try:
                            count = await self.cache.delete_pattern(pattern)
                            invalidated_count += count
                        except Exception as e:
                            logger.warning(
                                "Failed to delete cache pattern",
                                pattern=pattern,
                                tenant_id=str(tenant_id),
                                user_id=str(user_id),
                                error=str(e),
                            )

                span.set_attribute("cache.invalidated_count", invalidated_count)

                logger.info(
                    "User cache invalidated",
                    tenant_id=str(tenant_id),
                    user_id=str(user_id),
                    cache_types=cache_types,
                    invalidated_count=invalidated_count,
                )

                return invalidated_count

            except Exception as e:
                logger.error(
                    "User cache invalidation error",
                    tenant_id=str(tenant_id),
                    user_id=str(user_id),
                    error=str(e),
                    exc_info=True,
                )
                span.record_exception(e)
                return 0

    async def get_auth_cache(
        self,
        key_type: str,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        resource_id: Optional[uuid.UUID] = None,
    ) -> Optional[Any]:
        """Get authentication-related cache entry.

        Args:
            key_type: Type of auth cache (user, permissions, profile, etc.)
            tenant_id: Tenant ID for scoping
            user_id: Optional user ID
            resource_id: Optional resource ID

        Returns:
            Optional[Any]: Cached value if found

        """
        cache_key = self.key_maker.make_auth_cache_key(
            key_type=key_type,
            tenant_id=tenant_id,
            user_id=user_id,
            resource_id=resource_id,
        )

        try:
            return await self.cache.get(cache_key)
        except Exception as e:
            logger.error(
                "Auth cache get error",
                cache_key=cache_key,
                key_type=key_type,
                tenant_id=str(tenant_id),
                error=str(e),
            )
            return None

    async def set_auth_cache(
        self,
        key_type: str,
        value: Any,
        tenant_id: uuid.UUID,
        ttl: int = 600,
        user_id: Optional[uuid.UUID] = None,
        resource_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """Set authentication-related cache entry.

        Args:
            key_type: Type of auth cache (user, permissions, profile, etc.)
            value: Value to cache
            tenant_id: Tenant ID for scoping
            ttl: Time to live in seconds
            user_id: Optional user ID
            resource_id: Optional resource ID

        Returns:
            bool: True if set successfully

        """
        cache_key = self.key_maker.make_auth_cache_key(
            key_type=key_type,
            tenant_id=tenant_id,
            user_id=user_id,
            resource_id=resource_id,
        )

        try:
            await self.cache.set(
                key=cache_key,
                response=value,
                ttl=ttl,
            )
            return True
        except Exception as e:
            logger.error(
                "Auth cache set error",
                cache_key=cache_key,
                key_type=key_type,
                tenant_id=str(tenant_id),
                error=str(e),
            )
            return False

    def is_tenant_isolated(self, cache_key: str) -> bool:
        """Check if cache key is tenant-isolated.

        Args:
            cache_key: Cache key to check

        Returns:
            bool: True if key is tenant-isolated

        """
        return self.key_maker.is_tenant_scoped(cache_key)

    def extract_tenant_context(self, cache_key: str) -> Optional[dict[str, str]]:
        """Extract tenant context from cache key.

        Args:
            cache_key: Cache key to parse

        Returns:
            Optional[Dict[str, str]]: Tenant context if found

        """
        tenant_id = self.key_maker.extract_tenant_from_key(cache_key)
        user_id = self.key_maker.extract_user_from_key(cache_key)

        if tenant_id:
            context = {"tenant_id": tenant_id}
            if user_id:
                context["user_id"] = user_id
            return context

        return None


# Global tenant cache manager instance
tenant_cache: Optional[TenantCacheManager] = None


def get_tenant_cache() -> Optional[TenantCacheManager]:
    """Get global tenant cache manager instance.

    Returns:
        Optional[TenantCacheManager]: Tenant cache manager if available

    """
    global tenant_cache
    if tenant_cache is None:
        try:
            from core.cache import Cache

            if Cache.backend is not None:
                tenant_cache = TenantCacheManager(
                    cache_backend=Cache.backend,
                    key_maker=CustomKeyMaker(),
                )
        except Exception as e:
            logger.warning("Failed to initialize tenant cache", error=str(e))

    return tenant_cache
