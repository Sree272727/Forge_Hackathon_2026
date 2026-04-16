import pickle
import uuid
from typing import Any, Optional

import redis.asyncio as aioredis
import ujson

from core.cache.base import BaseBackend
from core.cache.metrics import metrics
from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)


class RedisBackend(BaseBackend):
    def __init__(self) -> None:
        self.redis: Optional[aioredis.Redis] = None
        self._connection_pool: Optional[aioredis.ConnectionPool] = None

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client, creating connection if needed."""
        if not settings.CACHE_ENABLED:
            raise RuntimeError("Cache is disabled in configuration")

        if self.redis is None:
            try:
                self._connection_pool = aioredis.ConnectionPool.from_url(
                    settings.REDIS_URL,
                    max_connections=settings.CACHE_MAX_CONNECTIONS,
                    retry_on_timeout=settings.CACHE_RETRY_ON_TIMEOUT,
                    socket_timeout=settings.CACHE_SOCKET_TIMEOUT,
                    socket_connect_timeout=settings.CACHE_SOCKET_TIMEOUT,
                    health_check_interval=30,
                )
                self.redis = aioredis.Redis(connection_pool=self._connection_pool)
                # Test connection
                await self.redis.ping()  # type: ignore
                logger.info(
                    "Redis connection established successfully",
                    url=settings.REDIS_URL,
                    max_connections=settings.CACHE_MAX_CONNECTIONS,
                )
            except Exception as e:
                logger.error("Failed to connect to Redis", error=str(e))
                raise
        return self.redis

    async def get(self, key: str) -> Any:
        operation_id = str(uuid.uuid4())
        metrics.record_operation_start("get", operation_id)

        try:
            redis_client = await self._get_redis()
            result = await redis_client.get(key)

            if not result:
                metrics.record_cache_miss("redis", operation_id)
                return None

            try:
                deserialized_result = ujson.loads(result.decode("utf8"))
                metrics.record_cache_hit("redis", operation_id)
                return deserialized_result
            except (UnicodeDecodeError, ujson.JSONDecodeError):
                try:
                    deserialized_result = pickle.loads(result)
                    metrics.record_cache_hit("redis", operation_id)
                    return deserialized_result
                except Exception as e:
                    logger.warning("Failed to deserialize cached value", key=key, error=str(e))
                    metrics.record_cache_error("get", "redis", operation_id)
                    return None
        except Exception as e:
            logger.error("Cache get operation failed", key=key, error=str(e))
            metrics.record_cache_error("get", "redis", operation_id)
            return None

    async def set(self, response: Any, key: str, ttl: int = 60) -> None:
        operation_id = str(uuid.uuid4())
        metrics.record_operation_start("set", operation_id)

        try:
            redis_client = await self._get_redis()

            if isinstance(response, (dict, list)):
                serialized_response = ujson.dumps(response)
            else:
                serialized_response = pickle.dumps(response)

            await redis_client.set(name=key, value=serialized_response, ex=ttl)
            logger.debug("Cache set operation successful", key=key, ttl=ttl)
            metrics.record_cache_set("redis", operation_id, success=True)
        except Exception as e:
            logger.error("Cache set operation failed", key=key, error=str(e))
            metrics.record_cache_set("redis", operation_id, success=False)
            raise

    async def delete_startswith(self, value: str) -> None:
        operation_id = str(uuid.uuid4())
        metrics.record_operation_start("delete", operation_id)

        try:
            redis_client = await self._get_redis()
            count = 0
            async for key in redis_client.scan_iter(f"{value}::*"):
                await redis_client.delete(key)
                count += 1
            logger.debug("Cache delete operation completed", pattern=f"{value}::*", deleted_count=count)
            metrics.record_cache_delete("redis", operation_id, success=True)
        except Exception as e:
            logger.error("Cache delete operation failed", pattern=f"{value}::*", error=str(e))
            metrics.record_cache_delete("redis", operation_id, success=False)
            raise

    async def close(self) -> None:
        """Close Redis connection."""
        try:
            if self.redis:
                await self.redis.aclose()
                self.redis = None
            if self._connection_pool:
                await self._connection_pool.aclose()
                self._connection_pool = None
            logger.info("Redis connection closed successfully")
        except Exception as e:
            logger.error("Error closing Redis connection", error=str(e))
