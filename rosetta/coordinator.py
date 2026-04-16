"""Coordinator agent — the heart of v1.5.

Strategy:
  1. Receive the user message.
  2. Check answer cache.
  3. Run Claude in a tool-calling loop over deterministic tools.
  4. When a tool result is a backward_trace, optionally delegate to
     FormulaExplainer for grounded prose.
  5. When the LLM ends its turn, pass the answer through the citation
     auditor. Failure → retry once with violation feedback. Retry failure
     → return "I don't know" partial answer.
  6. Update conversation state, cache the result.

Spec: docs/plan_v1_5.md §7.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from . import auditor as auditor_module
from .conversation import (
    CachedAnswer, ConversationState, ToolCall,
    extract_entity_from_text, question_hash,
)
from .models import WorkbookModel
from .specialists import formula_explainer
from .tools import TOOLS, execute_tool

log = logging.getLogger("rosetta.coordinator")


COORDINATOR_SYSTEM_PROMPT = """You are Rosetta's coordinator. You answer questions about a specific parsed
Excel workbook by calling deterministic tools and, when needed, delegating
to the FormulaExplainer specialist.

CORE RULES — never violate:
1. Every number, percentage, currency value, and cell reference in your
   answer must come from a tool result in this conversation. Never invent
   a value or cell ref.
2. If simple arithmetic is needed (e.g. subtracting two fetched numbers),
   do it yourself but only with values you have just fetched. Cite the
   source refs.
3. Resolve named ranges by NAME AND VALUE (e.g. "FloorPlanRate (5.8%)",
   not "Assumptions!B2").
4. When a question is ambiguous (multiple candidate cells), list the
   candidates and ask — never silently pick.
5. When you cannot ground an answer in tool results, say "I don't know"
   and explain what specifically you couldn't verify. "I don't know" is
   better than a guess.
6. Cite cell refs in canonical form: `Sheet!Ref` (e.g. `P&L Summary!G32`).
7. Do not fabricate cell refs. If a tool returns no results, do not
   continue as if it had — broaden the search or ask for clarification.

PLANNING GUIDANCE:
- Unfamiliar workbook? Start with get_workbook_summary to orient yourself.
- "How is X calculated?" → find_cells(X) → backward_trace(that cell) →
  delegate_to_formula_explainer(trace).
- "What depends on X?" / "What would change if X changes?" →
  resolve_named_range or find_cells → forward_impact.
- "What if X changes?" / scenario questions → scenario_recalc with
  the overrides dict.
- "Stale / issues / hidden / circular / anomaly" → list_findings.
- Comparison questions ("how does A differ from B?") → find_cells for
  both → backward_trace each → write a diff explanation directly.
- Follow-ups referring to "it" / "that" / "what about April" → the
  active entity from prior turns is provided in the context below.

DELEGATION:
- You have a virtual tool "delegate_to_formula_explainer" that is NOT in
  the tools list — instead, indicate delegation by calling the
  backward_trace tool and then in your final answer text, include a line
  `<<DELEGATE_FORMULA_EXPLAINER ref=Sheet!Ref>>` on its own. The host
  will run the specialist and splice in the grounded prose.
  Only do this for formula-explanation questions.

OUTPUT:
- Your final answer is free-form prose but MUST adhere to rules 1–7.
- Cite evidence inline, e.g. "(P&L Summary!G32: $142,300)".
- Keep answers concise. Lead with the answer, then supporting trace.
"""


MAX_TOOL_TURNS = 10
MAX_AUDIT_RETRIES = 1  # total attempts = 1 + retries


def answer(wb: WorkbookModel, state: ConversationState, message: str) -> dict:
    """Produce a grounded answer. See docs/plan_v1_5.md §7 for return shape."""
    state.append_user(message)

    # --- Cache lookup ---
    qh = question_hash(message, state.scenario_overrides)
    cached = state.answer_cache.get(qh)
    if cached and _cached_is_fresh(cached):
        log.info("Cache hit for question_hash=%s", qh)
        state.append_assistant(cached.answer_text)
        return {
            "session_id": state.session_id,
            "answer": cached.answer_text,
            "trace": cached.trace,
            "evidence": [{"ref": r} for r in cached.evidence_refs],
            "escalated": False,
            "audit_status": cached.audit_status,
            "confidence": cached.confidence,
            "tool_calls_made": 0,
            "active_entity": state.active_entity,
            "scenario_overrides": state.scenario_overrides,
            "cached": True,
        }

    # --- LLM tool loop ---
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _no_api_key_response(state, message)

    try:
        import anthropic  # type: ignore
    except ImportError:
        return _no_api_key_response(state, message, reason="anthropic SDK not installed")

    client = anthropic.Anthropic()
    model = os.environ.get("ROSETTA_MODEL", "claude-sonnet-4-5")

    claude_messages = _build_claude_messages(state)

    tool_calls_made = 0
    trace_for_ui: Optional[dict] = None
    violation_retries_used = 0
    final_text = ""
    final_response = None

    while True:
        attempt_text, attempt_tool_calls, attempt_trace = _run_tool_loop(
            client, model, claude_messages, wb, state
        )
        tool_calls_made += attempt_tool_calls
        if attempt_trace and not trace_for_ui:
            trace_for_ui = attempt_trace

        # Splice in FormulaExplainer if the coordinator requested delegation
        attempt_text, explainer_invoked = _maybe_delegate_to_explainer(
            attempt_text, wb, message
        )
        if explainer_invoked and not trace_for_ui:
            # Pull the trace from recent tool calls
            trace_for_ui = _latest_trace_from_log(state)

        # Audit
        result = auditor_module.audit(attempt_text, state.tool_call_log, wb)

        if result.status == "passed":
            final_text = attempt_text
            audit_status = "passed"
            confidence = 0.9
            break

        if violation_retries_used >= MAX_AUDIT_RETRIES:
            # Second-chance: emit partial "I don't know" wrapper
            final_text = _build_partial_answer(attempt_text, result)
            audit_status = "unknown"
            confidence = 0.3
            break

        # Retry: inject violation feedback
        log.info("Audit failed on attempt; retrying with violation list.")
        violation_retries_used += 1
        claude_messages.append({"role": "assistant", "content": attempt_text})
        claude_messages.append({
            "role": "user",
            "content": auditor_module.format_violations_for_retry(result.violations),
        })
        continue

    # --- Post-process ---
    # Extract evidence refs from tool log
    evidence_refs = _extract_evidence_refs(state.tool_call_log[-tool_calls_made:])
    # Update active_entity
    extracted = extract_entity_from_text(final_text)
    if extracted:
        state.active_entity = extracted

    # Cache successful answers only
    if audit_status == "passed":
        state.answer_cache[qh] = CachedAnswer(
            question_hash=qh, answer_text=final_text,
            evidence_refs=list(evidence_refs),
            trace=trace_for_ui, confidence=confidence,
            audit_status=audit_status,
        )

    state.append_assistant(final_text)

    return {
        "session_id": state.session_id,
        "answer": final_text,
        "trace": trace_for_ui,
        "evidence": [{"ref": r} for r in evidence_refs],
        "escalated": bool(trace_for_ui),
        "audit_status": audit_status,
        "confidence": confidence,
        "tool_calls_made": tool_calls_made,
        "active_entity": state.active_entity,
        "scenario_overrides": state.scenario_overrides,
    }


# --- Helpers ---

def _cached_is_fresh(cached: CachedAnswer) -> bool:
    ttl = int(os.environ.get("ROSETTA_CACHE_TTL_SECS", "3600"))
    return (time.time() - cached.cached_at) < ttl


def _no_api_key_response(state: ConversationState, message: str,
                          reason: str = "ANTHROPIC_API_KEY not set") -> dict:
    msg = (f"I can't answer this: {reason}. "
           f"The coordinator requires Claude for planning. "
           f"Set ANTHROPIC_API_KEY and retry.")
    state.append_assistant(msg)
    return {
        "session_id": state.session_id, "answer": msg, "trace": None,
        "evidence": [], "escalated": False, "audit_status": "unknown",
        "confidence": 0.0, "tool_calls_made": 0,
        "active_entity": state.active_entity,
        "scenario_overrides": state.scenario_overrides,
    }


def _build_claude_messages(state: ConversationState) -> list[dict]:
    """Build Claude-format messages from session history, plus a system-injected
    context line with active_entity + scenario overrides."""
    context_lines = []
    if state.active_entity:
        context_lines.append(f"Active entity (from prior turn): {state.active_entity}")
    if state.scenario_overrides:
        overrides_str = ", ".join(f"{k}={v}" for k, v in state.scenario_overrides.items())
        context_lines.append(f"Active scenario overrides: {overrides_str}")
    context_block = "\n".join(context_lines)

    messages: list[dict] = []
    # Prior turns (excluding the current user turn we just appended)
    for m in state.messages[:-1]:
        messages.append({"role": m.role, "content": m.content})
    # Current user turn with context prepended
    current = state.messages[-1].content
    if context_block:
        current = f"[Context: {context_block}]\n\n{current}"
    messages.append({"role": "user", "content": current})
    return messages


def _run_tool_loop(client, model: str, claude_messages: list[dict],
                    wb: WorkbookModel, state: ConversationState) -> tuple[str, int, Optional[dict]]:
    """Run Claude tool-calling until end_turn. Returns (text, num_tool_calls, trace_seen)."""
    tool_calls_made = 0
    trace_seen: Optional[dict] = None

    for _ in range(MAX_TOOL_TURNS):
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0,
            system=COORDINATOR_SYSTEM_PROMPT,
            tools=TOOLS,
            messages=claude_messages,
        )
        if resp.stop_reason == "tool_use":
            # Append the assistant's content blocks verbatim
            claude_messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in resp.content]
            })
            tool_results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                t0 = time.time()
                out = execute_tool(wb, block.name, block.input)
                elapsed_ms = int((time.time() - t0) * 1000)
                state.log_tool_call(block.name, block.input, out, elapsed_ms)
                tool_calls_made += 1
                if block.name == "backward_trace" and isinstance(out, dict) and "trace" in out:
                    if trace_seen is None:
                        trace_seen = out["trace"]
                # Serialize tool output for Claude
                serialized = json.dumps(out, default=str)[:12000]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": serialized,
                })
            claude_messages.append({"role": "user", "content": tool_results})
            continue
        # end_turn / max_tokens / stop_sequence
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text, tool_calls_made, trace_seen

    return "(Coordinator hit max tool turns without producing an answer.)", tool_calls_made, trace_seen


def _maybe_delegate_to_explainer(text: str, wb: WorkbookModel,
                                   original_question: str) -> tuple[str, bool]:
    """If the answer contains a delegation marker, run the specialist and splice in the prose."""
    import re
    marker_re = re.compile(r"<<DELEGATE_FORMULA_EXPLAINER\s+ref=([^>]+)>>")
    m = marker_re.search(text)
    if not m:
        return text, False
    ref = m.group(1).strip()
    from .graph import backward_trace
    if ref not in wb.cells:
        replacement = f"(Could not delegate: cell {ref} not found.)"
        return marker_re.sub(replacement, text), True
    trace = backward_trace(wb, ref, max_depth=3).model_dump()
    result = formula_explainer.explain(trace, original_question)
    return marker_re.sub(result["prose"], text), True


def _latest_trace_from_log(state: ConversationState) -> Optional[dict]:
    for tc in reversed(state.tool_call_log):
        if tc.tool_name == "backward_trace" and isinstance(tc.output, dict) and "trace" in tc.output:
            return tc.output["trace"]
    return None


def _extract_evidence_refs(recent_tool_calls: list[ToolCall]) -> list[str]:
    """Pull cell refs touched by recent tool calls."""
    refs: list[str] = []
    seen: set[str] = set()

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "ref" and isinstance(v, str) and "!" in v and v not in seen:
                    seen.add(v)
                    refs.append(v)
                _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                _walk(x)

    for tc in recent_tool_calls:
        _walk(tc.output)
    return refs[:30]


def _build_partial_answer(attempt_text: str,
                           audit_result: auditor_module.AuditResult) -> str:
    """Build the 'I don't know' partial answer after second audit failure."""
    parts = [
        "I can only partially answer this. Here's what I verified:",
    ]
    if audit_result.verified_numbers:
        parts.append(f"  • Verified numbers: {', '.join(audit_result.verified_numbers[:8])}")
    if audit_result.verified_refs:
        parts.append(f"  • Verified cell refs: {', '.join(audit_result.verified_refs[:8])}")
    if audit_result.verified_named_ranges:
        parts.append(f"  • Verified named ranges: {', '.join(audit_result.verified_named_ranges[:8])}")

    parts.append("")
    parts.append("What I couldn't verify:")
    for v in audit_result.violations[:8]:
        parts.append(f"  • {v}")

    parts.append("")
    parts.append("You might rephrase the question to be more specific about the cell or metric.")
    return "\n".join(parts)
