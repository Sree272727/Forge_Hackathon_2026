"""Data source API routes for file upload and metadata management."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.schemas.data_source import DataSourceListResponse, DataSourceResponse
from core.database.session import get_db_session
from core.dependencies.auth import get_current_user
from core.logging import get_logger
from core.models.user import User
from core.services.data_source import DataSourceService
from core.services.excel_agent import ExcelAgentService

logger = get_logger(__name__)


class KnowledgeSearchRequest(BaseModel):
    """Request model for knowledge base search."""

    query: str = Field(..., min_length=1, description="Search query")
    data_source_id: Optional[str] = Field(None, description="Filter by specific data source")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results to return")


class KnowledgeSearchResult(BaseModel):
    """Single search result from knowledge base."""

    id: str
    content: str
    score: float
    metadata: dict


class KnowledgeSearchResponse(BaseModel):
    """Response model for knowledge base search."""

    results: list[KnowledgeSearchResult]
    total: int
    query: str


class WorkbookAnalysisSummary(BaseModel):
    """Summary of workbook analysis for UI display."""

    total_rows: Optional[int] = None
    total_columns: Optional[int] = None
    has_formulas: bool = False
    has_errors: bool = False
    formula_categories: list[str] = []
    error_types: list[str] = []
    column_purposes: dict[str, int] = {}


class SheetInfo(BaseModel):
    """Information about a single sheet."""

    name: str
    row_count: int = 0
    column_count: int = 0
    formula_count: int = 0
    error_count: int = 0
    inferred_purpose: Optional[str] = None
    data_patterns: list[str] = []


class WorkbookAnalysisResponse(BaseModel):
    """Detailed workbook analysis for UI display."""

    file_name: str
    sheet_count: int
    total_formulas: int = 0
    total_errors: int = 0
    overall_purpose: Optional[str] = None
    sheets: list[SheetInfo] = []
    summary: Optional[WorkbookAnalysisSummary] = None


class IndexingResponse(BaseModel):
    """Response model for indexing operations."""

    data_source_id: str
    chunks_indexed: int
    status: str
    analysis: Optional[WorkbookAnalysisResponse] = None

router = APIRouter(prefix="/data-sources", tags=["Data Sources"])


def _convert_analysis_to_response(analysis: dict | None) -> WorkbookAnalysisResponse | None:
    """Convert analysis dict from service to response model for API."""
    if not analysis:
        return None

    # Build sheet info list
    sheets = []
    for sheet_data in analysis.get("sheets", []):
        sheets.append(
            SheetInfo(
                name=sheet_data.get("name", "Unknown"),
                row_count=sheet_data.get("row_count", 0),
                column_count=sheet_data.get("column_count", 0),
                formula_count=sheet_data.get("formula_count", 0),
                error_count=sheet_data.get("error_count", 0),
                inferred_purpose=sheet_data.get("inferred_purpose"),
                data_patterns=sheet_data.get("data_patterns", []),
            )
        )

    # Build summary
    summary_data = analysis.get("summary", {})
    summary = WorkbookAnalysisSummary(
        total_rows=summary_data.get("total_rows"),
        total_columns=summary_data.get("total_columns"),
        has_formulas=summary_data.get("has_formulas", False),
        has_errors=summary_data.get("has_errors", False),
        formula_categories=summary_data.get("formula_categories", []),
        error_types=summary_data.get("error_types", []),
        column_purposes=summary_data.get("column_purposes", {}),
    )

    return WorkbookAnalysisResponse(
        file_name=analysis.get("file_name", "Unknown"),
        sheet_count=analysis.get("sheet_count", 0),
        total_formulas=analysis.get("total_formulas", 0),
        total_errors=analysis.get("total_errors", 0),
        overall_purpose=analysis.get("overall_purpose"),
        sheets=sheets,
        summary=summary,
    )


async def get_data_source_service(session: AsyncSession = Depends(get_db_session)) -> DataSourceService:
    """Dependency for data source service."""
    return DataSourceService(session)


async def get_excel_agent_service(session: AsyncSession = Depends(get_db_session)) -> ExcelAgentService:
    """Dependency for Excel agent service."""
    return ExcelAgentService(session)


async def process_in_background(
    data_source_id: str,
    user_id: str,
    session: AsyncSession,
    index_to_knowledge_base: bool = True,
) -> None:
    """Background task to process Excel file through agent pipeline and optionally index to knowledge base."""
    try:
        # Process through Excel agent pipeline
        excel_service = ExcelAgentService(session)
        await excel_service.process_data_source(
            data_source_id=data_source_id,
            user_id=user_id,
        )
        await session.commit()
        logger.info(
            "Excel agent processing completed",
            data_source_id=data_source_id,
        )

        # Index to knowledge base for vector search
        if index_to_knowledge_base:
            try:
                data_source_service = DataSourceService(session)
                indexing_result = await data_source_service.index_to_knowledge_base(
                    data_source_id=data_source_id,
                    user_id=user_id,
                )
                await session.commit()
                logger.info(
                    "Knowledge base indexing completed",
                    data_source_id=data_source_id,
                    chunks_indexed=indexing_result.chunk_count,
                    has_analysis=bool(indexing_result.analysis),
                )
            except Exception as kb_error:
                logger.error(
                    "Knowledge base indexing failed (non-fatal)",
                    data_source_id=data_source_id,
                    error=str(kb_error),
                    exc_info=True,
                )

    except Exception as e:
        logger.error(
            "Background processing failed",
            data_source_id=data_source_id,
            error=str(e),
            exc_info=True,
        )


@router.post("/upload", response_model=DataSourceResponse)
async def upload_sheet(
    name: str = Form(..., description="Data source display name"),
    file: UploadFile = File(..., description="Excel file (.xlsx, .xls, .xlsm)"),
    auto_process: bool = Form(default=True, description="Automatically process through Excel agent pipeline"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    data_source_service: DataSourceService = Depends(get_data_source_service),
    session: AsyncSession = Depends(get_db_session),
) -> DataSourceResponse:
    """Upload an Excel sheet and create a new data source.

    If auto_process is True (default), the file will be automatically
    processed through the Excel agent pipeline in the background.
    """
    data_source = await data_source_service.create_data_source(
        user_id=current_user.id,
        name=name,
        file=file,
    )

    # Trigger background processing if requested
    if auto_process:
        background_tasks.add_task(
            process_in_background,
            data_source_id=str(data_source.id),
            user_id=str(current_user.id),
            session=session,
        )
        logger.info(
            "Scheduled background processing",
            data_source_id=data_source.id,
        )

    return DataSourceResponse.model_validate(data_source)


@router.get("", response_model=DataSourceListResponse)
async def list_data_sources(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    data_source_service: DataSourceService = Depends(get_data_source_service),
) -> DataSourceListResponse:
    """List all data sources owned by the authenticated user."""
    items, total = await data_source_service.list_data_sources(
        user_id=current_user.id,
        skip=skip,
        limit=limit,
    )

    return DataSourceListResponse(
        items=[DataSourceResponse.model_validate(item) for item in items],
        total=total,
    )


@router.get("/{data_source_id}", response_model=DataSourceResponse)
async def get_data_source(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    data_source_service: DataSourceService = Depends(get_data_source_service),
) -> DataSourceResponse:
    """Get one data source by id for the authenticated user."""
    data_source = await data_source_service.get_data_source(current_user.id, data_source_id)
    return DataSourceResponse.model_validate(data_source)


@router.get("/{data_source_id}/analysis", response_model=WorkbookAnalysisResponse | None)
async def get_data_source_analysis(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    data_source_service: DataSourceService = Depends(get_data_source_service),
) -> WorkbookAnalysisResponse | None:
    """Get the workbook analysis for a data source.

    Returns the parsed analysis including sheet info, formulas, errors,
    and data patterns. Returns null if the data source hasn't been
    indexed yet.

    This endpoint is useful for polling after background processing
    to check if analysis is available.
    """
    data_source = await data_source_service.get_data_source(str(current_user.id), data_source_id)

    # Check if knowledge base indexing has been done
    knowledge_base_info = data_source.meta_info.get("knowledge_base", {})
    analysis = knowledge_base_info.get("analysis")

    return _convert_analysis_to_response(analysis)


@router.post("/{data_source_id}/index", response_model=IndexingResponse)
async def index_data_source(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    data_source_service: DataSourceService = Depends(get_data_source_service),
) -> IndexingResponse:
    """Manually trigger indexing of a data source to the knowledge base.

    Use this endpoint to re-index a data source or index one that was
    uploaded with auto_process=False.

    Returns indexing result including workbook analysis for UI display.
    """
    result = await data_source_service.index_to_knowledge_base(
        data_source_id=data_source_id,
        user_id=str(current_user.id),
    )

    return IndexingResponse(
        data_source_id=data_source_id,
        chunks_indexed=result.chunk_count,
        status="completed",
        analysis=_convert_analysis_to_response(result.analysis),
    )


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge_base(
    request: KnowledgeSearchRequest,
    current_user: User = Depends(get_current_user),
    data_source_service: DataSourceService = Depends(get_data_source_service),
) -> KnowledgeSearchResponse:
    """Search the knowledge base for relevant content.

    Performs semantic search across all indexed data sources or
    optionally filters by a specific data source.
    """
    results = await data_source_service.search_knowledge_base(
        query=request.query,
        user_id=current_user.id,
        data_source_id=request.data_source_id,
        limit=request.limit,
    )

    return KnowledgeSearchResponse(
        results=[KnowledgeSearchResult(**r) for r in results],
        total=len(results),
        query=request.query,
    )


@router.delete("/{data_source_id}/index")
async def delete_from_knowledge_base(
    data_source_id: str,
    current_user: User = Depends(get_current_user),
    data_source_service: DataSourceService = Depends(get_data_source_service),
) -> dict:
    """Delete a data source from the knowledge base.

    This removes the vector embeddings from Qdrant but keeps the
    original data source record and file.
    """
    deleted_count = await data_source_service.delete_from_knowledge_base(
        data_source_id=data_source_id,
        user_id=current_user.id,
    )

    return {
        "data_source_id": data_source_id,
        "chunks_deleted": deleted_count,
        "status": "deleted",
    }
