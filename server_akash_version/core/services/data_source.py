"""Service layer for uploaded spreadsheet data sources."""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions.base import NotFoundError, ValidationError
from core.logging import get_logger
from core.models.data_source import DataSource
from core.vector.knowledge_base import KnowledgeBaseService

logger = get_logger(__name__)


@dataclass
class KnowledgeIndexingResult:
    """Result of indexing a data source to the knowledge base."""

    chunk_count: int
    analysis: dict[str, Any]


class DataSourceService:
    """Service for handling data source uploads and metadata retrieval."""

    def __init__(self, session: AsyncSession, knowledge_base: Optional[KnowledgeBaseService] = None):
        """Initialize service with database session and optional knowledge base."""
        self.session = session
        self._knowledge_base = knowledge_base or KnowledgeBaseService()

    async def create_data_source(self, user_id: str, name: str, file: UploadFile) -> DataSource:
        """Create a data source from an uploaded spreadsheet."""
        if not name.strip():
            raise ValidationError("Data source name is required", field="name")

        if not file.filename:
            raise ValidationError("File name is required", field="file")

        file_extension = Path(file.filename).suffix.lower()
        if file_extension not in settings.DATA_SOURCE_ALLOWED_EXTENSIONS:
            raise ValidationError(
                "Unsupported file type. Allowed types: .xlsx, .xls, .xlsm",
                field="file",
                value=file_extension,
            )

        file_content = await file.read()
        file_size_bytes = len(file_content)
        max_size_bytes = settings.DATA_SOURCE_MAX_FILE_SIZE_MB * 1024 * 1024

        if file_size_bytes == 0:
            raise ValidationError("Uploaded file is empty", field="file")
        if file_size_bytes > max_size_bytes:
            raise ValidationError(
                f"File exceeds max size of {settings.DATA_SOURCE_MAX_FILE_SIZE_MB} MB",
                field="file",
                value=file_size_bytes,
            )

        metadata = self._extract_excel_metadata(file_content=file_content, file_extension=file_extension)
        stored_file_path = self._persist_file(file.filename, file_extension, file_content)

        checksum = hashlib.sha256(file_content).hexdigest()

        db_obj = DataSource(
            user_id=user_id,
            name=name.strip(),
            original_file_name=file.filename,
            stored_file_path=stored_file_path,
            mime_type=file.content_type,
            file_extension=file_extension,
            file_size_bytes=file_size_bytes,
            sheet_count=metadata["sheet_count"],
            sheet_names=metadata["sheet_names"],
            file_checksum_sha256=checksum,
            meta_info=metadata,
        )

        self.session.add(db_obj)
        await self.session.flush()
        await self.session.refresh(db_obj)

        logger.info(
            "Data source created",
            data_source_id=db_obj.id,
            user_id=user_id,
            name=name,
            sheet_count=db_obj.sheet_count,
        )
        return db_obj

    async def list_data_sources(self, user_id: str, skip: int = 0, limit: int = 50) -> tuple[list[DataSource], int]:
        """List data sources for a user."""
        stmt = (
            select(DataSource)
            .where(DataSource.user_id == user_id)
            .order_by(desc(DataSource.created_at))
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        count_stmt = select(func.count(DataSource.id)).where(DataSource.user_id == user_id)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        return items, total

    async def get_data_source(self, user_id: str, data_source_id: str) -> DataSource:
        """Get one data source by ID for the authenticated user."""
        stmt = select(DataSource).where(DataSource.id == data_source_id, DataSource.user_id == user_id)
        result = await self.session.execute(stmt)
        data_source = result.scalar_one_or_none()

        if not data_source:
            raise NotFoundError(
                message="Data source not found",
                resource_type="DataSource",
                resource_id=data_source_id,
            )

        return data_source

    async def index_to_knowledge_base(
        self,
        data_source_id: str,
        user_id: str,
    ) -> KnowledgeIndexingResult:
        """Index a data source into the vector knowledge base.

        Args:
            data_source_id: ID of the data source to index
            user_id: ID of the user

        Returns:
            KnowledgeIndexingResult with chunk count and workbook analysis
        """
        data_source = await self.get_data_source(user_id, data_source_id)

        # Read the file content
        file_path = Path(data_source.stored_file_path)
        if not file_path.exists():
            raise NotFoundError(
                message="Data source file not found on disk",
                resource_type="DataSourceFile",
                resource_id=data_source_id,
            )

        file_content = file_path.read_bytes()

        # Index into knowledge base and get analysis
        indexing_result = await self._knowledge_base.index_data_source(
            file_content=file_content,
            file_extension=data_source.file_extension,
            data_source_id=str(data_source.id),
            user_id=user_id,
            file_name=data_source.original_file_name,
        )

        # Update data source meta_info with the analysis
        if indexing_result.analysis:
            updated_meta = {
                **data_source.meta_info,
                "knowledge_base": {
                    "indexed": True,
                    "chunk_count": indexing_result.chunk_count,
                    "analysis": indexing_result.analysis,
                },
            }
            data_source.meta_info = updated_meta
            await self.session.flush()

        logger.info(
            "Data source indexed to knowledge base",
            data_source_id=data_source_id,
            chunk_count=indexing_result.chunk_count,
            has_analysis=bool(indexing_result.analysis),
        )

        return KnowledgeIndexingResult(
            chunk_count=indexing_result.chunk_count,
            analysis=indexing_result.analysis,
        )

    async def search_knowledge_base(
        self,
        query: str,
        user_id: str,
        data_source_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search the knowledge base for relevant content.

        Args:
            query: Search query
            user_id: User ID for filtering
            data_source_id: Optional data source ID to filter by
            limit: Maximum number of results

        Returns:
            List of search results with content and metadata
        """
        results = await self._knowledge_base.search(
            query=query,
            user_id=user_id,
            data_source_id=data_source_id,
            limit=limit,
        )

        return [
            {
                "id": r.id,
                "content": r.content,
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
        ]

    async def delete_from_knowledge_base(
        self,
        data_source_id: str,
        user_id: str,
    ) -> int:
        """Delete a data source from the knowledge base.

        Args:
            data_source_id: ID of the data source
            user_id: User ID for verification

        Returns:
            Number of chunks deleted
        """
        return await self._knowledge_base.delete_data_source(
            data_source_id=data_source_id,
            user_id=user_id,
        )

    def _persist_file(self, original_filename: str, file_extension: str, file_content: bytes) -> str:
        """Persist uploaded file to local storage and return path."""
        upload_dir = Path(settings.DATA_SOURCE_UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)

        safe_base_name = Path(original_filename).stem.replace(" ", "_")[:80]
        saved_name = f"{uuid4()}_{safe_base_name}{file_extension}"
        target_path = upload_dir / saved_name
        target_path.write_bytes(file_content)

        return str(target_path)

    def _extract_excel_metadata(self, file_content: bytes, file_extension: str) -> dict:
        """Extract metadata from uploaded excel file."""
        sheet_names: list[str] = []

        if file_extension in {".xlsx", ".xlsm"}:
            try:
                openpyxl_module = importlib.import_module("openpyxl")
            except ImportError as exc:
                raise ValidationError(
                    "openpyxl is required to read .xlsx/.xlsm files",
                    field="file",
                ) from exc

            workbook = openpyxl_module.load_workbook(filename=BytesIO(file_content), read_only=True, data_only=True)
            sheet_names = list(workbook.sheetnames)
            workbook.close()

        elif file_extension == ".xls":
            try:
                xlrd_module = importlib.import_module("xlrd")
            except ImportError as exc:
                raise ValidationError(
                    "xlrd is required to read .xls files",
                    field="file",
                ) from exc

            workbook = xlrd_module.open_workbook(file_contents=file_content, on_demand=True)
            sheet_names = workbook.sheet_names()
            workbook.release_resources()

        if not sheet_names:
            raise ValidationError("No sheets found in uploaded file", field="file")

        return {
            "sheet_count": len(sheet_names),
            "sheet_names": sheet_names,
            "size_bytes": len(file_content),
            "size_mb": round(len(file_content) / (1024 * 1024), 4),
        }
