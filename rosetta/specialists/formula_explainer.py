"""FormulaExplainer specialist.

Converts a backward-trace JSON tree into grounded prose. A single LLM call
writes the full explanation; the recursion is handled by walking the tree
in the prompt context, not by spawning child agents.

Spec: docs/plan_v1_5.md §8.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("rosetta.formula_explainer")


SYSTEM_PROMPT = """You are Rosetta's FormulaExplainer. You receive a structured backward
trace of an Excel cell and produce a grounded prose explanation in the
style of a senior financial analyst walking a colleague through the
calculation.

STYLE CONTRACT — never violate:
1. Cite EVERY number with its cell ref in parentheses.
   Example: "(P&L Summary!G18: $487,500)".
2. Resolve every named range by name AND value.
   Example: "FloorPlanRate (5.8%)", not "Assumptions!B2".
3. Lead with WHAT the cell IS: its business label, its ref, and its value.
4. Describe the formula in plain business language. Do not regurgitate
   Excel syntax. Explain what each term MEANS in the domain.
5. Walk the dependency tree ONE level deep by default. Go deeper only if
   the explanation requires it (typically for sums of sums, or when a
   dependency is itself a cross-sheet pull).
6. Never round unless the original value is already rounded.
7. NEVER introduce a number, cell ref, or named range that is not in
   the provided trace JSON. If you want to refer to something, find it
   in the tree first.
8. Surface warnings from the trace: hardcoded, volatile, stale assumptions.

OUTPUT: Prose only. Typically 1–3 short paragraphs. Use a short bulleted
list ONLY if the trace has 3+ parallel components at the same level.
Do not include headers or markdown styling.
"""


def _trim_trace(trace: dict, max_depth: int = 3, max_children: int = 12) -> dict:
    """Trim a backward trace for LLM consumption.

    Keep the top-level formula fully visible. Trim deeply nested children
    beyond max_depth or when a node has too many children.
    """
    def _walk(node: dict, depth: int) -> dict:
        if not node:
            return node
        out = {k: v for k, v in node.items() if k != "children"}
        children = node.get("children", [])
        if depth >= max_depth:
            out["children"] = []
            if children:
                out["_truncated_children"] = len(children)
            return out
        if len(children) > max_children:
            out["children"] = [_walk(c, depth + 1) for c in children[:max_children]]
            out["_truncated_children"] = len(children) - max_children
        else:
            out["children"] = [_walk(c, depth + 1) for c in children]
        return out
    return _walk(trace, 0)


def _format_trace_for_prompt(trace: dict) -> str:
    """Render a trace as nested indented text for the LLM."""
    lines: list[str] = []

    def _render(node: dict, indent: int):
        pad = "  " * indent
        ref = node.get("ref", "?")
        label = node.get("label", "")
        value = node.get("value")
        formula = node.get("formula")
        nr = node.get("named_range")
        hc = node.get("is_hardcoded", False)
        vol = node.get("is_volatile", False)
        warnings = node.get("warnings", [])

        lead_parts = [f"{pad}- {ref}"]
        if label:
            lead_parts.append(f"({label})")
        if value is not None:
            lead_parts.append(f"= {value!r}")
        if nr:
            lead_parts.append(f"[named range: {nr}]")
        if hc:
            lead_parts.append("[hardcoded]")
        if vol:
            lead_parts.append("[volatile]")
        lines.append(" ".join(lead_parts))

        if formula:
            lines.append(f"{pad}    formula: ={formula}")
        for w in warnings:
            lines.append(f"{pad}    ⚠ {w}")

        for c in node.get("children", []):
            _render(c, indent + 1)

        tc = node.get("_truncated_children")
        if tc:
            lines.append(f"{pad}    … ({tc} more children truncated)")

    _render(trace, 0)
    return "\n".join(lines)


def explain(trace: dict, original_question: str, model: str | None = None) -> dict:
    """Convert a backward trace to grounded prose.

    Returns {"prose": str, "warnings": list[str]}.
    If ANTHROPIC_API_KEY is missing, returns a deterministic fallback
    rendering (less polished but grounded).
    """
    warnings: list[str] = []
    trimmed = _trim_trace(trace)
    formatted = _format_trace_for_prompt(trimmed)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        warnings.append("ANTHROPIC_API_KEY not set — returning deterministic fallback.")
        return {"prose": _deterministic_fallback(trimmed), "warnings": warnings}

    try:
        import anthropic  # type: ignore
    except ImportError:
        warnings.append("anthropic SDK not installed — returning deterministic fallback.")
        return {"prose": _deterministic_fallback(trimmed), "warnings": warnings}

    client = anthropic.Anthropic()
    model = model or os.environ.get("ROSETTA_MODEL", "claude-sonnet-4-5")

    user_prompt = f"""The user asked:
"{original_question}"

Below is the backward trace of the target cell. Write the grounded prose
explanation per the STYLE CONTRACT in your system prompt. Do not add numbers,
refs, or named ranges outside this trace.

TRACE:
{formatted}

RAW TRACE JSON (for reference only — do not quote IDs from this):
{json.dumps(trimmed, default=str, indent=2)[:6000]}
"""

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=1200,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if not text:
            warnings.append("LLM returned empty response — fallback used.")
            return {"prose": _deterministic_fallback(trimmed), "warnings": warnings}
        return {"prose": text, "warnings": warnings}
    except Exception as e:
        log.warning("FormulaExplainer LLM call failed: %s", e)
        warnings.append(f"LLM call failed: {type(e).__name__}: {e}")
        return {"prose": _deterministic_fallback(trimmed), "warnings": warnings}


def _deterministic_fallback(trace: dict) -> str:
    """When the LLM is unavailable, produce a plain-English trace walk.

    Still fully grounded — no invented numbers.
    """
    ref = trace.get("ref", "?")
    label = trace.get("label") or "This cell"
    value = trace.get("value")
    formula = trace.get("formula")
    children = trace.get("children", [])

    parts = [f"{label} is in {ref} and equals {_fmt_value(value)}."]
    if formula:
        parts.append(f"Formula: ={formula}.")

    if children:
        comp_descriptions = []
        for c in children:
            c_ref = c.get("ref", "?")
            c_label = c.get("label") or c_ref
            c_val = c.get("value")
            c_nr = c.get("named_range")
            piece = f"{c_label} ({c_ref}: {_fmt_value(c_val)})"
            if c_nr:
                piece += f" [named range {c_nr}]"
            if c.get("is_hardcoded"):
                piece += " [hardcoded]"
            if c.get("is_volatile"):
                piece += " [volatile]"
            comp_descriptions.append(piece)
        parts.append("It depends on: " + "; ".join(comp_descriptions[:8]) + (
            "." if len(comp_descriptions) <= 8 else
            f"; and {len(comp_descriptions) - 8} more references."))

    return " ".join(parts)


def _fmt_value(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"${v:,.2f}"
        if abs(v) < 1:
            return f"{v:.4f}".rstrip("0").rstrip(".")
        return f"{v:.2f}"
    if isinstance(v, int):
        return f"${v:,}" if abs(v) >= 100 else str(v)
    return str(v)
