"""Hybrid chat engine.

Strategy:
  1. Try the existing deterministic `qa.answer()` first.
  2. If it returns a confident, non-fallback answer AND the user hasn't asked
     a follow-up that benefits from memory, return it (optionally LLM-polished).
  3. Otherwise, escalate to a Claude tool-calling loop with full session memory.

Session memory: kept in chat_store.ChatSession. We include the full prior
message history in the tool-calling loop so follow-ups like "what about April?"
or "show the trace" work.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from . import qa as qa_module
from .models import QAResponse, WorkbookModel
from .store import ChatSession
from .tools import TOOLS, execute_tool

log = logging.getLogger("rosetta.chat")


SYSTEM_PROMPT = """You are Rosetta, an Excel intelligence agent that answers questions about a specific parsed workbook.

CORE RULES — never violate:
1. Ground every claim in tool results. Never invent cell refs, formulas, named ranges, or values.
2. Always cite cell references in canonical form like `Sheet!G32` when referring to specific cells.
3. When a user asks "how is X calculated?", call `find_cells` to locate X, then `backward_trace` on the best match.
4. When a user asks a "what if" question, use the `what_if` tool rather than guessing.
5. When a user asks about issues / stale / anomalies / circular / hidden, use `list_findings`.
6. Resolve named ranges to their semantic names in your answer (e.g. say "FloorPlanRate (5.8%)" not just "Assumptions!B2").
7. Keep answers concise. Lead with the answer, then a short trace. Use plain business language.
8. If a tool returns an error or the data isn't there, say so — don't guess.

Use `list_sheets` and `list_named_ranges` when you need to orient yourself on a new question.
"""


def chat(wb: WorkbookModel, session: ChatSession, user_msg: str) -> dict[str, Any]:
    """Answer a user message. Returns {answer, trace, evidence, escalated, confidence}."""
    # Always record the turn
    session.messages.append({"role": "user", "content": user_msg})

    # First, try the deterministic path
    resp = qa_module.answer(wb, user_msg)
    fallback_phrases = (
        "couldn't pin",
        "could not parse",
        "couldn't find",
        "no matching",
        "i need a key",
        "no rows found",
    )
    looks_like_fallback = any(p in resp.answer.lower() for p in fallback_phrases)
    deterministic_ok = resp.confidence >= 0.7 and not looks_like_fallback

    # Follow-up detection: if we have prior turns, prefer tool-calling for richer context
    is_followup = len([m for m in session.messages if m["role"] == "assistant"]) > 0 and _looks_like_followup(user_msg)

    use_llm = os.environ.get("ANTHROPIC_API_KEY") and (not deterministic_ok or is_followup)

    if use_llm:
        log.info("Escalating to tool-calling: det_ok=%s followup=%s", deterministic_ok, is_followup)
        try:
            answer_text, trace = _tool_calling_loop(wb, session)
            session.messages.append({"role": "assistant", "content": answer_text})
            return {
                "session_id": session.session_id,
                "answer": answer_text,
                "trace": trace,
                "evidence": [],
                "escalated": True,
                "confidence": 0.85,
            }
        except Exception as e:
            log.warning("Tool-calling loop failed, falling back to deterministic: %s", e)

    # Deterministic path wins
    session.messages.append({"role": "assistant", "content": resp.answer})
    return {
        "session_id": session.session_id,
        "answer": resp.answer,
        "trace": resp.trace.model_dump() if resp.trace else None,
        "evidence": [e.model_dump() for e in resp.evidence],
        "escalated": False,
        "confidence": resp.confidence,
    }


def _looks_like_followup(msg: str) -> bool:
    ml = msg.lower().strip()
    followup_signals = (
        ml.startswith("and "),
        ml.startswith("what about"),
        ml.startswith("how about"),
        ml.startswith("why"),
        ml.startswith("show me"),
        ml.startswith("can you"),
        ml.startswith("also"),
        " it " in ml,
        " that " in ml and "?" in ml,
        len(ml.split()) <= 5,
    )
    return any(followup_signals)


def _tool_calling_loop(wb: WorkbookModel, session: ChatSession, max_turns: int = 8) -> tuple[str, dict | None]:
    """Run Claude with tool-calling until it produces a final text answer.

    Returns (answer_text, optional_trace_dict).
    """
    import anthropic  # type: ignore

    client = anthropic.Anthropic()
    # Build Claude-format messages from session history
    # Session stores strings; Claude needs list-of-content-blocks for assistant turns with tool_use.
    # For simplicity we include only text turns from prior history, then let this turn be fresh.
    claude_messages: list[dict] = []
    for m in session.messages[:-1]:  # exclude the user message we just appended (we'll add it last)
        if isinstance(m["content"], str):
            claude_messages.append({"role": m["role"], "content": m["content"]})
    # Add the current user message
    claude_messages.append({"role": "user", "content": session.messages[-1]["content"]})

    trace_result: dict | None = None

    for _ in range(max_turns):
        resp = client.messages.create(
            model=os.environ.get("ROSETTA_MODEL", "claude-sonnet-4-5"),
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=claude_messages,
        )
        if resp.stop_reason == "tool_use":
            # Append assistant turn as-is (content blocks)
            claude_messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    result = execute_tool(wb, block.name, block.input)
                    # Capture the first backward_trace as the surfaced trace
                    if block.name == "backward_trace" and isinstance(result, dict) and "trace" in result and trace_result is None:
                        trace_result = result["trace"]
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str)[:20000],
                    })
            claude_messages.append({"role": "user", "content": tool_results})
            continue
        # end_turn or max_tokens
        text_parts = [b.text for b in resp.content if b.type == "text"]
        return ("\n".join(text_parts).strip() or "(no answer)", trace_result)

    return ("I reached the tool-calling limit before finishing the answer. Try a more specific question.", trace_result)
