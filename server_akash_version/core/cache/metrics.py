import time
from typing import Dict, Optional

from prometheus_client import Counter, Gauge, Histogram

from core.logging import get_logger

logger = get_logger(__name__)

# Prometheus metrics for cache operations
cache_operations_total = Counter("cache_operations_total", "Total number of cache operations", ["operation", "backend", "status"])

cache_hit_rate = Gauge("cache_hit_rate", "Cache hit rate percentage", ["backend"])

cache_operation_duration = Histogram(
    "cache_operation_duration_seconds",
    "Time spent on cache operations",
    ["operation", "backend"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

cache_size = Gauge("cache_size_bytes", "Estimated cache size in bytes", ["backend"])

cache_connections = Gauge("cache_connections_active", "Number of active cache connections", ["backend"])


class CacheMetrics:
    """Cache metrics collector and reporter."""

    def __init__(self):
        self.hit_count = 0
        self.miss_count = 0
        self.error_count = 0
        self._start_times: Dict[str, float] = {}

    def record_operation_start(self, operation: str, operation_id: str) -> None:
        """Record the start of a cache operation."""
        self._start_times[operation_id] = time.time()

    def record_cache_hit(self, backend: str = "redis", operation_id: Optional[str] = None) -> None:
        """Record a cache hit."""
        self.hit_count += 1
        cache_operations_total.labels(operation="get", backend=backend, status="hit").inc()

        if operation_id and operation_id in self._start_times:
            duration = time.time() - self._start_times[operation_id]
            cache_operation_duration.labels(operation="get", backend=backend).observe(duration)
            del self._start_times[operation_id]

        self._update_hit_rate(backend)
        logger.debug("Cache hit recorded", backend=backend, total_hits=self.hit_count)

    def record_cache_miss(self, backend: str = "redis", operation_id: Optional[str] = None) -> None:
        """Record a cache miss."""
        self.miss_count += 1
        cache_operations_total.labels(operation="get", backend=backend, status="miss").inc()

        if operation_id and operation_id in self._start_times:
            duration = time.time() - self._start_times[operation_id]
            cache_operation_duration.labels(operation="get", backend=backend).observe(duration)
            del self._start_times[operation_id]

        self._update_hit_rate(backend)
        logger.debug("Cache miss recorded", backend=backend, total_misses=self.miss_count)

    def record_cache_set(self, backend: str = "redis", operation_id: Optional[str] = None, success: bool = True) -> None:
        """Record a cache set operation."""
        status = "success" if success else "error"
        cache_operations_total.labels(operation="set", backend=backend, status=status).inc()

        if operation_id and operation_id in self._start_times:
            duration = time.time() - self._start_times[operation_id]
            cache_operation_duration.labels(operation="set", backend=backend).observe(duration)
            del self._start_times[operation_id]

        if not success:
            self.error_count += 1

        logger.debug("Cache set recorded", backend=backend, success=success)

    def record_cache_delete(self, backend: str = "redis", operation_id: Optional[str] = None, success: bool = True) -> None:
        """Record a cache delete operation."""
        status = "success" if success else "error"
        cache_operations_total.labels(operation="delete", backend=backend, status=status).inc()

        if operation_id and operation_id in self._start_times:
            duration = time.time() - self._start_times[operation_id]
            cache_operation_duration.labels(operation="delete", backend=backend).observe(duration)
            del self._start_times[operation_id]

        if not success:
            self.error_count += 1

        logger.debug("Cache delete recorded", backend=backend, success=success)

    def record_cache_error(self, operation: str, backend: str = "redis", operation_id: Optional[str] = None) -> None:
        """Record a cache operation error."""
        self.error_count += 1
        cache_operations_total.labels(operation=operation, backend=backend, status="error").inc()

        if operation_id and operation_id in self._start_times:
            duration = time.time() - self._start_times[operation_id]
            cache_operation_duration.labels(operation=operation, backend=backend).observe(duration)
            del self._start_times[operation_id]

        logger.warning("Cache error recorded", operation=operation, backend=backend, total_errors=self.error_count)

    def _update_hit_rate(self, backend: str) -> None:
        """Update the cache hit rate metric."""
        total_operations = self.hit_count + self.miss_count
        if total_operations > 0:
            hit_rate_percentage = (self.hit_count / total_operations) * 100
            cache_hit_rate.labels(backend=backend).set(hit_rate_percentage)

    def get_stats(self) -> Dict[str, int]:
        """Get current cache statistics."""
        return {
            "hits": self.hit_count,
            "misses": self.miss_count,
            "errors": self.error_count,
            "total_operations": self.hit_count + self.miss_count + self.error_count,
        }

    def reset_stats(self) -> None:
        """Reset all statistics."""
        self.hit_count = 0
        self.miss_count = 0
        self.error_count = 0
        self._start_times.clear()
        logger.info("Cache metrics reset")


# Global metrics instance
metrics = CacheMetrics()
