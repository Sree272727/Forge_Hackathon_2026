"""Conversation state for multi-turn chat sessions.

Introduced in v1.5. Replaces the simpler ChatSession dataclass.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str
    turn_id: int
    timestamp: float = field(default_factory=time.time)


@dataclass
class ToolCall:
    turn_id: int
    tool_name: str
    input: dict
    output: dict
    latency_ms: int = 0
    error: Optional[str] = None


@dataclass
class CachedAnswer:
    question_hash: str
    answer_text: str
    evidence_refs: list[str]
    trace: Optional[dict]
    confidence: float
    audit_status: str
    cached_at: float = field(default_factory=time.time)


@dataclass
class ConversationState:
    session_id: str
    workbook_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    active_entity: Optional[str] = None  # last referenced cell ref or metric
    scenario_overrides: dict[str, Any] = field(default_factory=dict)
    answer_cache: dict[str, CachedAnswer] = field(default_factory=dict)
    tool_call_log: list[ToolCall] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def current_turn_id(self) -> int:
        return len([m for m in self.messages if m.role == "user"])

    def append_user(self, content: str) -> int:
        turn_id = self.current_turn_id() + 1
        self.messages.append(ChatMessage(role="user", content=content, turn_id=turn_id))
        self.updated_at = time.time()
        return turn_id

    def append_assistant(self, content: str) -> None:
        turn_id = self.current_turn_id()
        self.messages.append(ChatMessage(role="assistant", content=content, turn_id=turn_id))
        self.updated_at = time.time()

    def log_tool_call(self, tool_name: str, input_args: dict, output: dict,
                       latency_ms: int = 0, error: Optional[str] = None) -> None:
        self.tool_call_log.append(
            ToolCall(
                turn_id=self.current_turn_id(),
                tool_name=tool_name,
                input=input_args,
                output=output,
                latency_ms=latency_ms,
                error=error,
            )
        )

    def set_scenario(self, overrides: dict[str, Any]) -> None:
        """Replace current scenarios (does NOT append)."""
        self.scenario_overrides = dict(overrides)
        self.updated_at = time.time()

    def clear_scenario(self, ref: Optional[str] = None) -> None:
        if ref is None:
            self.scenario_overrides = {}
        else:
            self.scenario_overrides.pop(ref, None)
        self.updated_at = time.time()


# --- Helpers ---

def question_hash(question: str, scenario_overrides: dict[str, Any]) -> str:
    """Stable hash for cache keys. Case-insensitive question normalization."""
    normalized = re.sub(r"\s+", " ", question.lower().strip())
    sig = f"{normalized}::{json.dumps(scenario_overrides, sort_keys=True, default=str)}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


# --- Entity extraction (simple heuristics) ---

CELL_REF_PATTERN = re.compile(r"([A-Za-z_][\w &\-\.]*?)!(\$?[A-Z]{1,3}\$?[0-9]+)")


def extract_entity_from_text(text: str) -> Optional[str]:
    """Pull the first canonical cell ref from a string, if any."""
    m = CELL_REF_PATTERN.search(text)
    if m:
        sheet = m.group(1).strip()
        coord = m.group(2).replace("$", "")
        return f"{sheet}!{coord}"
    return None
