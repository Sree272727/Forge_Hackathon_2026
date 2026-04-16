from functools import lru_cache
from typing import Any, Optional

from fastapi import Depends

from core.cache import Cache


@lru_cache()
def get_cache_manager():
    """Get the global cache manager instance."""
    return Cache


# Define dependency at module level to avoid function call in default
_get_cache_manager_dep = Depends(get_cache_manager)


async def get_cache(cache_manager=None):
    """FastAPI dependency to get cache manager."""
    if cache_manager is None:
        cache_manager = _get_cache_manager_dep
    return cache_manager


def cache_available(cache_manager=None) -> bool:
    """Check if cache is available and properly initialized."""
    if cache_manager is None:
        cache_manager = get_cache_manager()
    return cache_manager.backend is not None and cache_manager.key_maker is not None


async def safe_cache_get(key: str, cache_manager=None) -> Optional[Any]:
    """Safely get value from cache with error handling."""
    if cache_manager is None:
        cache_manager = get_cache_manager()

    if not cache_available(cache_manager):
        return None

    try:
        if cache_manager.backend is None:
            return None
        return await cache_manager.backend.get(key)
    except Exception:
        # Log error but don't raise - graceful degradation
        return None


async def safe_cache_set(key: str, value: Any, ttl: int = 60, cache_manager=None) -> bool:
    """Safely set value in cache with error handling."""
    if cache_manager is None:
        cache_manager = get_cache_manager()

    if not cache_available(cache_manager):
        return False

    try:
        if cache_manager.backend is None:
            return False
        await cache_manager.backend.set(response=value, key=key, ttl=ttl)
        return True
    except Exception:
        # Log error but don't raise - graceful degradation
        return False


# FastAPI dependency versions
async def cache_available_dep(cache_manager=_get_cache_manager_dep) -> bool:
    """FastAPI dependency version of cache_available."""
    return cache_available(cache_manager)


async def safe_cache_get_dep(key: str, cache_manager=_get_cache_manager_dep) -> Optional[Any]:
    """FastAPI dependency version of safe_cache_get."""
    return await safe_cache_get(key, cache_manager)


async def safe_cache_set_dep(key: str, value: Any, ttl: int = 60, cache_manager=_get_cache_manager_dep) -> bool:
    """FastAPI dependency version of safe_cache_set."""
    return await safe_cache_set(key, value, ttl, cache_manager)
