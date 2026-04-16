"""Local sentence-transformers embedder + Qdrant index wrapper.

Supports two Qdrant deployment modes:
  - Server mode (QDRANT_URL set): connects to a running Qdrant instance
    (Docker or Qdrant Cloud). QDRANT_API_KEY optional.
  - Embedded mode (default): stores vectors on local disk via
    QdrantClient(path=...). No server required, perfect for local dev.

Embeds using BAAI/bge-small-en-v1.5 (384-dim, CPU-friendly).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("rosetta.embeddings")


EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = 384


class Embedder:
    """Process-wide lazy singleton for the sentence-transformers model."""

    _model = None
    _lock = threading.Lock()

    @classmethod
    def get_model(cls):
        if cls._model is None:
            with cls._lock:
                if cls._model is None:
                    from sentence_transformers import SentenceTransformer
                    log.info("Loading embedding model: %s", EMBEDDING_MODEL)
                    cls._model = SentenceTransformer(EMBEDDING_MODEL)
        return cls._model

    @classmethod
    def embed(cls, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = cls.get_model()
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()


class QdrantIndex:
    """Wrapper around QdrantClient covering both embedded and server modes."""

    _client = None
    _lock = threading.Lock()

    @classmethod
    def get_client(cls):
        if cls._client is None:
            with cls._lock:
                if cls._client is None:
                    cls._client = cls._make_client()
        return cls._client

    @classmethod
    def _make_client(cls):
        from qdrant_client import QdrantClient
        url = os.environ.get("QDRANT_URL")
        api_key = os.environ.get("QDRANT_API_KEY")
        if url:
            log.info("Connecting to Qdrant server: %s", url)
            return QdrantClient(url=url, api_key=api_key, timeout=30)
        # Embedded mode: local on-disk storage
        storage_path = os.environ.get(
            "QDRANT_EMBEDDED_PATH",
            str(Path(__file__).resolve().parent.parent / "qdrant_storage")
        )
        Path(storage_path).mkdir(parents=True, exist_ok=True)
        log.info("Using Qdrant in embedded mode at: %s", storage_path)
        return QdrantClient(path=storage_path)

    @classmethod
    def collection_name(cls, workbook_id: str) -> str:
        # Collection names must be alphanumeric + underscores
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in workbook_id)
        return f"rosetta_{safe}"

    @classmethod
    def ensure_collection(cls, workbook_id: str) -> str:
        from qdrant_client.http.models import Distance, VectorParams
        name = cls.collection_name(workbook_id)
        client = cls.get_client()
        try:
            client.get_collection(name)
        except Exception:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )
        return name

    @classmethod
    def upsert_cells(cls, workbook_id: str, contexts) -> int:
        """Embed context_strings and upsert to Qdrant. Returns count upserted."""
        if not contexts:
            return 0
        from qdrant_client.http.models import PointStruct

        name = cls.ensure_collection(workbook_id)
        texts = [c.context_string for c in contexts]
        vectors = Embedder.embed(texts)

        points = []
        for i, (vec, c) in enumerate(zip(vectors, contexts)):
            points.append(PointStruct(
                id=i,
                vector=vec,
                payload={
                    "ref": c.ref,
                    "sheet": c.sheet,
                    "coord": c.coord,
                    "semantic_label": c.semantic_label,
                    "context_string": c.context_string,
                    "is_summary_cell": c.is_summary_cell,
                    "is_major_output": c.is_major_output,
                    "formula_type": c.formula_type,
                },
            ))
        # Batch upsert
        BATCH = 256
        client = cls.get_client()
        for i in range(0, len(points), BATCH):
            client.upsert(collection_name=name, points=points[i:i + BATCH], wait=True)
        return len(points)

    @classmethod
    def search(cls, workbook_id: str, query: str, limit: int = 10) -> list[dict]:
        """Semantic search. Returns list of {ref, label, score, context}."""
        name = cls.collection_name(workbook_id)
        client = cls.get_client()
        try:
            qvec = Embedder.embed([query])[0]
            # qdrant-client>=1.9 uses query_points; fall back to search for older versions
            if hasattr(client, "query_points"):
                response = client.query_points(
                    collection_name=name, query=qvec, limit=limit, with_payload=True,
                )
                hits = response.points
            else:
                hits = client.search(collection_name=name, query_vector=qvec, limit=limit)
            return [
                {
                    "ref": h.payload.get("ref"),
                    "label": h.payload.get("semantic_label"),
                    "context": h.payload.get("context_string"),
                    "score": float(h.score),
                    "is_major_output": h.payload.get("is_major_output", False),
                }
                for h in hits
            ]
        except Exception as e:
            log.warning("Qdrant search failed (%s) for workbook=%s query=%r", e, workbook_id, query)
            return []

    @classmethod
    def delete_collection(cls, workbook_id: str) -> bool:
        name = cls.collection_name(workbook_id)
        try:
            cls.get_client().delete_collection(name)
            return True
        except Exception as e:
            log.warning("delete_collection failed: %s", e)
            return False


def is_enabled() -> bool:
    """Whether the semantic tier should be used.

    Enabled automatically if the qdrant client import succeeds AND
    (QDRANT_URL set OR embedded mode allowed). The v1.5 stub check for
    ROSETTA_SEMANTIC_ENABLED is bypassed here because v2A is always-on.
    """
    try:
        from qdrant_client import QdrantClient  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except ImportError:
        return False
    return True
