from functools import wraps
from typing import Type

from core.logging import get_logger

from .base import BaseBackend, BaseKeyMaker
from .cache_tag import CacheTag

logger = get_logger(__name__)


class CacheManager:
    def __init__(self):
        self.backend: BaseBackend | None = None
        self.key_maker: BaseKeyMaker | None = None

    def init(self, backend: Type[BaseBackend], key_maker: Type[BaseKeyMaker]) -> None:
        self.backend = backend()
        self.key_maker = key_maker()

    def cached(self, prefix: str | None = None, tag: CacheTag | None = None, ttl: int = 60, fallback_on_error: bool = True):
        def _cached(function):
            @wraps(function)
            async def __cached(*args, **kwargs):
                # If cache is not initialized, execute function directly
                if not self.backend or not self.key_maker:
                    if fallback_on_error:
                        logger.warning("Cache not initialized, executing function without caching", function=function.__name__)
                        return await function(*args, **kwargs)
                    else:
                        raise ValueError("Backend or KeyMaker not initialized")

                try:
                    key = await self.key_maker.make(
                        function=function,
                        prefix=prefix if prefix else (tag.value if tag else "default"),
                    )

                    # Try to get from cache
                    try:
                        cached_response = await self.backend.get(key=key)
                        if cached_response is not None:
                            logger.debug("Cache hit", key=key, function=function.__name__)
                            return cached_response
                    except Exception as e:
                        logger.warning(
                            "Cache get failed, falling back to function execution",
                            key=key,
                            function=function.__name__,
                            error=str(e),
                        )

                    # Execute function if not in cache or cache failed
                    logger.debug("Cache miss, executing function", key=key, function=function.__name__)
                    response = await function(*args, **kwargs)

                    # Try to cache the response
                    try:
                        await self.backend.set(response=response, key=key, ttl=ttl)
                        logger.debug("Response cached successfully", key=key, function=function.__name__)
                    except Exception as e:
                        logger.warning("Failed to cache response", key=key, function=function.__name__, error=str(e))

                    return response

                except Exception as e:
                    if fallback_on_error:
                        logger.error(
                            "Cache operation failed, falling back to function execution", function=function.__name__, error=str(e)
                        )
                        return await function(*args, **kwargs)
                    else:
                        raise

            return __cached

        return _cached

    async def remove_by_tag(self, tag: CacheTag) -> None:
        if not self.backend:
            raise ValueError("Backend not initialized")
        await self.backend.delete_startswith(value=tag.value)

    async def remove_by_prefix(self, prefix: str) -> None:
        if not self.backend:
            raise ValueError("Backend not initialized")
        await self.backend.delete_startswith(value=prefix)


Cache = CacheManager()
