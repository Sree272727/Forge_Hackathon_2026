"""Multi-agent Excel processing system.

This module provides a multi-agent orchestration framework for processing
complex, human-formatted Excel files with dynamic structures and visual cues.

Architecture:
- Visual Metadata Extractor: Extracts colors, merged cells, and structural info
- Semantic Mapper: Creates JSON schema mapping of workbook structure (legacy)
- Semantic Enricher: Creates rich semantic enrichment with domain, metrics, context header
- Code Executor: LangGraph stateful agent with Python REPL for data operations
- Orchestrator: Coordinates all agents for query processing
"""

from core.agents.base import BaseAgent
from core.agents.extractor import VisualMetadataExtractor
from core.agents.mapper import SemanticMapper
from core.agents.semantic_enricher import SemanticEnricher
from core.agents.executor import CodeExecutorAgent
from core.agents.orchestrator import ExcelAgentOrchestrator

__all__ = [
    "BaseAgent",
    "VisualMetadataExtractor",
    "SemanticMapper",
    "SemanticEnricher",
    "CodeExecutorAgent",
    "ExcelAgentOrchestrator",
]
