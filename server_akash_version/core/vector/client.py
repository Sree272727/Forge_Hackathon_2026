"""Qdrant client singleton management with connection pooling and health checks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from qdrant_client import AsyncQdrantClient, QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)


class QdrantClientManager:
    """Manages Qdrant client connections with singleton pattern and health checks."""

    _instance: Optional[QdrantClientManager] = None
    _async_client: Optional[AsyncQdrantClient] = None
    _sync_client: Optional[QdrantClient] = None
    _lock: asyncio.Lock = asyncio.Lock()
    _initialized: bool = False

    def __new__(cls) -> QdrantClientManager:
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    async def get_async_client(cls) -> AsyncQdrantClient:
        """Get or create the async Qdrant client with thread-safe initialization."""
        if cls._async_client is None:
            async with cls._lock:
                if cls._async_client is None:
                    cls._async_client = cls._create_async_client()
                    logger.info(
                        "Qdrant async client initialized",
                        host=settings.QDRANT_HOST,
                        port=settings.QDRANT_PORT,
                        prefer_grpc=settings.QDRANT_PREFER_GRPC,
                    )
        return cls._async_client

    @classmethod
    def get_sync_client(cls) -> QdrantClient:
        """Get or create the synchronous Qdrant client."""
        if cls._sync_client is None:
            cls._sync_client = cls._create_sync_client()
            logger.info(
                "Qdrant sync client initialized",
                host=settings.QDRANT_HOST,
                port=settings.QDRANT_PORT,
            )
        return cls._sync_client

    @classmethod
    def _create_async_client(cls) -> AsyncQdrantClient:
        """Create a new async Qdrant client instance."""
        client_kwargs = {
            "host": settings.QDRANT_HOST,
            "port": settings.QDRANT_PORT,
            "grpc_port": settings.QDRANT_GRPC_PORT,
            "prefer_grpc": settings.QDRANT_PREFER_GRPC,
            "timeout": settings.QDRANT_TIMEOUT,
        }

        if settings.QDRANT_API_KEY:
            client_kwargs["api_key"] = settings.QDRANT_API_KEY

        return AsyncQdrantClient(**client_kwargs)

    @classmethod
    def _create_sync_client(cls) -> QdrantClient:
        """Create a new synchronous Qdrant client instance."""
        client_kwargs = {
            "host": settings.QDRANT_HOST,
            "port": settings.QDRANT_PORT,
            "grpc_port": settings.QDRANT_GRPC_PORT,
            "prefer_grpc": settings.QDRANT_PREFER_GRPC,
            "timeout": settings.QDRANT_TIMEOUT,
        }

        if settings.QDRANT_API_KEY:
            client_kwargs["api_key"] = settings.QDRANT_API_KEY

        return QdrantClient(**client_kwargs)

    @classmethod
    async def health_check(cls) -> dict:
        """Perform health check on Qdrant connection."""
        try:
            client = await cls.get_async_client()
            # Get collections to verify connectivity
            collections = await client.get_collections()
            return {
                "status": "healthy",
                "collections_count": len(collections.collections),
                "host": settings.QDRANT_HOST,
                "port": settings.QDRANT_PORT,
            }
        except (ResponseHandlingException, UnexpectedResponse) as e:
            logger.error("Qdrant health check failed", error=str(e))
            return {
                "status": "unhealthy",
                "error": str(e),
                "host": settings.QDRANT_HOST,
                "port": settings.QDRANT_PORT,
            }
        except Exception as e:
            logger.error("Qdrant health check unexpected error", error=str(e))
            return {
                "status": "unhealthy",
                "error": f"Unexpected error: {str(e)}",
                "host": settings.QDRANT_HOST,
                "port": settings.QDRANT_PORT,
            }

    @classmethod
    async def close(cls) -> None:
        """Close all client connections."""
        if cls._async_client is not None:
            await cls._async_client.close()
            cls._async_client = None
            logger.info("Qdrant async client closed")

        if cls._sync_client is not None:
            cls._sync_client.close()
            cls._sync_client = None
            logger.info("Qdrant sync client closed")

    @classmethod
    async def ensure_collection(
        cls,
        collection_name: str,
        vector_size: int,
        distance: str = "Cosine",
        on_disk_payload: bool = True,
    ) -> bool:
        """Ensure a collection exists, creating it if necessary.

        Args:
            collection_name: Name of the collection
            vector_size: Dimension of vectors
            distance: Distance metric (Cosine, Euclid, Dot)
            on_disk_payload: Store payload on disk to save RAM

        Returns:
            True if collection was created, False if it already existed
        """
        from qdrant_client.models import Distance, VectorParams

        client = await cls.get_async_client()

        try:
            collections = await client.get_collections()
            existing_names = [c.name for c in collections.collections]

            if collection_name in existing_names:
                logger.debug("Collection already exists", collection_name=collection_name)
                return False

            # Map string distance to enum
            distance_map = {
                "Cosine": Distance.COSINE,
                "Euclid": Distance.EUCLID,
                "Dot": Distance.DOT,
            }

            await client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=distance_map.get(distance, Distance.COSINE),
                    on_disk=True,  # Store vectors on disk for large datasets
                ),
                on_disk_payload=on_disk_payload,
                # Optimized for search performance
                optimizers_config={
                    "default_segment_number": 2,
                    "memmap_threshold": 20000,
                    "indexing_threshold": 20000,
                },
            )

            logger.info(
                "Collection created",
                collection_name=collection_name,
                vector_size=vector_size,
                distance=distance,
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to ensure collection",
                collection_name=collection_name,
                error=str(e),
            )
            raise


@asynccontextmanager
async def get_qdrant_client() -> AsyncGenerator[AsyncQdrantClient, None]:
    """Context manager for getting Qdrant client.

    Usage:
        async with get_qdrant_client() as client:
            await client.get_collections()
    """
    client = await QdrantClientManager.get_async_client()
    try:
        yield client
    except Exception as e:
        logger.error("Error during Qdrant operation", error=str(e))
        raise
