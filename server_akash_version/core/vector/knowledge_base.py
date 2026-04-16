"""Knowledge base service for extracting, storing, and retrieving knowledge from Excel files."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from core.config import settings
from core.logging import get_logger
from core.vector.client import QdrantClientManager, get_qdrant_client
from core.vector.chunk_generator import ChunkGenerationResult, DocumentChunk, SemanticChunkGenerator
from core.vector.embedding import get_embedding_service

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """Represents a search result from the knowledge base."""

    id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IndexingResult:
    """Result of indexing a data source into the knowledge base."""

    chunk_count: int
    analysis: dict[str, Any]  # Serializable workbook analysis for DB storage


class KnowledgeBaseService:
    """Service for managing knowledge in Qdrant vector database.

    Uses semantic chunk generation to extract rich information from Excel files:
    - Workbook overview and purpose
    - Sheet structure and purpose
    - Column schemas and data types
    - Formula analysis and categorization
    - Error detection and reporting
    - Data patterns and relationships
    - Statistical summaries
    """

    def __init__(
        self,
        collection_name: str = settings.KNOWLEDGE_COLLECTION_NAME,
    ):
        """Initialize the knowledge base service.

        Args:
            collection_name: Name of the Qdrant collection
        """
        self._collection_name = collection_name
        self._embedding_service = get_embedding_service()
        self._chunk_generator = SemanticChunkGenerator()

    async def initialize(self) -> None:
        """Initialize the knowledge base collection."""
        await QdrantClientManager.ensure_collection(
            collection_name=self._collection_name,
            vector_size=self._embedding_service.dimension,
            distance="Cosine",
        )
        logger.info(
            "Knowledge base initialized",
            collection_name=self._collection_name,
        )

    async def index_data_source(
        self,
        file_content: bytes,
        file_extension: str,
        data_source_id: str,
        user_id: str,
        file_name: str,
    ) -> IndexingResult:
        """Index a data source file into the knowledge base.

        Extracts semantic information from the file including:
        - Document structure and purpose
        - Column definitions and data types
        - Formulas and their categories
        - Errors and their descriptions
        - Data patterns and statistics

        Args:
            file_content: Raw file content
            file_extension: File extension
            data_source_id: ID of the data source
            user_id: ID of the user
            file_name: Original file name

        Returns:
            IndexingResult with chunk count and workbook analysis
        """
        # Ensure collection exists
        await self.initialize()

        # Default empty analysis for non-xlsx files
        analysis: dict[str, Any] = {}
        chunks: list[DocumentChunk] = []

        # Generate semantic chunks based on file type
        if file_extension in {".xlsx", ".xlsm"}:
            result = self._chunk_generator.generate_chunks(
                file_content=file_content,
                file_name=file_name,
                data_source_id=data_source_id,
                user_id=user_id,
            )
            chunks = result.chunks
            analysis = result.analysis
        elif file_extension == ".csv":
            # For CSV, use a simpler extraction since openpyxl doesn't support CSV
            chunks = self._extract_csv_chunks(
                file_content=file_content,
                file_name=file_name,
                data_source_id=data_source_id,
                user_id=user_id,
            )
            analysis = self._create_csv_analysis(file_name, chunks)
        elif file_extension == ".xls":
            # Old Excel format - fallback to basic extraction
            chunks = self._extract_xls_chunks(
                file_content=file_content,
                file_name=file_name,
                data_source_id=data_source_id,
                user_id=user_id,
            )
            analysis = {"file_name": file_name, "file_type": "xls_legacy"}
        else:
            logger.warning(f"Unsupported file type: {file_extension}")
            return IndexingResult(chunk_count=0, analysis={})

        if not chunks:
            logger.warning(
                "No chunks extracted from file",
                file_name=file_name,
                data_source_id=data_source_id,
            )
            return IndexingResult(chunk_count=0, analysis=analysis)

        # Filter out chunks with empty or whitespace-only content
        valid_chunks = [
            chunk for chunk in chunks
            if chunk.content and chunk.content.strip()
        ]

        if not valid_chunks:
            logger.warning(
                "All chunks had empty content",
                file_name=file_name,
                data_source_id=data_source_id,
                original_chunk_count=len(chunks),
            )
            return IndexingResult(chunk_count=0, analysis=analysis)

        logger.info(
            "Generated semantic chunks",
            file_name=file_name,
            chunk_count=len(valid_chunks),
            chunk_types=[c.metadata.get("chunk_type") for c in valid_chunks],
        )

        # Generate embeddings
        texts = [chunk.content for chunk in valid_chunks]
        embeddings = await self._embedding_service.embed_texts(texts)

        # Assign embeddings to chunks
        chunks_with_embeddings = []
        for chunk, embedding in zip(valid_chunks, embeddings, strict=True):
            if embedding and len(embedding) > 0:
                chunk.embedding = embedding
                chunks_with_embeddings.append(chunk)
            else:
                logger.warning(
                    "Chunk produced empty embedding, skipping",
                    chunk_id=chunk.id,
                    chunk_type=chunk.metadata.get("chunk_type"),
                )

        if not chunks_with_embeddings:
            logger.warning(
                "No valid embeddings generated",
                file_name=file_name,
                data_source_id=data_source_id,
            )
            return IndexingResult(chunk_count=0, analysis=analysis)

        # Upload to Qdrant
        await self._upload_chunks(chunks_with_embeddings)

        logger.info(
            "Data source indexed with semantic chunks",
            data_source_id=data_source_id,
            chunk_count=len(chunks_with_embeddings),
            file_name=file_name,
        )

        return IndexingResult(
            chunk_count=len(chunks_with_embeddings),
            analysis=analysis,
        )

    def _extract_csv_chunks(
        self,
        file_content: bytes,
        file_name: str,
        data_source_id: str,
        user_id: str,
    ) -> list[DocumentChunk]:
        """Extract chunks from a CSV file."""
        import uuid
        from io import BytesIO

        import pandas as pd

        chunks = []

        try:
            df = pd.read_csv(BytesIO(file_content))

            base_metadata = {
                "data_source_id": data_source_id,
                "user_id": user_id,
                "file_name": file_name,
                "sheet_name": "csv",
            }

            # Overview chunk
            overview_content = [
                f"# CSV File: {file_name}",
                "",
                "## Structure",
                f"- Total Rows: {len(df)}",
                f"- Total Columns: {len(df.columns)}",
                "",
                "## Columns",
            ]

            for col in df.columns:
                dtype = str(df[col].dtype)
                non_null = df[col].notna().sum()
                unique = df[col].nunique()
                samples = df[col].dropna().head(3).tolist()
                sample_str = ", ".join([str(v)[:30] for v in samples])

                overview_content.append(
                    f"- **{col}** ({dtype}): {non_null} values, {unique} unique | Examples: {sample_str}"
                )

            chunks.append(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    content="\n".join(overview_content),
                    metadata={**base_metadata, "chunk_type": "csv_overview"},
                )
            )

            # Statistics chunk for numeric columns
            numeric_df = df.select_dtypes(include=["number"])
            if not numeric_df.empty:
                stats_content = [f"# Statistics for {file_name}", ""]

                for col in numeric_df.columns:
                    stats = numeric_df[col].describe()
                    stats_content.append(f"## {col}")
                    stats_content.append(f"- Count: {int(stats['count'])}")
                    stats_content.append(f"- Mean: {stats['mean']:.2f}")
                    stats_content.append(f"- Std: {stats['std']:.2f}")
                    stats_content.append(f"- Min: {stats['min']}")
                    stats_content.append(f"- Max: {stats['max']}")
                    stats_content.append("")

                chunks.append(
                    DocumentChunk(
                        id=str(uuid.uuid4()),
                        content="\n".join(stats_content),
                        metadata={**base_metadata, "chunk_type": "statistics"},
                    )
                )

        except Exception as e:
            logger.error(f"Error extracting CSV chunks: {e}")

        return chunks

    def _extract_xls_chunks(
        self,
        file_content: bytes,
        file_name: str,
        data_source_id: str,
        user_id: str,
    ) -> list[DocumentChunk]:
        """Extract chunks from an old Excel (.xls) file."""
        import uuid
        from io import BytesIO

        import pandas as pd

        chunks = []

        try:
            excel_file = pd.ExcelFile(BytesIO(file_content), engine="xlrd")

            for sheet_name in excel_file.sheet_names:
                df = pd.read_excel(excel_file, sheet_name=sheet_name)

                base_metadata = {
                    "data_source_id": data_source_id,
                    "user_id": user_id,
                    "file_name": file_name,
                    "sheet_name": sheet_name,
                }

                # Sheet overview
                content = [
                    f"# Sheet: {sheet_name} (from {file_name})",
                    "",
                    "## Structure",
                    f"- Rows: {len(df)}",
                    f"- Columns: {len(df.columns)}",
                    "",
                    "## Column Definitions",
                ]

                for col in df.columns:
                    dtype = str(df[col].dtype)
                    non_null = df[col].notna().sum()
                    content.append(f"- {col} ({dtype}): {non_null} non-null values")

                chunks.append(
                    DocumentChunk(
                        id=str(uuid.uuid4()),
                        content="\n".join(content),
                        metadata={**base_metadata, "chunk_type": "sheet_overview"},
                    )
                )

        except Exception as e:
            logger.error(f"Error extracting XLS chunks: {e}")

        return chunks

    def _create_csv_analysis(
        self,
        file_name: str,
        chunks: list[DocumentChunk],
    ) -> dict[str, Any]:
        """Create a basic analysis dict for CSV files."""
        return {
            "file_name": file_name,
            "file_type": "csv",
            "sheet_count": 1,
            "sheets": [{"name": "csv", "chunk_count": len(chunks)}],
            "total_formulas": 0,
            "total_errors": 0,
            "overall_purpose": "CSV data file",
            "summary": {
                "has_formulas": False,
                "has_errors": False,
            },
        }

    async def _upload_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Upload chunks to Qdrant."""
        expected_dim = self._embedding_service.dimension

        # Filter and validate chunks
        valid_points = []
        for chunk in chunks:
            if chunk.embedding is None:
                logger.warning(f"Chunk {chunk.id} has None embedding, skipping")
                continue

            if not isinstance(chunk.embedding, list):
                logger.warning(f"Chunk {chunk.id} embedding is not a list, skipping")
                continue

            embedding_dim = len(chunk.embedding)
            if embedding_dim != expected_dim:
                logger.warning(
                    f"Chunk {chunk.id} has wrong dimension: {embedding_dim}, expected {expected_dim}, skipping"
                )
                continue

            valid_points.append(
                PointStruct(
                    id=chunk.id,
                    vector=chunk.embedding,
                    payload={
                        "content": chunk.content,
                        **chunk.metadata,
                    },
                )
            )

        if not valid_points:
            logger.warning("No valid points to upload to Qdrant after validation")
            return

        logger.info(f"Uploading {len(valid_points)} semantic chunks to Qdrant")

        async with get_qdrant_client() as client:
            await client.upsert(
                collection_name=self._collection_name,
                points=valid_points,
                wait=True,
            )

    async def search(
        self,
        query: str,
        user_id: str,
        data_source_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: float = 0.5,
    ) -> list[SearchResult]:
        """Search the knowledge base.

        Args:
            query: Search query
            user_id: User ID for filtering
            data_source_id: Optional data source ID filter
            limit: Maximum number of results
            score_threshold: Minimum similarity score

        Returns:
            List of search results
        """
        # Generate query embedding
        query_embedding = await self._embedding_service.embed_text(query)

        # Build filter
        filter_conditions = [
            FieldCondition(
                key="user_id",
                match=MatchValue(value=user_id),
            )
        ]

        if data_source_id:
            filter_conditions.append(
                FieldCondition(
                    key="data_source_id",
                    match=MatchValue(value=data_source_id),
                )
            )

        async with get_qdrant_client() as client:
            results = await client.search(
                collection_name=self._collection_name,
                query_vector=query_embedding,
                query_filter=Filter(must=filter_conditions),
                limit=limit,
                score_threshold=score_threshold,
            )

        search_results = [
            SearchResult(
                id=str(result.id),
                content=result.payload.get("content", ""),
                score=result.score,
                metadata={
                    k: v
                    for k, v in result.payload.items()
                    if k != "content"
                },
            )
            for result in results
        ]

        logger.debug(
            "Knowledge base search completed",
            query=query[:100],
            result_count=len(search_results),
        )

        return search_results

    async def delete_data_source(self, data_source_id: str, user_id: str) -> int:
        """Delete all chunks for a data source.

        Args:
            data_source_id: ID of the data source to delete
            user_id: User ID for verification

        Returns:
            Number of points deleted
        """
        async with get_qdrant_client() as client:
            # Get count before deletion
            count_result = await client.count(
                collection_name=self._collection_name,
                count_filter=Filter(
                    must=[
                        FieldCondition(
                            key="data_source_id",
                            match=MatchValue(value=data_source_id),
                        ),
                        FieldCondition(
                            key="user_id",
                            match=MatchValue(value=user_id),
                        ),
                    ]
                ),
            )

            deleted_count = count_result.count

            # Delete points
            await client.delete(
                collection_name=self._collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="data_source_id",
                            match=MatchValue(value=data_source_id),
                        ),
                        FieldCondition(
                            key="user_id",
                            match=MatchValue(value=user_id),
                        ),
                    ]
                ),
            )

            logger.info(
                "Data source deleted from knowledge base",
                data_source_id=data_source_id,
                deleted_count=deleted_count,
            )

            return deleted_count

    async def get_collection_info(self) -> dict:
        """Get information about the knowledge base collection."""
        async with get_qdrant_client() as client:
            info = await client.get_collection(self._collection_name)
            return {
                "collection_name": self._collection_name,
                "points_count": info.points_count,
                "vectors_count": info.vectors_count,
                "status": info.status.value,
            }
