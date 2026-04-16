import asyncio
import os
from typing import Any

import pytest

from core.cache import Cache, CustomKeyMaker, RedisBackend
from core.cache.cache_manager import CacheManager
from core.dependencies.cache import cache_available, get_cache_manager, safe_cache_get, safe_cache_set


@pytest.mark.integration
class TestCacheIntegration:
    """Integration tests for cache system with real Redis instance."""

    @pytest.fixture(scope="function")
    def event_loop(self):
        """Create an instance of the default event loop for each test."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        yield loop
        # Don't close the loop here - let pytest handle it

    @pytest.fixture(scope="function", autouse=True)
    async def setup_cache(self):
        """Setup cache manager for integration tests."""
        # Skip integration tests if Redis is not available
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/1")  # Use DB 1 for tests

        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(redis_url)
            await redis_client.ping()
            await redis_client.aclose()
        except Exception:
            pytest.skip("Redis not available for integration tests")

        # Override settings for tests
        import core.config

        original_settings = core.config.settings
        test_settings = core.config.Settings(
            ENVIRONMENT=core.config.Environment.TESTING,
            REDIS_URL=redis_url,
            CACHE_ENABLED=True,
            DEBUG=True,
            LOG_LEVEL=core.config.LogLevel.DEBUG,
        )
        core.config.settings = test_settings

        # Initialize cache manager
        test_cache = CacheManager()
        test_cache.init(RedisBackend, CustomKeyMaker)

        # Override global cache for tests
        Cache.backend = test_cache.backend
        Cache.key_maker = test_cache.key_maker

        yield test_cache

        # Cleanup - ensure we close the backend properly
        try:
            if Cache.backend and hasattr(Cache.backend, "close"):
                await Cache.backend.close()  # type: ignore
        except Exception:
            # Ignore cleanup errors
            pass

        # Restore original settings
        core.config.settings = original_settings

    @pytest.mark.asyncio
    async def test_cache_decorator_end_to_end(self, setup_cache):
        """Test cache decorator with real Redis backend."""
        cache_manager = setup_cache
        call_count = 0

        @cache_manager.cached(prefix="integration_test", ttl=5)
        async def expensive_function(value: str) -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)  # Simulate expensive operation
            return f"processed_{value}"

        # First call should execute function
        result1 = await expensive_function("test_data")
        assert result1 == "processed_test_data"
        assert call_count == 1

        # Second call should use cache
        result2 = await expensive_function("test_data")
        assert result2 == "processed_test_data"
        assert call_count == 1  # Function not called again

        # Wait for cache to expire
        await asyncio.sleep(6)

        # Third call should execute function again
        result3 = await expensive_function("test_data")
        assert result3 == "processed_test_data"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_different_function_signatures(self, setup_cache):
        """Test caching with different function signatures."""
        cache_manager = setup_cache

        @cache_manager.cached(prefix="sig_test", ttl=30)
        async def func_with_args(arg1: str, arg2: int) -> str:
            return f"{arg1}_{arg2}"

        @cache_manager.cached(prefix="sig_test", ttl=30)
        async def func_no_args() -> str:
            return "no_args_result"

        # Test functions with different signatures
        result1 = await func_with_args("test", 123)
        result2 = await func_no_args()

        assert result1 == "test_123"
        assert result2 == "no_args_result"

    @pytest.mark.asyncio
    async def test_cache_prefix_operations(self, setup_cache):
        """Test cache operations using prefixes."""
        cache_manager = setup_cache

        @cache_manager.cached(prefix="user_data", ttl=30)
        async def get_user_data(user_id: int) -> dict:
            return {"id": user_id, "name": f"user_{user_id}"}

        @cache_manager.cached(prefix="user_data", ttl=30)
        async def get_user_profile(user_id: int) -> dict:
            return {"id": user_id, "profile": f"profile_{user_id}"}

        # Cache results for both functions
        await get_user_data(1)
        await get_user_profile(1)

        # Clear all cache entries with prefix
        await cache_manager.remove_by_prefix("user_data")

        # Both functions should execute again
        result1 = await get_user_data(1)
        result2 = await get_user_profile(1)

        assert result1 == {"id": 1, "name": "user_1"}
        assert result2 == {"id": 1, "profile": "profile_1"}

    @pytest.mark.asyncio
    async def test_cache_complex_data_types(self, setup_cache):
        """Test caching with complex data types."""
        cache_manager = setup_cache

        @cache_manager.cached(prefix="complex_data", ttl=30)
        async def get_complex_data() -> dict:
            return {"string": "test", "number": 42, "list": [1, 2, 3], "nested": {"key": "value"}, "boolean": True}

        result1 = await get_complex_data()
        result2 = await get_complex_data()

        assert result1 == result2
        assert isinstance(result1["list"], list)
        assert isinstance(result1["nested"], dict)

    @pytest.mark.asyncio
    async def test_safe_cache_operations(self, setup_cache):
        """Test safe cache operation dependencies."""
        # Use the fixture-provided cache manager to ensure consistency
        cache_manager = setup_cache

        # Test cache availability
        is_available = cache_available(cache_manager)
        assert is_available is True

        # Test safe cache set and get
        success = await safe_cache_set("test_key", {"data": "value"}, ttl=30, cache_manager=cache_manager)
        assert success is True

        result = await safe_cache_get("test_key", cache_manager=cache_manager)
        assert result == {"data": "value"}

        # Test getting non-existent key
        result = await safe_cache_get("non_existent", cache_manager=cache_manager)
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_error_handling(self, setup_cache):
        """Test cache behavior when Redis connection fails."""
        cache_manager = setup_cache

        # Close the Redis connection to simulate failure
        if cache_manager.backend and hasattr(cache_manager.backend, "close"):
            await cache_manager.backend.close()

        @cache_manager.cached(prefix="error_test", ttl=30, fallback_on_error=True)
        async def test_function_with_fallback() -> str:
            return "fallback_result"

        # Should fall back to function execution when cache fails
        result = await test_function_with_fallback()
        assert result == "fallback_result"

    @pytest.mark.asyncio
    async def test_concurrent_cache_access(self, setup_cache):
        """Test concurrent access to cache."""
        cache_manager = setup_cache
        call_count = 0

        # Clear any existing cache entries for this test
        try:
            await cache_manager.remove_by_prefix("concurrent_test")
        except Exception:
            pass  # Ignore errors if prefix doesn't exist

        @cache_manager.cached(prefix="concurrent_test", ttl=30)
        async def slow_function(value: str) -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.2)  # Simulate slow operation
            return f"result_{value}"

        # Run multiple concurrent calls
        tasks = [slow_function("test") for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # All results should be the same
        assert all(result == "result_test" for result in results)

        # Function should have been called at least once but potentially more
        # due to concurrent access before first result is cached
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_cache_serialization_edge_cases(self, setup_cache):
        """Test cache with edge case data types."""
        cache_manager = setup_cache

        @cache_manager.cached(prefix="edge_cases", ttl=30)
        async def get_edge_case_data(case: str) -> Any:
            if case == "none":
                return None
            elif case == "empty_list":
                return []
            elif case == "empty_dict":
                return {}
            elif case == "unicode":
                return "Hello, 世界! 🌍"
            elif case == "numbers":
                return {"int": 42, "float": 3.14, "negative": -1}
            return case

        # Test various edge cases
        test_cases = ["none", "empty_list", "empty_dict", "unicode", "numbers"]

        for case in test_cases:
            result1 = await get_edge_case_data(case)
            result2 = await get_edge_case_data(case)  # Should come from cache

            assert result1 == result2, f"Mismatch for case: {case}"


@pytest.mark.integration
class TestCacheDependencies:
    """Test cache dependency injection in realistic scenarios."""

    @pytest.fixture(autouse=True)
    async def setup_test_cache(self):
        """Setup test cache instance."""
        redis_url = "redis://localhost:6379/1"

        # Skip if Redis not available
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(redis_url)
            await redis_client.ping()
            await redis_client.aclose()
        except Exception:
            pytest.skip("Redis not available for dependency injection tests")

        # Override settings for tests
        import core.config

        original_settings = core.config.settings
        test_settings = core.config.Settings(
            ENVIRONMENT=core.config.Environment.TESTING,
            REDIS_URL=redis_url,
            CACHE_ENABLED=True,
            DEBUG=True,
            LOG_LEVEL=core.config.LogLevel.DEBUG,
        )
        core.config.settings = test_settings

        # Initialize cache
        test_cache = CacheManager()
        test_cache.init(RedisBackend, CustomKeyMaker)

        # Override global instance
        Cache.backend = test_cache.backend
        Cache.key_maker = test_cache.key_maker

        yield

        # Cleanup - ensure we close the backend properly
        try:
            if Cache.backend and hasattr(Cache.backend, "close"):
                await Cache.backend.close()  # type: ignore
        except Exception:
            # Ignore cleanup errors
            pass

        # Restore original settings
        core.config.settings = original_settings

    @pytest.mark.asyncio
    async def test_dependency_injection_flow(self):
        """Test the complete dependency injection flow."""
        # Get cache manager through dependency
        cache_manager = get_cache_manager()
        assert cache_manager is not None

        # Check availability
        assert cache_available(cache_manager) is True

        # Test safe operations
        await safe_cache_set("dep_test", {"message": "hello"})
        result = await safe_cache_get("dep_test")
        assert result == {"message": "hello"}
