"""Semantic Mapper Agent.

This agent uses LLM to analyze the structural manifest from the Visual Metadata
Extractor and creates a semantic JSON map of the workbook that describes:
- What each section contains
- Column meanings and data types
- Relationships between sections
- Key metrics and their locations
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.agents.base import AgentResult, BaseAgent, get_llm_client
from core.agents.extractor import WorkbookManifest, manifest_to_dict
from core.logging import get_logger

logger = get_logger(__name__)


SEMANTIC_MAPPING_PROMPT = """You are an expert data analyst specializing in understanding complex Excel spreadsheets.

Analyze the following structural manifest of an Excel workbook and create a semantic map that describes:

1. **Overall Purpose**: What is this workbook likely used for?
2. **Sheet Analysis**: For each sheet, describe:
   - Primary purpose/content type
   - Key data columns and their meanings
   - Data types (dates, currency, percentages, text, etc.)
   - Relationships to other sheets if any
3. **Section Mapping**: For sheets with multiple sections:
   - What each section represents
   - How sections relate to each other
4. **Key Metrics**: Identify columns that likely contain important metrics
5. **Query Hints**: What types of questions could be answered from this data?

STRUCTURAL MANIFEST:
{manifest}

Respond with a JSON object following this exact schema:
{{
    "workbook_purpose": "string describing overall purpose",
    "sheets": {{
        "sheet_name": {{
            "purpose": "what this sheet contains",
            "primary_entity": "main data entity (e.g., 'transactions', 'employees')",
            "columns": [
                {{
                    "name": "column header",
                    "semantic_name": "human-friendly name",
                    "data_type": "string|number|currency|date|percentage|boolean",
                    "description": "what this column represents",
                    "is_key_metric": true/false,
                    "sample_values": ["if available from sample data"]
                }}
            ],
            "sections": [
                {{
                    "name": "section name",
                    "purpose": "what this section contains",
                    "row_range": "start-end",
                    "key_columns": ["important columns in this section"]
                }}
            ],
            "relationships": ["descriptions of relationships to other sheets"],
            "queryable_questions": ["example questions that can be answered"]
        }}
    }},
    "global_metrics": [
        {{
            "name": "metric name",
            "location": "Sheet!Column or Sheet!Cell",
            "description": "what this metric represents"
        }}
    ],
    "data_quality_notes": ["any concerns about data quality or structure"]
}}

Ensure your response is valid JSON only, with no additional text.
"""


@dataclass
class ColumnSchema:
    """Schema for a single column."""

    name: str
    semantic_name: str
    data_type: str
    description: str
    is_key_metric: bool = False
    sample_values: list[Any] = field(default_factory=list)


@dataclass
class SectionSchema:
    """Schema for a section within a sheet."""

    name: str
    purpose: str
    row_range: str
    key_columns: list[str] = field(default_factory=list)


@dataclass
class SheetSchema:
    """Semantic schema for a single sheet."""

    name: str
    purpose: str
    primary_entity: str
    columns: list[ColumnSchema] = field(default_factory=list)
    sections: list[SectionSchema] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    queryable_questions: list[str] = field(default_factory=list)


@dataclass
class GlobalMetric:
    """A key metric identified in the workbook."""

    name: str
    location: str
    description: str


@dataclass
class WorkbookSchema:
    """Complete semantic schema for a workbook."""

    workbook_purpose: str
    sheets: dict[str, SheetSchema] = field(default_factory=dict)
    global_metrics: list[GlobalMetric] = field(default_factory=list)
    data_quality_notes: list[str] = field(default_factory=list)
    raw_llm_response: dict | None = None


class SemanticMapper(BaseAgent):
    """Agent that creates semantic mappings of Excel workbooks using LLM.

    This agent takes the structural manifest from VisualMetadataExtractor
    and uses an LLM to understand and describe the semantic meaning of
    the data structure.
    """

    def __init__(self):
        """Initialize the Semantic Mapper agent."""
        super().__init__(name="SemanticMapper")

    async def execute(
        self,
        manifest: WorkbookManifest | dict,
    ) -> AgentResult:
        """Create semantic mapping from workbook manifest.

        Args:
            manifest: WorkbookManifest from VisualMetadataExtractor or dict.

        Returns:
            AgentResult containing WorkbookSchema or error.
        """
        self._log_start({"manifest_type": type(manifest).__name__})

        try:
            # Convert manifest to dict if needed
            if isinstance(manifest, WorkbookManifest):
                manifest_dict = manifest_to_dict(manifest)
            else:
                manifest_dict = manifest

            # Get LLM client
            llm = get_llm_client()

            # Create prompt
            prompt = SEMANTIC_MAPPING_PROMPT.format(
                manifest=json.dumps(manifest_dict, indent=2, default=str)
            )

            # Call LLM
            response = await llm.ainvoke(prompt)

            # Parse response
            response_text = response.content if hasattr(response, "content") else str(response)

            # Clean up response (remove markdown code blocks if present)
            response_text = self._clean_json_response(response_text)

            # Parse JSON
            try:
                schema_dict = json.loads(response_text)
            except json.JSONDecodeError as e:
                self.logger.warning(f"Failed to parse LLM response as JSON: {e}")
                # Try to extract JSON from the response
                schema_dict = self._extract_json_from_text(response_text)

            # Convert to WorkbookSchema
            workbook_schema = self._dict_to_schema(schema_dict)
            workbook_schema.raw_llm_response = schema_dict

            result = AgentResult(
                success=True,
                data=workbook_schema,
                metadata={
                    "sheets_mapped": len(workbook_schema.sheets),
                    "global_metrics_found": len(workbook_schema.global_metrics),
                },
            )
            self._log_complete(result)
            return result

        except Exception as e:
            self._log_error(e)
            return AgentResult(
                success=False,
                error=f"Failed to create semantic mapping: {str(e)}",
            )

    def _clean_json_response(self, text: str) -> str:
        """Clean JSON response from LLM (remove markdown, etc.)."""
        text = text.strip()

        # Remove markdown code blocks
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]

        if text.endswith("```"):
            text = text[:-3]

        return text.strip()

    def _extract_json_from_text(self, text: str) -> dict:
        """Try to extract JSON object from text."""
        # Find the first { and last }
        start = text.find("{")
        end = text.rfind("}")

        if start != -1 and end != -1 and end > start:
            json_str = text[start : end + 1]
            return json.loads(json_str)

        raise ValueError("Could not extract valid JSON from LLM response")

    def _dict_to_schema(self, data: dict) -> WorkbookSchema:
        """Convert dictionary to WorkbookSchema dataclass."""
        sheets = {}
        for sheet_name, sheet_data in data.get("sheets", {}).items():
            columns = [
                ColumnSchema(
                    name=col.get("name", ""),
                    semantic_name=col.get("semantic_name", col.get("name", "")),
                    data_type=col.get("data_type", "string"),
                    description=col.get("description", ""),
                    is_key_metric=col.get("is_key_metric", False),
                    sample_values=col.get("sample_values", []),
                )
                for col in sheet_data.get("columns", [])
            ]

            sections = [
                SectionSchema(
                    name=sec.get("name", ""),
                    purpose=sec.get("purpose", ""),
                    row_range=sec.get("row_range", ""),
                    key_columns=sec.get("key_columns", []),
                )
                for sec in sheet_data.get("sections", [])
            ]

            sheets[sheet_name] = SheetSchema(
                name=sheet_name,
                purpose=sheet_data.get("purpose", ""),
                primary_entity=sheet_data.get("primary_entity", ""),
                columns=columns,
                sections=sections,
                relationships=sheet_data.get("relationships", []),
                queryable_questions=sheet_data.get("queryable_questions", []),
            )

        global_metrics = [
            GlobalMetric(
                name=metric.get("name", ""),
                location=metric.get("location", ""),
                description=metric.get("description", ""),
            )
            for metric in data.get("global_metrics", [])
        ]

        return WorkbookSchema(
            workbook_purpose=data.get("workbook_purpose", ""),
            sheets=sheets,
            global_metrics=global_metrics,
            data_quality_notes=data.get("data_quality_notes", []),
        )


def schema_to_dict(schema: WorkbookSchema) -> dict:
    """Convert WorkbookSchema to JSON-serializable dictionary."""
    return {
        "workbook_purpose": schema.workbook_purpose,
        "sheets": {
            name: {
                "name": sheet.name,
                "purpose": sheet.purpose,
                "primary_entity": sheet.primary_entity,
                "columns": [
                    {
                        "name": col.name,
                        "semantic_name": col.semantic_name,
                        "data_type": col.data_type,
                        "description": col.description,
                        "is_key_metric": col.is_key_metric,
                        "sample_values": col.sample_values,
                    }
                    for col in sheet.columns
                ],
                "sections": [
                    {
                        "name": sec.name,
                        "purpose": sec.purpose,
                        "row_range": sec.row_range,
                        "key_columns": sec.key_columns,
                    }
                    for sec in sheet.sections
                ],
                "relationships": sheet.relationships,
                "queryable_questions": sheet.queryable_questions,
            }
            for name, sheet in schema.sheets.items()
        },
        "global_metrics": [
            {
                "name": metric.name,
                "location": metric.location,
                "description": metric.description,
            }
            for metric in schema.global_metrics
        ],
        "data_quality_notes": schema.data_quality_notes,
    }
