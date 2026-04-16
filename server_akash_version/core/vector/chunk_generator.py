"""Semantic chunk generator for Excel workbook analysis."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from core.logging import get_logger
from core.vector.excel_parser import (
    ColumnAnalysis,
    ExcelParser,
    SheetAnalysis,
    WorkbookAnalysis,
)

logger = get_logger(__name__)


@dataclass
class DocumentChunk:
    """Represents a chunk of document content for embedding."""

    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[list[float]] = None


@dataclass
class ChunkGenerationResult:
    """Result of chunk generation including chunks and analysis."""

    chunks: list[DocumentChunk]
    analysis: dict[str, Any]  # Serializable workbook analysis for DB storage
    workbook: Optional[WorkbookAnalysis] = None  # Full workbook analysis object


class SemanticChunkGenerator:
    """Generates semantic chunks from Excel workbook analysis."""

    def __init__(self, max_chunk_size: int = 1500):
        """Initialize the chunk generator.

        Args:
            max_chunk_size: Maximum characters per chunk
        """
        self._max_chunk_size = max_chunk_size
        self._parser = ExcelParser()

    def generate_chunks(
        self,
        file_content: bytes,
        file_name: str,
        data_source_id: str,
        user_id: str,
    ) -> ChunkGenerationResult:
        """Generate semantic chunks from an Excel file.

        Args:
            file_content: Raw file content
            file_name: Original file name
            data_source_id: ID of the data source
            user_id: ID of the user

        Returns:
            ChunkGenerationResult with chunks and workbook analysis
        """
        # Parse the workbook
        workbook = self._parser.parse_workbook(file_content, file_name)

        chunks: list[DocumentChunk] = []
        base_metadata = {
            "data_source_id": data_source_id,
            "user_id": user_id,
            "file_name": file_name,
        }

        # 1. Workbook Overview Chunk
        chunks.append(self._create_workbook_overview_chunk(workbook, base_metadata))

        # 2. For each sheet, create semantic chunks
        for sheet in workbook.sheets:
            sheet_metadata = {
                **base_metadata,
                "sheet_name": sheet.name,
            }

            # Sheet overview chunk
            chunks.append(self._create_sheet_overview_chunk(sheet, sheet_metadata))

            # Schema/Structure chunk
            chunks.append(self._create_schema_chunk(sheet, sheet_metadata))

            # Column analysis chunks
            chunks.extend(self._create_column_chunks(sheet, sheet_metadata))

            # Formula analysis chunk (if formulas exist)
            if sheet.formulas:
                chunks.append(self._create_formula_chunk(sheet, sheet_metadata))

            # Error analysis chunk (if errors exist)
            if sheet.errors:
                chunks.append(self._create_error_chunk(sheet, sheet_metadata))

            # Data patterns chunk
            if sheet.data_patterns:
                chunks.append(self._create_patterns_chunk(sheet, sheet_metadata))

            # Statistics chunk
            if sheet.summary_statistics:
                chunks.append(self._create_statistics_chunk(sheet, sheet_metadata))

        # 3. Relationships chunk (if multiple sheets with relationships)
        if len(workbook.sheets) > 1 and workbook.relationships:
            chunks.append(self._create_relationships_chunk(workbook, base_metadata))

        logger.info(
            "Generated semantic chunks",
            file_name=file_name,
            chunk_count=len(chunks),
            sheet_count=len(workbook.sheets),
        )

        # Return chunks along with serializable analysis for DB storage
        return ChunkGenerationResult(
            chunks=chunks,
            analysis=workbook.to_dict(),
            workbook=workbook,
        )

    def _create_workbook_overview_chunk(
        self,
        workbook: WorkbookAnalysis,
        base_metadata: dict,
    ) -> DocumentChunk:
        """Create an overview chunk for the entire workbook."""
        content_parts = [
            f"# Workbook Overview: {workbook.file_name}",
            "",
            f"## Summary",
            f"- Total Sheets: {workbook.sheet_count}",
            f"- Total Formulas: {workbook.total_formulas}",
            f"- Total Errors: {workbook.total_errors}",
        ]

        # Document properties
        if workbook.document_properties:
            props = workbook.document_properties
            if props.get("title"):
                content_parts.append(f"- Document Title: {props['title']}")
            if props.get("subject"):
                content_parts.append(f"- Subject: {props['subject']}")
            if props.get("creator"):
                content_parts.append(f"- Created By: {props['creator']}")
            if props.get("description"):
                content_parts.append(f"- Description: {props['description']}")

        content_parts.append("")
        content_parts.append("## Sheet List")
        for sheet in workbook.sheets:
            content_parts.append(
                f"- **{sheet.name}**: {sheet.row_count} rows, {sheet.column_count} columns"
                f" | Purpose: {sheet.inferred_purpose or 'General'}"
            )

        if workbook.overall_purpose:
            content_parts.append("")
            content_parts.append(f"## Inferred Purpose")
            content_parts.append(workbook.overall_purpose)

        if workbook.named_ranges:
            content_parts.append("")
            content_parts.append(f"## Named Ranges")
            content_parts.append(", ".join(workbook.named_ranges[:20]))

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **base_metadata,
                "chunk_type": "workbook_overview",
                "sheet_count": workbook.sheet_count,
                "total_formulas": workbook.total_formulas,
                "total_errors": workbook.total_errors,
            },
        )

    def _create_sheet_overview_chunk(
        self,
        sheet: SheetAnalysis,
        metadata: dict,
    ) -> DocumentChunk:
        """Create an overview chunk for a single sheet."""
        content_parts = [
            f"# Sheet: {sheet.name}",
            "",
            f"## Structure",
            f"- Rows: {sheet.row_count}",
            f"- Columns: {sheet.column_count}",
            f"- Data Range: {sheet.data_range}",
            f"- Merged Cells: {len(sheet.merged_cells)}",
            f"- Comments: {len(sheet.comments)}",
            f"- Formulas: {len(sheet.formulas)}",
            f"- Errors: {len(sheet.errors)}",
        ]

        if sheet.inferred_purpose:
            content_parts.append("")
            content_parts.append(f"## Purpose")
            content_parts.append(sheet.inferred_purpose)

        # Data regions
        if sheet.data_regions:
            content_parts.append("")
            content_parts.append("## Data Regions")
            for region in sheet.data_regions:
                content_parts.append(
                    f"- **{region['type'].title()}** ({region['range']}): {region.get('description', '')}"
                )

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **metadata,
                "chunk_type": "sheet_overview",
                "row_count": sheet.row_count,
                "column_count": sheet.column_count,
            },
        )

    def _create_schema_chunk(
        self,
        sheet: SheetAnalysis,
        metadata: dict,
    ) -> DocumentChunk:
        """Create a schema/structure chunk describing the columns."""
        content_parts = [
            f"# Schema for Sheet: {sheet.name}",
            "",
            "## Column Definitions",
        ]

        for col in sheet.columns:
            col_info = [
                f"### {col.name} (Column {col.letter})",
                f"- Data Type: {col.data_type}",
                f"- Non-null Values: {col.non_null_count}",
                f"- Unique Values: {col.unique_count}",
            ]

            if col.inferred_purpose:
                col_info.append(f"- Purpose: {col.inferred_purpose}")

            if col.has_formulas:
                col_info.append(f"- Contains {col.formula_count} formula(s)")

            if col.has_errors:
                col_info.append(f"- Contains {col.error_count} error(s)")

            if col.sample_values:
                samples = [str(v)[:50] for v in col.sample_values[:3]]
                col_info.append(f"- Sample Values: {', '.join(samples)}")

            if col.min_value is not None and col.max_value is not None:
                col_info.append(f"- Range: {col.min_value} to {col.max_value}")

            content_parts.extend(col_info)
            content_parts.append("")

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **metadata,
                "chunk_type": "schema",
                "column_count": len(sheet.columns),
            },
        )

    def _create_column_chunks(
        self,
        sheet: SheetAnalysis,
        metadata: dict,
    ) -> list[DocumentChunk]:
        """Create detailed chunks for columns with significant data."""
        chunks = []

        # Group columns by their inferred purpose
        purpose_groups: dict[str, list[ColumnAnalysis]] = {}
        for col in sheet.columns:
            purpose = col.inferred_purpose or "general"
            if purpose not in purpose_groups:
                purpose_groups[purpose] = []
            purpose_groups[purpose].append(col)

        for purpose, columns in purpose_groups.items():
            if len(columns) == 0:
                continue

            content_parts = [
                f"# {purpose.title()} Columns in {sheet.name}",
                "",
            ]

            for col in columns:
                content_parts.append(f"## {col.name}")
                content_parts.append(f"- Type: {col.data_type}")
                content_parts.append(f"- Values: {col.non_null_count} non-null, {col.unique_count} unique")

                if col.mean_value is not None:
                    content_parts.append(f"- Average: {col.mean_value:.2f}")

                if col.sample_values:
                    samples = [str(v)[:30] for v in col.sample_values[:5]]
                    content_parts.append(f"- Examples: {', '.join(samples)}")

                content_parts.append("")

            chunks.append(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    content="\n".join(content_parts),
                    metadata={
                        **metadata,
                        "chunk_type": "column_analysis",
                        "column_purpose": purpose,
                        "column_count": len(columns),
                    },
                )
            )

        return chunks

    def _create_formula_chunk(
        self,
        sheet: SheetAnalysis,
        metadata: dict,
    ) -> DocumentChunk:
        """Create a chunk describing formulas in the sheet."""
        content_parts = [
            f"# Formulas in Sheet: {sheet.name}",
            "",
            f"Total Formulas: {len(sheet.formulas)}",
            "",
        ]

        # Group by category
        by_category: dict[str, list] = {}
        for formula in sheet.formulas:
            cat = formula.get("category", "other")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(formula)

        for category, formulas in by_category.items():
            content_parts.append(f"## {category.title()} Formulas ({len(formulas)})")

            for f in formulas[:10]:  # Limit to 10 per category
                content_parts.append(f"- Cell {f['address']}: `{f['formula'][:100]}`")

            if len(formulas) > 10:
                content_parts.append(f"  ... and {len(formulas) - 10} more")

            content_parts.append("")

        # Describe what these formulas do
        content_parts.append("## Formula Analysis")
        if "mathematical" in by_category:
            content_parts.append("- Contains SUM/AVERAGE/COUNT operations for aggregation")
        if "logical" in by_category:
            content_parts.append("- Contains IF/AND/OR logic for conditional calculations")
        if "lookup" in by_category:
            content_parts.append("- Contains VLOOKUP/INDEX/MATCH for data lookups")
        if "financial" in by_category:
            content_parts.append("- Contains financial functions (PMT, NPV, etc.)")

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **metadata,
                "chunk_type": "formula_analysis",
                "formula_count": len(sheet.formulas),
                "formula_categories": list(by_category.keys()),
            },
        )

    def _create_error_chunk(
        self,
        sheet: SheetAnalysis,
        metadata: dict,
    ) -> DocumentChunk:
        """Create a chunk describing errors in the sheet."""
        content_parts = [
            f"# Errors in Sheet: {sheet.name}",
            "",
            f"Total Errors: {len(sheet.errors)}",
            "",
        ]

        # Group by error type
        by_type: dict[str, list] = {}
        for error in sheet.errors:
            error_type = error.get("error_type", "Unknown")
            if error_type not in by_type:
                by_type[error_type] = []
            by_type[error_type].append(error)

        for error_type, errors in by_type.items():
            desc = errors[0].get("description", "Unknown error")
            content_parts.append(f"## {error_type} ({len(errors)} occurrences)")
            content_parts.append(f"Description: {desc}")
            content_parts.append("Locations:")

            for e in errors[:10]:
                if e.get("formula"):
                    content_parts.append(f"- Cell {e['address']}: Formula `{e['formula'][:50]}`")
                else:
                    content_parts.append(f"- Cell {e['address']}")

            if len(errors) > 10:
                content_parts.append(f"  ... and {len(errors) - 10} more")

            content_parts.append("")

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **metadata,
                "chunk_type": "error_analysis",
                "error_count": len(sheet.errors),
                "error_types": list(by_type.keys()),
            },
        )

    def _create_patterns_chunk(
        self,
        sheet: SheetAnalysis,
        metadata: dict,
    ) -> DocumentChunk:
        """Create a chunk describing data patterns in the sheet."""
        content_parts = [
            f"# Data Patterns in Sheet: {sheet.name}",
            "",
            "## Detected Patterns",
        ]

        for pattern in sheet.data_patterns:
            content_parts.append(f"- {pattern}")

        # Add column purpose distribution
        content_parts.append("")
        content_parts.append("## Column Types")

        purpose_counts: dict[str, int] = {}
        for col in sheet.columns:
            purpose = col.inferred_purpose or "general"
            purpose_counts[purpose] = purpose_counts.get(purpose, 0) + 1

        for purpose, count in sorted(purpose_counts.items(), key=lambda x: -x[1]):
            content_parts.append(f"- {purpose}: {count} column(s)")

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **metadata,
                "chunk_type": "data_patterns",
                "pattern_count": len(sheet.data_patterns),
            },
        )

    def _create_statistics_chunk(
        self,
        sheet: SheetAnalysis,
        metadata: dict,
    ) -> DocumentChunk:
        """Create a chunk with statistical summaries."""
        content_parts = [
            f"# Statistics for Sheet: {sheet.name}",
            "",
        ]

        if isinstance(sheet.summary_statistics, dict) and "message" not in sheet.summary_statistics:
            content_parts.append("## Numeric Column Statistics")
            content_parts.append("")

            for col_name, stats in sheet.summary_statistics.items():
                content_parts.append(f"### {col_name}")
                content_parts.append(f"- Count: {stats.get('count', 'N/A')}")
                content_parts.append(f"- Sum: {stats.get('sum', 'N/A')}")
                content_parts.append(f"- Mean: {stats.get('mean', 'N/A')}")
                content_parts.append(f"- Std Dev: {stats.get('std', 'N/A')}")
                content_parts.append(f"- Min: {stats.get('min', 'N/A')}")
                content_parts.append(f"- Max: {stats.get('max', 'N/A')}")
                content_parts.append("")
        else:
            content_parts.append("No numeric columns with statistics available.")

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **metadata,
                "chunk_type": "statistics",
            },
        )

    def _create_relationships_chunk(
        self,
        workbook: WorkbookAnalysis,
        metadata: dict,
    ) -> DocumentChunk:
        """Create a chunk describing relationships between sheets."""
        content_parts = [
            f"# Sheet Relationships in {workbook.file_name}",
            "",
            "## Cross-Sheet References",
        ]

        # Group by source sheet
        by_source: dict[str, list] = {}
        for rel in workbook.relationships:
            source = rel.get("from_sheet", "Unknown")
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(rel)

        for source, rels in by_source.items():
            content_parts.append(f"### From: {source}")
            for rel in rels:
                content_parts.append(
                    f"- References **{rel['to_sheet']}** at {rel.get('formula_location', 'unknown location')}"
                )
            content_parts.append("")

        return DocumentChunk(
            id=str(uuid.uuid4()),
            content="\n".join(content_parts),
            metadata={
                **metadata,
                "chunk_type": "relationships",
                "relationship_count": len(workbook.relationships),
            },
        )
