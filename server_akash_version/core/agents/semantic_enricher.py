"""Semantic Enricher Agent.

This agent performs deep semantic analysis of Excel sheets using LLM to create
rich metadata that powers the Q&A system. It produces:
- Sheet-level semantic enrichment (domain, metrics, dimensions, time analysis)
- Workbook-level summary with cross-sheet relationships
- Query routing recommendations
- Context headers for Q&A injection
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.agents.base import AgentResult, BaseAgent, get_llm_client
from core.agents.extractor import WorkbookManifest, SheetManifest, manifest_to_dict
from core.logging import get_logger
from core.models.conversation import LLMUsage, LLMCallType

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# PROMPT TEMPLATES
# ---------------------------------------------------------------------------

SHEET_ENRICHMENT_SYSTEM = """You are a senior data analyst specialising in interpreting
structured and semi-structured Excel spreadsheets. Your job is to examine a raw structural
extract of a single Excel sheet and produce a rich semantic description that will power a
question-answering system.

The files you will encounter include:
- Financial reports: P&L statements, balance sheets, income statements, cash flow
- Sales / CRM exports: pipeline data, account lists, deal stages, revenue by rep
- Operations / inventory: stock levels, SKUs, warehouse data, order fulfilment
- General-purpose workbooks: ad-hoc analyses, project trackers, mixed content

Your output MUST be valid JSON and nothing else. No preamble, no explanation, no markdown
fences. Return only the JSON object described in the user message."""


WORKBOOK_SUMMARY_SYSTEM = """You are a senior data analyst. You have been given enriched
descriptions of every sheet in an Excel workbook. Your job is to produce a workbook-level
summary that a question-answering system will inject at the top of every query prompt.

Output MUST be valid JSON only. No preamble, no markdown fences."""


def build_sheet_enrichment_prompt(sheet_extract: dict) -> str:
    """Build the user-turn prompt for a single sheet enrichment pass."""
    return f"""Analyse this structural extract from an Excel sheet and return a JSON object
with the exact schema shown below. Think carefully about what the sheet is *for*, not just
what it contains.

## STRUCTURAL EXTRACT
```json
{json.dumps(sheet_extract, indent=2, default=str)}
```

## REQUIRED OUTPUT SCHEMA
Return this exact JSON structure (all fields required, use null if unknown):

{{
  "sheet_name": "<original sheet name>",
  "semantic_title": "<human-readable title inferred from content, e.g. 'Monthly P&L FY2024'>",
  "domain": "<one of: financial | sales_crm | operations_inventory | general>",
  "primary_purpose": "<1–2 sentence description of what this sheet is for>",
  "time_dimension": {{
    "present": true | false,
    "grain": "<daily | weekly | monthly | quarterly | annual | none>",
    "range_start": "<inferred start period or null>",
    "range_end": "<inferred end period or null>",
    "time_column": "<column name containing dates, or null>"
  }},
  "key_metrics": [
    {{
      "name": "<metric name>",
      "column_or_cell": "<where it lives>",
      "unit": "<USD | GBP | % | units | count | null>",
      "description": "<what this metric represents>",
      "can_aggregate": true | false
    }}
  ],
  "dimensions": [
    {{
      "name": "<dimension name, e.g. 'Product Category', 'Sales Rep', 'Region'>",
      "column": "<column name>",
      "cardinality": "<low (<=10) | medium (11–100) | high (>100) | unknown>",
      "sample_values": ["<up to 5 examples>"]
    }}
  ],
  "detected_tables": [
    {{
      "table_id": "<short id, e.g. 'tbl_pnl_monthly'>",
      "label": "<the section heading above this table, if any>",
      "header_row": <row number>,
      "data_start_row": <row number>,
      "data_end_row": <row number or null>,
      "row_count_approx": <int>,
      "columns": [
        {{
          "name": "<column header text>",
          "inferred_type": "<currency | percentage | integer | float | date | boolean | category | text | formula_result>",
          "unit": "<USD | GBP | EUR | % | days | units | null>",
          "nullable": true | false,
          "is_calculated": true | false,
          "semantic_role": "<metric | dimension | date | identifier | label | subtotal | unknown>"
        }}
      ],
      "has_subtotals": true | false,
      "has_grand_total": true | false,
      "notes": "<any structural quirks, e.g. 'merged header spans Q1-Q4', 'alternating row layout'>"
    }}
  ],
  "section_labels": [
    {{
      "text": "<label text>",
      "cell_range": "<e.g. B2:F2>",
      "likely_meaning": "<what section this introduces>"
    }}
  ],
  "answerable_question_types": [
    "<list of natural language question patterns this sheet can answer, e.g.>",
    "What was [metric] in [period]?",
    "Which [dimension] had the highest [metric]?",
    "How did [metric] change over time?",
    "What is the total [metric] for [filter]?"
  ],
  "data_quality_flags": [
    {{
      "type": "<missing_headers | mixed_types | sparse_data | inconsistent_dates | probable_formula_errors | none>",
      "location": "<cell range or column name>",
      "description": "<brief note>"
    }}
  ],
  "retrieval_hints": {{
    "best_chunk_strategy": "<by_row | by_column_group | by_section | full_table>",
    "suggested_chunk_size": "<number of rows per chunk for RAG>",
    "key_filter_columns": ["<columns that are useful for filtering before retrieval>"],
    "always_include_in_context": ["<column names that must always be included in every chunk for context>"]
  }},
  "confidence": "<high | medium | low — how confident are you in this enrichment given the extract quality>"
}}"""


def build_workbook_summary_prompt(enriched_sheets: list[dict]) -> str:
    """Build the prompt for the workbook-level summary pass."""
    sheet_summaries = [
        {
            "sheet_name": s.get("sheet_name"),
            "semantic_title": s.get("semantic_title"),
            "domain": s.get("domain"),
            "primary_purpose": s.get("primary_purpose"),
            "key_metrics": [m["name"] for m in s.get("key_metrics", [])],
            "time_dimension": s.get("time_dimension"),
            "answerable_question_types": s.get("answerable_question_types", [])[:4],
        }
        for s in enriched_sheets
    ]

    return f"""Given these enriched sheet summaries from a single workbook, produce a
workbook-level JSON summary.

## SHEET SUMMARIES
```json
{json.dumps(sheet_summaries, indent=2)}
```

## REQUIRED OUTPUT SCHEMA

{{
  "workbook_title": "<inferred name for the whole workbook>",
  "workbook_purpose": "<2–3 sentences: what is this file, who likely made it, what decisions does it support>",
  "domain": "<primary domain: financial | sales_crm | operations_inventory | general | mixed>",
  "sheet_index": [
    {{
      "sheet_name": "<name>",
      "semantic_title": "<title>",
      "role": "<primary_data | summary | dashboard | reference | lookup | scratch | unknown>",
      "best_for": "<what question types this sheet handles best>"
    }}
  ],
  "cross_sheet_relationships": [
    {{
      "description": "<e.g. 'Summary sheet aggregates data from PnL and BalanceSheet tabs'>",
      "sheets_involved": ["<sheet names>"],
      "relationship_type": "<aggregation | lookup | drill_down | duplicate | linked_range>"
    }}
  ],
  "global_metrics": ["<metrics that appear across multiple sheets>"],
  "recommended_query_routing": {{
    "schema_questions": "<which sheet or store to query for 'what tabs exist / what columns are there'>",
    "financial_questions": "<sheet name(s) to prioritise>",
    "trend_questions": "<sheet name(s) to prioritise>",
    "lookup_questions": "<sheet name(s) to prioritise>"
  }},
  "context_header_for_qa": "<A concise 3–5 sentence paragraph that will be injected at the top of every Q&A prompt to ground the LLM. Written in plain English, not JSON. Should describe what the file is, what sheets exist, key metrics, and time coverage.>"
}}"""


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class TimeDimension:
    """Time dimension analysis for a sheet."""
    present: bool = False
    grain: str | None = None  # daily, weekly, monthly, quarterly, annual, none
    range_start: str | None = None
    range_end: str | None = None
    time_column: str | None = None


@dataclass
class KeyMetric:
    """A key metric identified in the sheet."""
    name: str
    column_or_cell: str
    unit: str | None = None
    description: str = ""
    can_aggregate: bool = True


@dataclass
class Dimension:
    """A dimension (categorical column) in the sheet."""
    name: str
    column: str
    cardinality: str = "unknown"  # low, medium, high, unknown
    sample_values: list[str] = field(default_factory=list)


@dataclass
class TableColumn:
    """Column metadata for a detected table."""
    name: str
    inferred_type: str = "text"
    unit: str | None = None
    nullable: bool = True
    is_calculated: bool = False
    semantic_role: str = "unknown"


@dataclass
class DetectedTable:
    """A table detected within the sheet."""
    table_id: str
    label: str | None = None
    header_row: int | None = None
    data_start_row: int | None = None
    data_end_row: int | None = None
    row_count_approx: int = 0
    columns: list[TableColumn] = field(default_factory=list)
    has_subtotals: bool = False
    has_grand_total: bool = False
    notes: str | None = None


@dataclass
class SectionLabel:
    """A section label detected in the sheet."""
    text: str
    cell_range: str
    likely_meaning: str = ""


@dataclass
class DataQualityFlag:
    """A data quality issue detected in the sheet."""
    type: str
    location: str
    description: str


@dataclass
class RetrievalHints:
    """Hints for RAG retrieval strategy."""
    best_chunk_strategy: str = "by_row"
    suggested_chunk_size: int = 50
    key_filter_columns: list[str] = field(default_factory=list)
    always_include_in_context: list[str] = field(default_factory=list)


@dataclass
class SheetEnrichment:
    """Complete semantic enrichment for a single sheet."""
    sheet_name: str
    semantic_title: str = ""
    domain: str = "general"
    primary_purpose: str = ""
    time_dimension: TimeDimension = field(default_factory=TimeDimension)
    key_metrics: list[KeyMetric] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    detected_tables: list[DetectedTable] = field(default_factory=list)
    section_labels: list[SectionLabel] = field(default_factory=list)
    answerable_question_types: list[str] = field(default_factory=list)
    data_quality_flags: list[DataQualityFlag] = field(default_factory=list)
    retrieval_hints: RetrievalHints = field(default_factory=RetrievalHints)
    confidence: str = "medium"
    raw_response: dict | None = None


@dataclass
class SheetIndexEntry:
    """Entry in the workbook sheet index."""
    sheet_name: str
    semantic_title: str
    role: str = "unknown"
    best_for: str = ""


@dataclass
class CrossSheetRelationship:
    """Relationship between sheets in the workbook."""
    description: str
    sheets_involved: list[str] = field(default_factory=list)
    relationship_type: str = "unknown"


@dataclass
class QueryRouting:
    """Query routing recommendations."""
    schema_questions: str = ""
    financial_questions: str = ""
    trend_questions: str = ""
    lookup_questions: str = ""


@dataclass
class WorkbookEnrichment:
    """Complete semantic enrichment for a workbook."""
    workbook_title: str = ""
    workbook_purpose: str = ""
    domain: str = "general"
    sheet_index: list[SheetIndexEntry] = field(default_factory=list)
    cross_sheet_relationships: list[CrossSheetRelationship] = field(default_factory=list)
    global_metrics: list[str] = field(default_factory=list)
    recommended_query_routing: QueryRouting = field(default_factory=QueryRouting)
    context_header_for_qa: str = ""
    sheets: dict[str, SheetEnrichment] = field(default_factory=dict)
    raw_response: dict | None = None


# ---------------------------------------------------------------------------
# SEMANTIC ENRICHER AGENT
# ---------------------------------------------------------------------------

class SemanticEnricher(BaseAgent):
    """Agent that performs deep semantic enrichment of Excel workbooks.

    This agent uses LLM to analyze structural manifests and produce rich
    semantic metadata including:
    - Domain classification
    - Time dimension analysis
    - Key metrics and dimensions
    - Cross-sheet relationships
    - Query routing recommendations
    - Context headers for Q&A
    """

    def __init__(self):
        """Initialize the Semantic Enricher agent."""
        super().__init__(name="SemanticEnricher")
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    async def execute(
        self,
        manifest: WorkbookManifest | dict,
        parallel_sheets: bool = True,
    ) -> AgentResult:
        """Perform semantic enrichment on a workbook manifest.

        Args:
            manifest: WorkbookManifest from VisualMetadataExtractor or dict.
            parallel_sheets: Whether to enrich sheets in parallel.

        Returns:
            AgentResult containing WorkbookEnrichment or error.
        """
        self._log_start({"manifest_type": type(manifest).__name__})
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        try:
            # Convert manifest to dict if needed
            if isinstance(manifest, WorkbookManifest):
                manifest_dict = manifest_to_dict(manifest)
            else:
                manifest_dict = manifest

            # Step 1: Enrich all sheets
            sheet_enrichments = await self._enrich_all_sheets(
                manifest_dict, parallel=parallel_sheets
            )

            # Step 2: Generate workbook-level summary
            workbook_enrichment = await self._enrich_workbook(sheet_enrichments)

            # Attach sheet enrichments
            workbook_enrichment.sheets = {
                s.sheet_name: s for s in sheet_enrichments
            }

            result = AgentResult(
                success=True,
                data=workbook_enrichment,
                metadata={
                    "sheets_enriched": len(sheet_enrichments),
                    "domain": workbook_enrichment.domain,
                    "has_context_header": bool(workbook_enrichment.context_header_for_qa),
                    "total_input_tokens": self._total_input_tokens,
                    "total_output_tokens": self._total_output_tokens,
                },
            )
            self._log_complete(result)
            return result

        except Exception as e:
            self._log_error(e)
            return AgentResult(
                success=False,
                error=f"Failed to enrich workbook: {str(e)}",
            )

    async def _enrich_all_sheets(
        self,
        manifest_dict: dict,
        parallel: bool = True,
    ) -> list[SheetEnrichment]:
        """Enrich all sheets in the workbook."""
        sheets_data = manifest_dict.get("sheets", {})

        if not sheets_data:
            return []

        # Build sheet extracts for enrichment
        sheet_extracts = []
        for sheet_name, sheet_data in sheets_data.items():
            extract = self._build_sheet_extract(sheet_name, sheet_data)
            sheet_extracts.append(extract)

        # Enrich sheets
        if parallel:
            tasks = [self._enrich_sheet(extract) for extract in sheet_extracts]
            enrichments = await asyncio.gather(*tasks, return_exceptions=True)

            # Handle exceptions
            results = []
            for i, enrichment in enumerate(enrichments):
                if isinstance(enrichment, Exception):
                    logger.error(f"Failed to enrich sheet: {enrichment}")
                    results.append(SheetEnrichment(
                        sheet_name=sheet_extracts[i]["sheet_name"],
                        primary_purpose="Enrichment failed",
                    ))
                else:
                    results.append(enrichment)
            return results
        else:
            return [await self._enrich_sheet(extract) for extract in sheet_extracts]

    def _build_sheet_extract(self, sheet_name: str, sheet_data: dict) -> dict:
        """Build the sheet extract format expected by the enrichment prompt."""
        # Get sample data for raw_sample
        sample_data = sheet_data.get("sample_data", [])
        raw_sample = []
        if sample_data:
            # Convert sample data to 2D array
            headers = list(sample_data[0].keys()) if sample_data else []
            raw_sample.append(headers)
            for row in sample_data[:19]:  # Limit to 20 total rows including header
                raw_sample.append([row.get(h) for h in headers])

        # Build detected tables from sections
        detected_tables = []
        for section in sheet_data.get("sections", []):
            table = {
                "header_row": int(section.get("rows", "1-1").split("-")[0]),
                "data_rows": section.get("row_count", 0),
                "columns": [
                    {"name": h, "raw_types": ["text"], "sample_values": []}
                    for h in section.get("headers", [])
                ],
            }
            detected_tables.append(table)

        # If no sections, create table from sample data
        if not detected_tables and sample_data:
            headers = list(sample_data[0].keys()) if sample_data else []
            columns = []
            for h in headers:
                samples = [str(row.get(h, ""))[:50] for row in sample_data[:5] if row.get(h) is not None]
                columns.append({
                    "name": h,
                    "raw_types": self._infer_types(samples),
                    "sample_values": samples[:3],
                })
            detected_tables.append({
                "header_row": sheet_data.get("header_row", 1),
                "data_rows": sheet_data.get("total_rows", 0) - 1,
                "columns": columns,
            })

        # Build merged cells list
        merged_cells = [
            {"cell_range": mr.get("range"), "value": mr.get("value")}
            for mr in sheet_data.get("merged_regions", [])
        ]

        # Build label zones from sections
        label_zones = [
            {
                "cell_range": f"A{section.get('rows', '1-1').split('-')[0]}",
                "text": section.get("name"),
                "style": section.get("color", "default"),
            }
            for section in sheet_data.get("sections", [])
        ]

        return {
            "sheet_name": sheet_name,
            "dimensions": {
                "rows": sheet_data.get("total_rows", 0),
                "cols": sheet_data.get("total_cols", 0),
            },
            "label_zones": label_zones,
            "detected_tables": detected_tables,
            "merged_cells": merged_cells,
            "named_ranges": [],  # Not currently extracted
            "formula_samples": [],  # Could be added later
            "image_descriptions": [],  # Vision pass not implemented yet
            "raw_sample": raw_sample,
        }

    def _infer_types(self, samples: list[str]) -> list[str]:
        """Infer data types from sample values."""
        types = set()
        for s in samples:
            if not s:
                continue
            s = s.strip()
            if s.startswith("$") or s.startswith("£") or s.startswith("€"):
                types.add("currency")
            elif s.endswith("%"):
                types.add("percentage")
            elif s.replace(",", "").replace(".", "").replace("-", "").isdigit():
                types.add("number")
            elif any(d in s for d in ["/", "-"]) and any(c.isdigit() for c in s):
                types.add("date")
            else:
                types.add("text")
        return list(types) if types else ["text"]

    async def _enrich_sheet(self, sheet_extract: dict) -> SheetEnrichment:
        """Enrich a single sheet using LLM."""
        llm = get_llm_client()

        # Build messages
        system_message = SHEET_ENRICHMENT_SYSTEM
        user_message = build_sheet_enrichment_prompt(sheet_extract)

        # Call LLM
        response = await llm.ainvoke(
            [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ]
        )

        # Track tokens if available
        if hasattr(response, "usage_metadata"):
            self._total_input_tokens += response.usage_metadata.get("input_tokens", 0)
            self._total_output_tokens += response.usage_metadata.get("output_tokens", 0)

        # Parse response
        response_text = response.content if hasattr(response, "content") else str(response)
        response_text = self._clean_json_response(response_text)

        try:
            result_dict = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse sheet enrichment JSON: {e}")
            result_dict = self._extract_json_from_text(response_text)

        return self._dict_to_sheet_enrichment(result_dict)

    async def _enrich_workbook(
        self, sheet_enrichments: list[SheetEnrichment]
    ) -> WorkbookEnrichment:
        """Generate workbook-level summary from enriched sheets."""
        if not sheet_enrichments:
            return WorkbookEnrichment()

        llm = get_llm_client()

        # Convert enrichments to dicts for the prompt
        enriched_dicts = [
            {
                "sheet_name": s.sheet_name,
                "semantic_title": s.semantic_title,
                "domain": s.domain,
                "primary_purpose": s.primary_purpose,
                "key_metrics": [
                    {"name": m.name, "unit": m.unit, "description": m.description}
                    for m in s.key_metrics
                ],
                "time_dimension": {
                    "present": s.time_dimension.present,
                    "grain": s.time_dimension.grain,
                    "range_start": s.time_dimension.range_start,
                    "range_end": s.time_dimension.range_end,
                },
                "answerable_question_types": s.answerable_question_types[:4],
            }
            for s in sheet_enrichments
        ]

        # Build messages
        system_message = WORKBOOK_SUMMARY_SYSTEM
        user_message = build_workbook_summary_prompt(enriched_dicts)

        # Call LLM
        response = await llm.ainvoke(
            [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ]
        )

        # Track tokens
        if hasattr(response, "usage_metadata"):
            self._total_input_tokens += response.usage_metadata.get("input_tokens", 0)
            self._total_output_tokens += response.usage_metadata.get("output_tokens", 0)

        # Parse response
        response_text = response.content if hasattr(response, "content") else str(response)
        response_text = self._clean_json_response(response_text)

        try:
            result_dict = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse workbook enrichment JSON: {e}")
            result_dict = self._extract_json_from_text(response_text)

        return self._dict_to_workbook_enrichment(result_dict)

    def _clean_json_response(self, text: str) -> str:
        """Clean JSON response from LLM."""
        text = text.strip()
        # Remove markdown code blocks
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _extract_json_from_text(self, text: str) -> dict:
        """Try to extract JSON object from text."""
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            json_str = text[start:end + 1]
            return json.loads(json_str)
        raise ValueError("Could not extract valid JSON from LLM response")

    def _dict_to_sheet_enrichment(self, data: dict) -> SheetEnrichment:
        """Convert dictionary to SheetEnrichment dataclass."""
        # Parse time dimension
        time_data = data.get("time_dimension", {})
        time_dimension = TimeDimension(
            present=time_data.get("present", False),
            grain=time_data.get("grain"),
            range_start=time_data.get("range_start"),
            range_end=time_data.get("range_end"),
            time_column=time_data.get("time_column"),
        )

        # Parse key metrics
        key_metrics = [
            KeyMetric(
                name=m.get("name", ""),
                column_or_cell=m.get("column_or_cell", ""),
                unit=m.get("unit"),
                description=m.get("description", ""),
                can_aggregate=m.get("can_aggregate", True),
            )
            for m in data.get("key_metrics", [])
        ]

        # Parse dimensions
        dimensions = [
            Dimension(
                name=d.get("name", ""),
                column=d.get("column", ""),
                cardinality=d.get("cardinality", "unknown"),
                sample_values=d.get("sample_values", []),
            )
            for d in data.get("dimensions", [])
        ]

        # Parse detected tables
        detected_tables = []
        for t in data.get("detected_tables", []):
            columns = [
                TableColumn(
                    name=c.get("name", ""),
                    inferred_type=c.get("inferred_type", "text"),
                    unit=c.get("unit"),
                    nullable=c.get("nullable", True),
                    is_calculated=c.get("is_calculated", False),
                    semantic_role=c.get("semantic_role", "unknown"),
                )
                for c in t.get("columns", [])
            ]
            detected_tables.append(DetectedTable(
                table_id=t.get("table_id", ""),
                label=t.get("label"),
                header_row=t.get("header_row"),
                data_start_row=t.get("data_start_row"),
                data_end_row=t.get("data_end_row"),
                row_count_approx=t.get("row_count_approx", 0),
                columns=columns,
                has_subtotals=t.get("has_subtotals", False),
                has_grand_total=t.get("has_grand_total", False),
                notes=t.get("notes"),
            ))

        # Parse section labels
        section_labels = [
            SectionLabel(
                text=s.get("text", ""),
                cell_range=s.get("cell_range", ""),
                likely_meaning=s.get("likely_meaning", ""),
            )
            for s in data.get("section_labels", [])
        ]

        # Parse data quality flags
        quality_flags = [
            DataQualityFlag(
                type=f.get("type", "none"),
                location=f.get("location", ""),
                description=f.get("description", ""),
            )
            for f in data.get("data_quality_flags", [])
        ]

        # Parse retrieval hints
        hints_data = data.get("retrieval_hints", {})
        retrieval_hints = RetrievalHints(
            best_chunk_strategy=hints_data.get("best_chunk_strategy", "by_row"),
            suggested_chunk_size=hints_data.get("suggested_chunk_size", 50),
            key_filter_columns=hints_data.get("key_filter_columns", []),
            always_include_in_context=hints_data.get("always_include_in_context", []),
        )

        return SheetEnrichment(
            sheet_name=data.get("sheet_name", ""),
            semantic_title=data.get("semantic_title", ""),
            domain=data.get("domain", "general"),
            primary_purpose=data.get("primary_purpose", ""),
            time_dimension=time_dimension,
            key_metrics=key_metrics,
            dimensions=dimensions,
            detected_tables=detected_tables,
            section_labels=section_labels,
            answerable_question_types=data.get("answerable_question_types", []),
            data_quality_flags=quality_flags,
            retrieval_hints=retrieval_hints,
            confidence=data.get("confidence", "medium"),
            raw_response=data,
        )

    def _dict_to_workbook_enrichment(self, data: dict) -> WorkbookEnrichment:
        """Convert dictionary to WorkbookEnrichment dataclass."""
        # Parse sheet index
        sheet_index = [
            SheetIndexEntry(
                sheet_name=s.get("sheet_name", ""),
                semantic_title=s.get("semantic_title", ""),
                role=s.get("role", "unknown"),
                best_for=s.get("best_for", ""),
            )
            for s in data.get("sheet_index", [])
        ]

        # Parse cross-sheet relationships
        relationships = [
            CrossSheetRelationship(
                description=r.get("description", ""),
                sheets_involved=r.get("sheets_involved", []),
                relationship_type=r.get("relationship_type", "unknown"),
            )
            for r in data.get("cross_sheet_relationships", [])
        ]

        # Parse query routing
        routing_data = data.get("recommended_query_routing", {})
        query_routing = QueryRouting(
            schema_questions=routing_data.get("schema_questions", ""),
            financial_questions=routing_data.get("financial_questions", ""),
            trend_questions=routing_data.get("trend_questions", ""),
            lookup_questions=routing_data.get("lookup_questions", ""),
        )

        return WorkbookEnrichment(
            workbook_title=data.get("workbook_title", ""),
            workbook_purpose=data.get("workbook_purpose", ""),
            domain=data.get("domain", "general"),
            sheet_index=sheet_index,
            cross_sheet_relationships=relationships,
            global_metrics=data.get("global_metrics", []),
            recommended_query_routing=query_routing,
            context_header_for_qa=data.get("context_header_for_qa", ""),
            raw_response=data,
        )

    def get_token_usage(self) -> tuple[int, int]:
        """Get total token usage from the enrichment process."""
        return self._total_input_tokens, self._total_output_tokens


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def enrichment_to_dict(enrichment: WorkbookEnrichment) -> dict:
    """Convert WorkbookEnrichment to JSON-serializable dictionary."""
    return {
        "workbook_title": enrichment.workbook_title,
        "workbook_purpose": enrichment.workbook_purpose,
        "domain": enrichment.domain,
        "sheet_index": [
            {
                "sheet_name": s.sheet_name,
                "semantic_title": s.semantic_title,
                "role": s.role,
                "best_for": s.best_for,
            }
            for s in enrichment.sheet_index
        ],
        "cross_sheet_relationships": [
            {
                "description": r.description,
                "sheets_involved": r.sheets_involved,
                "relationship_type": r.relationship_type,
            }
            for r in enrichment.cross_sheet_relationships
        ],
        "global_metrics": enrichment.global_metrics,
        "recommended_query_routing": {
            "schema_questions": enrichment.recommended_query_routing.schema_questions,
            "financial_questions": enrichment.recommended_query_routing.financial_questions,
            "trend_questions": enrichment.recommended_query_routing.trend_questions,
            "lookup_questions": enrichment.recommended_query_routing.lookup_questions,
        },
        "context_header_for_qa": enrichment.context_header_for_qa,
        "sheets": {
            name: sheet_enrichment_to_dict(sheet)
            for name, sheet in enrichment.sheets.items()
        },
    }


def sheet_enrichment_to_dict(sheet: SheetEnrichment) -> dict:
    """Convert SheetEnrichment to JSON-serializable dictionary."""
    return {
        "sheet_name": sheet.sheet_name,
        "semantic_title": sheet.semantic_title,
        "domain": sheet.domain,
        "primary_purpose": sheet.primary_purpose,
        "time_dimension": {
            "present": sheet.time_dimension.present,
            "grain": sheet.time_dimension.grain,
            "range_start": sheet.time_dimension.range_start,
            "range_end": sheet.time_dimension.range_end,
            "time_column": sheet.time_dimension.time_column,
        },
        "key_metrics": [
            {
                "name": m.name,
                "column_or_cell": m.column_or_cell,
                "unit": m.unit,
                "description": m.description,
                "can_aggregate": m.can_aggregate,
            }
            for m in sheet.key_metrics
        ],
        "dimensions": [
            {
                "name": d.name,
                "column": d.column,
                "cardinality": d.cardinality,
                "sample_values": d.sample_values,
            }
            for d in sheet.dimensions
        ],
        "detected_tables": [
            {
                "table_id": t.table_id,
                "label": t.label,
                "header_row": t.header_row,
                "data_start_row": t.data_start_row,
                "data_end_row": t.data_end_row,
                "row_count_approx": t.row_count_approx,
                "columns": [
                    {
                        "name": c.name,
                        "inferred_type": c.inferred_type,
                        "unit": c.unit,
                        "nullable": c.nullable,
                        "is_calculated": c.is_calculated,
                        "semantic_role": c.semantic_role,
                    }
                    for c in t.columns
                ],
                "has_subtotals": t.has_subtotals,
                "has_grand_total": t.has_grand_total,
                "notes": t.notes,
            }
            for t in sheet.detected_tables
        ],
        "section_labels": [
            {
                "text": s.text,
                "cell_range": s.cell_range,
                "likely_meaning": s.likely_meaning,
            }
            for s in sheet.section_labels
        ],
        "answerable_question_types": sheet.answerable_question_types,
        "data_quality_flags": [
            {
                "type": f.type,
                "location": f.location,
                "description": f.description,
            }
            for f in sheet.data_quality_flags
        ],
        "retrieval_hints": {
            "best_chunk_strategy": sheet.retrieval_hints.best_chunk_strategy,
            "suggested_chunk_size": sheet.retrieval_hints.suggested_chunk_size,
            "key_filter_columns": sheet.retrieval_hints.key_filter_columns,
            "always_include_in_context": sheet.retrieval_hints.always_include_in_context,
        },
        "confidence": sheet.confidence,
    }
