"""
Excel Semantic Enrichment Prompts
==================================
FastAPI/Python backend module for the semantic enrichment pass of the
Excel parsing pipeline. Covers all four file types: financial reports,
sales/CRM, operations/inventory, and general-purpose Excel files.

Usage:
    from excel_enrichment_prompts import enrich_sheet, enrich_workbook
    result = await enrich_sheet(sheet_extract)
    summary = await enrich_workbook(all_sheets_result)
"""

import json
import re
from typing import Any
import anthropic

client = anthropic.AsyncAnthropic()
MODEL = "claude-sonnet-4-20250514"


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


def build_sheet_enrichment_prompt(sheet_extract: dict) -> str:
    """
    Build the user-turn prompt for a single sheet enrichment pass.

    sheet_extract should contain:
        - sheet_name: str
        - dimensions: {rows: int, cols: int}
        - label_zones: list of {cell_range, text, style}
        - detected_tables: list of {header_row, data_rows, columns: [{name, raw_types, sample_values}]}
        - merged_cells: list of {cell_range, value}
        - named_ranges: list of {name, refers_to}
        - formula_samples: list of {cell, formula}
        - image_descriptions: list of str  (from vision pass, may be empty)
        - raw_sample: 2D list of up to 20 rows x 20 cols for context
    """
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
  "image_summaries": [
    {{
      "description": "<what the image/chart shows>",
      "data_source": "<likely data range or table it visualises>",
      "insight": "<key takeaway from this visual if discernible>"
    }}
  ],
  "confidence": "<high | medium | low — how confident are you in this enrichment given the extract quality>"
}}"""


# ---------------------------------------------------------------------------
# WORKBOOK-LEVEL SUMMARY PROMPT
# ---------------------------------------------------------------------------

WORKBOOK_SUMMARY_SYSTEM = """You are a senior data analyst. You have been given enriched
descriptions of every sheet in an Excel workbook. Your job is to produce a workbook-level
summary that a question-answering system will inject at the top of every query prompt.

Output MUST be valid JSON only. No preamble, no markdown fences."""


def build_workbook_summary_prompt(enriched_sheets: list[dict]) -> str:
    """
    Build the prompt for the workbook-level summary pass.
    Takes the list of per-sheet enrichment results.
    """
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
# IMAGE / CHART VISION PROMPT
# ---------------------------------------------------------------------------

IMAGE_DESCRIPTION_SYSTEM = """You are analysing an image extracted from an Excel workbook.
Describe only what you can see. Output valid JSON only, no preamble."""


IMAGE_DESCRIPTION_PROMPT = """This image was extracted from an Excel file. It may be a
chart, graph, logo, diagram, or decorative element.

Analyse it and return:

{{
  "image_type": "<bar_chart | line_chart | pie_chart | scatter | table_screenshot | logo | photo | diagram | unknown>",
  "title": "<chart or image title if visible, else null>",
  "axes": {{
    "x_label": "<x-axis label or null>",
    "y_label": "<y-axis label or null>",
    "x_range": "<e.g. Jan 2024 – Dec 2024 or null>",
    "y_range": "<e.g. 0 to 500,000 or null>"
  }},
  "series": ["<series names visible in legend, e.g. Revenue, COGS>"],
  "key_insight": "<the single most important thing this visual communicates, in one sentence>",
  "data_range_hint": "<if you can infer the source data range, e.g. 'monthly data for 12 periods'>",
  "useful_for_qa": true | false
}}"""


# ---------------------------------------------------------------------------
# CORE ENRICHMENT FUNCTIONS
# ---------------------------------------------------------------------------

async def enrich_sheet(sheet_extract: dict) -> dict:
    """
    Run the semantic enrichment pass on a single sheet extract.

    Args:
        sheet_extract: Raw structural data from openpyxl parsing pass.

    Returns:
        Enriched dict with semantic metadata, or error dict on failure.
    """
    response = await client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=SHEET_ENRICHMENT_SYSTEM,
        messages=[
            {"role": "user", "content": build_sheet_enrichment_prompt(sheet_extract)}
        ],
    )

    raw = response.content[0].text.strip()
    # Strip accidental markdown fences if model adds them
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {
            "error": "json_parse_failed",
            "detail": str(e),
            "raw_response": raw,
            "sheet_name": sheet_extract.get("sheet_name", "unknown"),
        }


async def enrich_workbook(enriched_sheets: list[dict]) -> dict:
    """
    Run the workbook-level summary pass after all sheets have been enriched.

    Args:
        enriched_sheets: List of results from enrich_sheet().

    Returns:
        Workbook-level summary dict.
    """
    # Filter out any error sheets before summarising
    valid_sheets = [s for s in enriched_sheets if "error" not in s]

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=WORKBOOK_SUMMARY_SYSTEM,
        messages=[
            {"role": "user", "content": build_workbook_summary_prompt(valid_sheets)}
        ],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": "json_parse_failed", "detail": str(e), "raw_response": raw}


async def describe_image(image_bytes: bytes, media_type: str = "image/png") -> dict:
    """
    Run the vision pass on an extracted chart or image.

    Args:
        image_bytes: Raw image bytes from openpyxl image extraction.
        media_type: MIME type, e.g. 'image/png', 'image/jpeg'.

    Returns:
        Image description dict.
    """
    import base64
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=IMAGE_DESCRIPTION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": IMAGE_DESCRIPTION_PROMPT},
                ],
            }
        ],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "parse_failed", "raw": raw}


# ---------------------------------------------------------------------------
# FULL PIPELINE ORCHESTRATOR
# ---------------------------------------------------------------------------

async def run_enrichment_pipeline(
    all_sheet_extracts: list[dict],
    images: list[dict[str, Any]] | None = None,
) -> dict:
    """
    Orchestrate the full enrichment pipeline for a workbook.

    Args:
        all_sheet_extracts: List of raw structural extracts, one per sheet.
        images: Optional list of {"sheet": str, "bytes": bytes, "media_type": str}

    Returns:
        {
            "sheets": [enriched sheet dicts],
            "workbook": workbook summary dict,
            "images": [image description dicts]
        }
    """
    import asyncio

    # Step 1: Enrich all sheets in parallel
    sheet_tasks = [enrich_sheet(extract) for extract in all_sheet_extracts]
    enriched_sheets = await asyncio.gather(*sheet_tasks)

    # Step 2: Describe images in parallel (if any)
    image_results = []
    if images:
        image_tasks = [
            describe_image(img["bytes"], img.get("media_type", "image/png"))
            for img in images
        ]
        image_descriptions = await asyncio.gather(*image_tasks)
        image_results = [
            {"sheet": images[i]["sheet"], **desc}
            for i, desc in enumerate(image_descriptions)
        ]

    # Step 3: Workbook-level summary (sequential, depends on step 1)
    workbook_summary = await enrich_workbook(list(enriched_sheets))

    return {
        "sheets": list(enriched_sheets),
        "workbook": workbook_summary,
        "images": image_results,
    }


# ---------------------------------------------------------------------------
# FASTAPI INTEGRATION EXAMPLE
# ---------------------------------------------------------------------------
#
# from fastapi import FastAPI, UploadFile
# from excel_parser import extract_sheets          # your openpyxl parsing module
# from excel_enrichment_prompts import run_enrichment_pipeline
#
# app = FastAPI()
#
# @app.post("/upload")
# async def upload_excel(file: UploadFile):
#     contents = await file.read()
#     sheet_extracts, images = extract_sheets(contents)   # your structural pass
#     enrichment = await run_enrichment_pipeline(sheet_extracts, images)
#     # → save enrichment["sheets"] to metadata store
#     # → chunk + embed for vector store
#     # → store enrichment["workbook"]["context_header_for_qa"] for runtime injection
#     return {"status": "ok", "workbook_title": enrichment["workbook"]["workbook_title"]}