"""Excel Agent Orchestrator.

This module provides the main orchestrator that coordinates all agents
in the Excel processing pipeline:
1. Visual Metadata Extractor - extracts structural metadata
2. Semantic Mapper - creates semantic schema using LLM (legacy)
3. Semantic Enricher - creates rich semantic enrichment with domain, metrics, etc.
4. Code Executor - answers questions by generating and running code
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.agents.base import AgentResult, BaseAgent
from core.agents.extractor import (
    VisualMetadataExtractor,
    WorkbookManifest,
    manifest_to_dict,
)
from core.agents.mapper import SemanticMapper, WorkbookSchema, schema_to_dict
from core.agents.semantic_enricher import (
    SemanticEnricher,
    WorkbookEnrichment,
    enrichment_to_dict,
)
from core.agents.executor import CodeExecutorAgent
from core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ProcessedWorkbook:
    """Complete processed workbook with all metadata."""

    file_path: str
    manifest: WorkbookManifest | None = None
    schema: WorkbookSchema | None = None
    enrichment: WorkbookEnrichment | None = None
    manifest_dict: dict = field(default_factory=dict)
    schema_dict: dict = field(default_factory=dict)
    enrichment_dict: dict = field(default_factory=dict)
    processing_errors: list[str] = field(default_factory=list)
    is_ready_for_queries: bool = False

    # Quick access to enrichment fields
    @property
    def context_header_for_qa(self) -> str | None:
        """Get the context header for Q&A injection."""
        if self.enrichment:
            return self.enrichment.context_header_for_qa
        return None

    @property
    def workbook_title(self) -> str | None:
        """Get the enriched workbook title."""
        if self.enrichment:
            return self.enrichment.workbook_title
        return None

    @property
    def domain(self) -> str | None:
        """Get the workbook domain."""
        if self.enrichment:
            return self.enrichment.domain
        return None


class ExcelAgentOrchestrator(BaseAgent):
    """Orchestrator that coordinates all Excel processing agents.

    This is the main entry point for:
    1. Processing Excel files (extract + map + enrich)
    2. Answering questions about processed files
    """

    def __init__(self):
        """Initialize the orchestrator with all sub-agents."""
        super().__init__(name="ExcelAgentOrchestrator")

        # Initialize sub-agents
        self.extractor = VisualMetadataExtractor()
        self.mapper = SemanticMapper()
        self.enricher = SemanticEnricher()
        self.executor = CodeExecutorAgent()

        # Cache for processed workbooks
        self._processed_cache: dict[str, ProcessedWorkbook] = {}

    async def execute(self, *args: Any, **kwargs: Any) -> AgentResult:
        """Default execute method - delegates to process_workbook."""
        file_path = kwargs.get("file_path") or (args[0] if args else None)
        if not file_path:
            return AgentResult(
                success=False,
                error="file_path is required",
            )
        return await self.process_workbook(file_path)

    async def process_workbook(
        self,
        file_path: str | None = None,
        file_content: bytes | None = None,
        force_reprocess: bool = False,
        skip_enrichment: bool = False,
    ) -> AgentResult:
        """Process an Excel workbook through the full pipeline.

        Phase A: Extract visual metadata (colors, merged cells, structure)
        Phase B: Create semantic mapping using LLM (legacy)
        Phase C: Create rich semantic enrichment (domain, metrics, context header)

        Args:
            file_path: Path to the Excel file.
            file_content: Raw bytes of the Excel file (alternative to path).
            force_reprocess: Force reprocessing even if cached.
            skip_enrichment: Skip the enrichment phase (use legacy mapping only).

        Returns:
            AgentResult containing ProcessedWorkbook or error.
        """
        self._log_start({"file_path": file_path})

        cache_key = file_path or "in-memory"

        # Check cache
        if not force_reprocess and cache_key in self._processed_cache:
            cached = self._processed_cache[cache_key]
            if cached.is_ready_for_queries:
                self.logger.info("Returning cached processed workbook")
                return AgentResult(
                    success=True,
                    data=cached,
                    metadata={"from_cache": True},
                )

        processed = ProcessedWorkbook(file_path=cache_key)

        try:
            # Phase A: Extract visual metadata
            self.logger.info("Phase A: Extracting visual metadata")
            extract_result = await self.extractor.execute(
                file_path=file_path,
                file_content=file_content,
                include_sample_data=True,
                sample_rows=10,  # Increased for better enrichment
            )

            if not extract_result.success:
                processed.processing_errors.append(
                    f"Extraction failed: {extract_result.error}"
                )
                return AgentResult(
                    success=False,
                    error=extract_result.error,
                    data=processed,
                )

            processed.manifest = extract_result.data
            processed.manifest_dict = manifest_to_dict(processed.manifest)

            # Phase B: Create semantic mapping (legacy - still useful as fallback)
            self.logger.info("Phase B: Creating semantic mapping")
            map_result = await self.mapper.execute(manifest=processed.manifest)

            if not map_result.success:
                processed.processing_errors.append(
                    f"Mapping failed: {map_result.error}"
                )
                self.logger.warning("Semantic mapping failed, but extraction succeeded")
            else:
                processed.schema = map_result.data
                processed.schema_dict = schema_to_dict(processed.schema)

            # Phase C: Create rich semantic enrichment
            if not skip_enrichment:
                self.logger.info("Phase C: Creating semantic enrichment")
                enrich_result = await self.enricher.execute(
                    manifest=processed.manifest_dict,
                    parallel_sheets=True,
                )

                if not enrich_result.success:
                    processed.processing_errors.append(
                        f"Enrichment failed: {enrich_result.error}"
                    )
                    self.logger.warning("Semantic enrichment failed, using legacy schema")
                else:
                    processed.enrichment = enrich_result.data
                    processed.enrichment_dict = enrichment_to_dict(processed.enrichment)

            # Ready if we have either schema or enrichment
            processed.is_ready_for_queries = (
                processed.schema is not None or processed.enrichment is not None
            )

            # Cache the result
            self._processed_cache[cache_key] = processed

            result = AgentResult(
                success=True,
                data=processed,
                metadata={
                    "manifest_extracted": processed.manifest is not None,
                    "schema_created": processed.schema is not None,
                    "enrichment_created": processed.enrichment is not None,
                    "ready_for_queries": processed.is_ready_for_queries,
                    "domain": processed.domain,
                    "has_context_header": processed.context_header_for_qa is not None,
                },
            )
            self._log_complete(result)
            return result

        except Exception as e:
            self._log_error(e)
            processed.processing_errors.append(str(e))
            return AgentResult(
                success=False,
                error=f"Processing failed: {str(e)}",
                data=processed,
            )

    async def ask_question(
        self,
        question: str,
        file_path: str,
        schema: WorkbookSchema | dict | None = None,
    ) -> AgentResult:
        """Ask a natural language question about an Excel file.

        Phase C: Code Executor generates and runs Python code to answer.

        Args:
            question: Natural language question about the data.
            file_path: Path to the Excel file.
            schema: Pre-computed schema (optional, will use cached or compute).

        Returns:
            AgentResult containing the answer or error.
        """
        self._log_start({"question": question, "file_path": file_path})

        try:
            # Get or compute schema
            if schema is None:
                # Check cache first
                if file_path in self._processed_cache:
                    cached = self._processed_cache[file_path]
                    if cached.schema:
                        schema = cached.schema
                    elif cached.schema_dict:
                        schema = cached.schema_dict

                # If still no schema, process the workbook
                if schema is None:
                    self.logger.info("No cached schema found, processing workbook")
                    process_result = await self.process_workbook(file_path=file_path)
                    if not process_result.success:
                        return process_result

                    processed = process_result.data
                    if not processed.is_ready_for_queries:
                        return AgentResult(
                            success=False,
                            error="Workbook processed but schema creation failed",
                        )
                    schema = processed.schema

            # Execute question with code executor
            result = await self.executor.execute(
                question=question,
                file_path=file_path,
                schema=schema,
            )

            self._log_complete(result)
            return result

        except Exception as e:
            self._log_error(e)
            return AgentResult(
                success=False,
                error=f"Failed to answer question: {str(e)}",
            )

    async def get_workbook_info(self, file_path: str) -> AgentResult:
        """Get information about a processed workbook.

        Args:
            file_path: Path to the Excel file.

        Returns:
            AgentResult containing workbook info or error.
        """
        if file_path not in self._processed_cache:
            # Try to process it
            result = await self.process_workbook(file_path=file_path)
            if not result.success:
                return result

        processed = self._processed_cache[file_path]

        info = {
            "file_path": processed.file_path,
            "is_ready_for_queries": processed.is_ready_for_queries,
            "processing_errors": processed.processing_errors,
        }

        if processed.manifest:
            info["manifest"] = {
                "sheet_count": processed.manifest.sheet_count,
                "sheet_names": processed.manifest.sheet_names,
                "total_merged_regions": processed.manifest.total_merged_regions,
                "total_sections": processed.manifest.total_sections,
                "detected_colors": processed.manifest.detected_colors,
            }

        if processed.schema:
            info["schema"] = {
                "workbook_purpose": processed.schema.workbook_purpose,
                "sheets": list(processed.schema.sheets.keys()),
                "global_metrics_count": len(processed.schema.global_metrics),
                "data_quality_notes": processed.schema.data_quality_notes,
            }

            # Include queryable questions for each sheet
            info["queryable_questions"] = {}
            for sheet_name, sheet in processed.schema.sheets.items():
                info["queryable_questions"][sheet_name] = sheet.queryable_questions

        # Include enrichment info if available
        if processed.enrichment:
            info["enrichment"] = {
                "workbook_title": processed.enrichment.workbook_title,
                "workbook_purpose": processed.enrichment.workbook_purpose,
                "domain": processed.enrichment.domain,
                "context_header_for_qa": processed.enrichment.context_header_for_qa,
                "global_metrics": processed.enrichment.global_metrics,
                "sheet_index": [
                    {
                        "sheet_name": s.sheet_name,
                        "semantic_title": s.semantic_title,
                        "role": s.role,
                        "best_for": s.best_for,
                    }
                    for s in processed.enrichment.sheet_index
                ],
                "cross_sheet_relationships": [
                    {
                        "description": r.description,
                        "sheets_involved": r.sheets_involved,
                        "relationship_type": r.relationship_type,
                    }
                    for r in processed.enrichment.cross_sheet_relationships
                ],
                "query_routing": {
                    "schema_questions": processed.enrichment.recommended_query_routing.schema_questions,
                    "financial_questions": processed.enrichment.recommended_query_routing.financial_questions,
                    "trend_questions": processed.enrichment.recommended_query_routing.trend_questions,
                    "lookup_questions": processed.enrichment.recommended_query_routing.lookup_questions,
                },
            }

            # Include enriched answerable questions from all sheets
            info["answerable_question_types"] = {}
            for sheet_name, sheet in processed.enrichment.sheets.items():
                info["answerable_question_types"][sheet_name] = sheet.answerable_question_types

        return AgentResult(
            success=True,
            data=info,
        )

    def clear_cache(self, file_path: str | None = None) -> None:
        """Clear cached processed workbooks.

        Args:
            file_path: Specific file to clear, or None to clear all.
        """
        if file_path:
            self._processed_cache.pop(file_path, None)
        else:
            self._processed_cache.clear()

    def get_cached_schema(self, file_path: str) -> dict | None:
        """Get cached schema for a file path.

        Args:
            file_path: Path to the Excel file.

        Returns:
            Schema dict or None if not cached.
        """
        if file_path in self._processed_cache:
            return self._processed_cache[file_path].schema_dict
        return None

    def get_cached_manifest(self, file_path: str) -> dict | None:
        """Get cached manifest for a file path.

        Args:
            file_path: Path to the Excel file.

        Returns:
            Manifest dict or None if not cached.
        """
        if file_path in self._processed_cache:
            return self._processed_cache[file_path].manifest_dict
        return None

    def get_cached_enrichment(self, file_path: str) -> dict | None:
        """Get cached enrichment for a file path.

        Args:
            file_path: Path to the Excel file.

        Returns:
            Enrichment dict or None if not cached.
        """
        if file_path in self._processed_cache:
            return self._processed_cache[file_path].enrichment_dict
        return None

    def get_context_header(self, file_path: str) -> str | None:
        """Get the context header for Q&A injection.

        Args:
            file_path: Path to the Excel file.

        Returns:
            Context header string or None if not available.
        """
        if file_path in self._processed_cache:
            return self._processed_cache[file_path].context_header_for_qa
        return None


# Singleton instance for application-wide use
_orchestrator_instance: ExcelAgentOrchestrator | None = None


def get_orchestrator() -> ExcelAgentOrchestrator:
    """Get the singleton orchestrator instance."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = ExcelAgentOrchestrator()
    return _orchestrator_instance
