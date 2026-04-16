"""Embedding service using OpenAI for generating vector embeddings."""

from __future__ import annotations

import asyncio
from typing import Optional

from openai import AsyncOpenAI

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingService:
    """Service for generating text embeddings using OpenAI.

    Uses a singleton pattern with lazy loading to avoid initializing
    the client until it's actually needed.
    """

    _instance: Optional[EmbeddingService] = None
    _client: Optional[AsyncOpenAI] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __new__(cls) -> EmbeddingService:
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the embedding service."""
        self._model_name = settings.EMBEDDING_MODEL
        self._dimension = settings.EMBEDDING_DIMENSION
        self._batch_size = settings.EMBEDDING_BATCH_SIZE

    @classmethod
    async def get_client(cls) -> AsyncOpenAI:
        """Lazy load the OpenAI client."""
        if cls._client is None:
            async with cls._lock:
                if cls._client is None:
                    cls._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                    logger.info(
                        "OpenAI client initialized",
                        model_name=settings.EMBEDDING_MODEL,
                        dimension=settings.EMBEDDING_DIMENSION,
                    )
        return cls._client

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: The text to embed

        Returns:
            List of floats representing the embedding vector
        """
        embeddings = await self.embed_texts([text])
        return embeddings[0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors (empty list for empty/whitespace texts)
        """
        if not texts:
            return []

        client = await self.get_client()

        # Track original indices and filter out empty texts
        valid_texts = []
        valid_indices = []
        for i, text in enumerate(texts):
            if text and text.strip():
                # OpenAI has a limit on input length, truncate if needed
                cleaned_text = text.strip()[:8000]  # Safe limit for tokens
                valid_texts.append(cleaned_text)
                valid_indices.append(i)

        # If no valid texts, return empty embeddings for all
        if not valid_texts:
            logger.warning("All texts were empty, returning empty embeddings")
            return [[] for _ in texts]

        # Process in batches
        valid_embeddings: list[list[float]] = []

        for i in range(0, len(valid_texts), self._batch_size):
            batch = valid_texts[i : i + self._batch_size]

            try:
                response = await client.embeddings.create(
                    model=self._model_name,
                    input=batch,
                    dimensions=self._dimension,
                )

                # Extract embeddings in order
                batch_embeddings = [item.embedding for item in response.data]
                valid_embeddings.extend(batch_embeddings)

            except Exception as e:
                logger.error(
                    "OpenAI embedding request failed",
                    error=str(e),
                    batch_size=len(batch),
                )
                # Return empty embeddings for failed batch
                valid_embeddings.extend([[] for _ in batch])

        # Reconstruct the full list with empty embeddings for invalid texts
        all_embeddings: list[list[float]] = [[] for _ in texts]
        for idx, embedding in zip(valid_indices, valid_embeddings):
            all_embeddings[idx] = embedding

        logger.debug(
            "Generated embeddings",
            text_count=len(texts),
            valid_text_count=len(valid_texts),
            embedding_dimension=len(valid_embeddings[0]) if valid_embeddings and valid_embeddings[0] else 0,
        )

        return all_embeddings

    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""
        return self._dimension

    @property
    def model_name(self) -> str:
        """Return the model name."""
        return self._model_name


# Global instance getter
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get the global embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
