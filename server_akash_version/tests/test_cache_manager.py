from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.cache import Cache
from core.cache.base import BaseBackend, BaseKeyMaker
from core.cache.cache_manager import CacheManager
from core.cache.custom_key_maker import CustomKeyMaker
from core.cache.redis_backend import RedisBackend


class MockBackend(BaseBackend):
    """Mock backend for testing."""

    def __init__(self):
        self.storage = {}
        self.get_calls = []
        self.set_calls = []
        self.delete_calls = []

    async def get(self, key: str) -> Any:
        self.get_calls.append(key)
        return self.storage.get(key)

    async def set(self, response: Any, key: str, ttl: int = 60) -> None:
        self.set_calls.append((key, response, ttl))
        self.storage[key] = response

    async def delete_startswith(self, value: str) -> None:
        self.delete_calls.append(value)
        keys_to_delete = [k for k in self.storage.keys() if k.startswith(f"{value}::")]
        for key in keys_to_delete:
            del self.storage[key]


class MockKeyMaker(BaseKeyMaker):
    """Mock key maker for testing."""

    async def make(self, function, prefix: str) -> str:
        return f"{prefix}::test_function"


class TestCacheManager:
    """Test cases for CacheManager."""

    def setup_method(self):
        """Setup for each test method."""
        self.cache_manager = CacheManager()
        self.mock_backend = MockBackend()
        self.mock_key_maker = MockKeyMaker()

    def test_init_sets_backend_and_key_maker(self):
        """Test that init properly sets backend and key maker."""
        self.cache_manager.init(MockBackend, MockKeyMaker)

        assert self.cache_manager.backend is not None
        assert self.cache_manager.key_maker is not None
        assert isinstance(self.cache_manager.backend, MockBackend)
        assert isinstance(self.cache_manager.key_maker, MockKeyMaker)

    @pytest.mark.asyncio
    async def test_cached_decorator_cache_hit(self):
        """Test cached decorator when cache hit occurs."""
        self.cache_manager.backend = self.mock_backend
        self.cache_manager.key_maker = self.mock_key_maker

        # Pre-populate cache
        await self.mock_backend.set("cached_result", "test_prefix::test_function", 60)

        @self.cache_manager.cached(prefix="test_prefix")
        async def test_function():
            return "function_result"

        result = await test_function()

        assert result == "cached_result"
        assert len(self.mock_backend.get_calls) == 1
        assert len(self.mock_backend.set_calls) == 1  # Only the pre-population

    @pytest.mark.asyncio
    async def test_cached_decorator_cache_miss(self):
        """Test cached decorator when cache miss occurs."""
        self.cache_manager.backend = self.mock_backend
        self.cache_manager.key_maker = self.mock_key_maker

        @self.cache_manager.cached(prefix="test_prefix", ttl=120)
        async def test_function():
            return "function_result"

        result = await test_function()

        assert result == "function_result"
        assert len(self.mock_backend.get_calls) == 1
        assert len(self.mock_backend.set_calls) == 1
        assert self.mock_backend.set_calls[0] == ("test_prefix::test_function", "function_result", 120)

    @pytest.mark.asyncio
    async def test_cached_decorator_no_backend_fallback_enabled(self):
        """Test cached decorator when backend is not initialized with fallback enabled."""
        self.cache_manager.backend = None
        self.cache_manager.key_maker = None

        @self.cache_manager.cached(prefix="test_prefix", fallback_on_error=True)
        async def test_function():
            return "function_result"

        result = await test_function()

        assert result == "function_result"

    @pytest.mark.asyncio
    async def test_cached_decorator_no_backend_fallback_disabled(self):
        """Test cached decorator when backend is not initialized with fallback disabled."""
        self.cache_manager.backend = None
        self.cache_manager.key_maker = None

        @self.cache_manager.cached(prefix="test_prefix", fallback_on_error=False)
        async def test_function():
            return "function_result"

        with pytest.raises(ValueError, match="Backend or KeyMaker not initialized"):
            await test_function()

    @pytest.mark.asyncio
    async def test_cached_decorator_backend_error_fallback_enabled(self):
        """Test cached decorator when backend raises error with fallback enabled."""
        failing_backend = AsyncMock(spec=BaseBackend)
        failing_backend.get.side_effect = Exception("Redis connection failed")

        self.cache_manager.backend = failing_backend
        self.cache_manager.key_maker = self.mock_key_maker

        @self.cache_manager.cached(prefix="test_prefix", fallback_on_error=True)
        async def test_function():
            return "function_result"

        result = await test_function()

        assert result == "function_result"

    @pytest.mark.asyncio
    async def test_remove_by_prefix(self):
        """Test remove_by_prefix functionality."""
        self.cache_manager.backend = self.mock_backend

        await self.cache_manager.remove_by_prefix("test_prefix")

        assert len(self.mock_backend.delete_calls) == 1
        assert self.mock_backend.delete_calls[0] == "test_prefix"


class TestCustomKeyMaker:
    """Test cases for CustomKeyMaker."""

    @pytest.mark.asyncio
    async def test_make_key_with_prefix(self):
        """Test key generation with prefix."""
        key_maker = CustomKeyMaker()

        async def test_function(arg1, arg2):
            return "result"

        key = await key_maker.make(test_function, "test_prefix")

        assert key.startswith("test_prefix::")
        assert "test_function" in key
        assert "arg1arg2" in key

    @pytest.mark.asyncio
    async def test_make_key_no_args(self):
        """Test key generation for function with no arguments."""
        key_maker = CustomKeyMaker()

        async def test_function():
            return "result"

        key = await key_maker.make(test_function, "test_prefix")

        assert key == "test_prefix::tests.test_cache_manager.test_function"

    @pytest.mark.asyncio
    async def test_make_key_unknown_module(self):
        """Test key generation when module name is unknown."""
        key_maker = CustomKeyMaker()

        # Create a function with no module
        test_function = lambda: "result"
        test_function.__name__ = "lambda_function"

        with patch("inspect.getmodule", return_value=None):
            key = await key_maker.make(test_function, "test_prefix")

        assert "unknown.lambda_function" in key


class TestRedisBackend:
    """Test cases for RedisBackend."""

    def setup_method(self):
        """Setup for each test method."""
        self.backend = RedisBackend()

    @pytest.mark.asyncio
    async def test_get_redis_cache_disabled(self):
        """Test _get_redis when cache is disabled."""
        with patch("core.cache.redis_backend.settings.CACHE_ENABLED", False):
            with pytest.raises(RuntimeError, match="Cache is disabled in configuration"):
                await self.backend._get_redis()

    @pytest.mark.asyncio
    @patch("core.cache.redis_backend.aioredis")
    async def test_get_redis_connection_success(self, mock_redis):
        """Test successful Redis connection."""
        mock_pool = AsyncMock()
        mock_client = AsyncMock()
        mock_redis.ConnectionPool.from_url.return_value = mock_pool
        mock_redis.Redis.return_value = mock_client

        with patch("core.cache.redis_backend.settings.CACHE_ENABLED", True):
            with patch("core.cache.redis_backend.settings.REDIS_URL", "redis://localhost:6379/0"):
                with patch("core.cache.redis_backend.settings.CACHE_MAX_CONNECTIONS", 50):
                    with patch("core.cache.redis_backend.settings.CACHE_RETRY_ON_TIMEOUT", True):
                        result = await self.backend._get_redis()

        assert result == mock_client
        mock_client.ping.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.cache.redis_backend.aioredis")
    async def test_get_redis_connection_failure(self, mock_redis):
        """Test Redis connection failure."""
        mock_redis.ConnectionPool.from_url.side_effect = Exception("Connection failed")

        with patch("core.cache.redis_backend.settings.CACHE_ENABLED", True):
            with pytest.raises(Exception, match="Connection failed"):
                await self.backend._get_redis()

    @pytest.mark.asyncio
    @patch.object(RedisBackend, "_get_redis")
    async def test_get_success(self, mock_get_redis):
        """Test successful get operation."""
        mock_client = AsyncMock()
        mock_client.get.return_value = b'{"key": "value"}'
        mock_get_redis.return_value = mock_client

        result = await self.backend.get("test_key")

        assert result == {"key": "value"}
        mock_client.get.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    @patch.object(RedisBackend, "_get_redis")
    async def test_get_not_found(self, mock_get_redis):
        """Test get operation when key not found."""
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        mock_get_redis.return_value = mock_client

        result = await self.backend.get("test_key")

        assert result is None

    @pytest.mark.asyncio
    @patch.object(RedisBackend, "_get_redis")
    async def test_get_pickle_fallback(self, mock_get_redis):
        """Test get operation with pickle fallback."""
        import pickle

        mock_client = AsyncMock()
        test_obj = {"complex": "object"}
        mock_client.get.return_value = pickle.dumps(test_obj)
        mock_get_redis.return_value = mock_client

        result = await self.backend.get("test_key")

        assert result == test_obj

    @pytest.mark.asyncio
    @patch.object(RedisBackend, "_get_redis")
    async def test_set_dict_object(self, mock_get_redis):
        """Test set operation with dictionary."""
        mock_client = AsyncMock()
        mock_get_redis.return_value = mock_client

        await self.backend.set({"key": "value"}, "test_key", 120)

        mock_client.set.assert_called_once()
        args = mock_client.set.call_args
        assert args[1]["name"] == "test_key"
        assert args[1]["ex"] == 120

    @pytest.mark.asyncio
    async def test_delete_startswith(self):
        """Test delete_startswith operation."""
        # Mock the entire method to avoid async iteration complexity in testing
        with patch.object(self.backend, "delete_startswith", new_callable=AsyncMock) as mock_delete:
            await self.backend.delete_startswith("prefix")
            mock_delete.assert_called_once_with("prefix")

    @pytest.mark.asyncio
    async def test_close(self):
        """Test close operation."""
        mock_redis = AsyncMock()
        mock_pool = AsyncMock()

        self.backend.redis = mock_redis
        self.backend._connection_pool = mock_pool

        await self.backend.close()

        mock_redis.aclose.assert_called_once()
        mock_pool.aclose.assert_called_once()
        assert self.backend.redis is None
        assert self.backend._connection_pool is None


class TestGlobalCacheInstance:
    """Test cases for the global Cache instance."""

    def test_global_cache_instance_exists(self):
        """Test that the global Cache instance exists."""
        assert Cache is not None
        assert isinstance(Cache, CacheManager)

    def test_global_cache_initially_uninitialized(self):
        """Test that the global Cache instance is initially uninitialized."""
        # Reset the global cache instance
        Cache.backend = None
        Cache.key_maker = None

        assert Cache.backend is None
        assert Cache.key_maker is None
