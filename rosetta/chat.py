"""DEPRECATED (v1.5) — thin compatibility shim.

The v1 hybrid regex+LLM chat engine has been replaced by rosetta/coordinator.py.
This module re-exports the coordinator's `answer` function as `chat` so older
API endpoints and tests continue to work during the transition.

Will be removed in v2.
"""
from __future__ import annotations

from .coordinator import answer

# Backward-compat name used by older imports
chat = answer

__all__ = ["chat", "answer"]
