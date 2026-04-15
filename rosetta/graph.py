"""Dependency graph traversal: forward/backward trace."""
from __future__ import annotations

from typing import Iterable

from .models import CellModel, TraceNode, WorkbookModel


def _cell_label(wb: WorkbookModel, ref: str) -> str | None:
    c = wb.cells.get(ref)
    if not c:
        return None
    return c.semantic_label


def _named_range_for_ref(wb: WorkbookModel, ref: str) -> str | None:
    for nr in wb.named_ranges:
        if ref in nr.resolved_refs:
            return nr.name
    return None


def backward_trace(wb: WorkbookModel, ref: str, max_depth: int = 8) -> TraceNode:
    """Build a tree of what feeds into this cell."""
    visited: set[str] = set()

    def build(r: str, depth: int) -> TraceNode:
        cell = wb.cells.get(r)
        warnings: list[str] = []
        if not cell:
            return TraceNode(ref=r, depth=depth, warnings=["Cell not found or out of parsed range."])
        nr = _named_range_for_ref(wb, r)
        node = TraceNode(
            ref=r,
            label=cell.semantic_label,
            value=cell.value,
            formula=cell.formula,
            depth=depth,
            is_hardcoded=cell.is_hardcoded,
            is_volatile=cell.is_volatile,
            named_range=nr,
        )
        if cell.is_hardcoded:
            warnings.append("Hardcoded value — not computed from a formula.")
        if cell.is_volatile:
            warnings.append("Uses a volatile function (e.g. NOW/TODAY/OFFSET/INDIRECT).")
        node.warnings = warnings

        if r in visited or depth >= max_depth:
            return node
        visited.add(r)
        if cell.formula:
            for d in cell.depends_on:
                if ":" in d.split("!", 1)[-1]:
                    # logical range — represent as a single range node
                    rn = TraceNode(
                        ref=d,
                        label=f"range reference",
                        depth=depth + 1,
                    )
                    node.children.append(rn)
                    continue
                node.children.append(build(d, depth + 1))
        return node

    return build(ref, 0)


def forward_impacted(wb: WorkbookModel, ref: str, max_depth: int = 12) -> list[tuple[str, int]]:
    """BFS from `ref` over depended_by edges. Returns list of (ref, depth)."""
    seen: dict[str, int] = {ref: 0}
    queue: list[tuple[str, int]] = [(ref, 0)]
    result: list[tuple[str, int]] = []
    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        cell = wb.cells.get(current)
        if not cell:
            continue
        for child in cell.depended_by:
            if child in seen:
                continue
            seen[child] = depth + 1
            result.append((child, depth + 1))
            queue.append((child, depth + 1))
    return result


def forward_impacted_for_named_range(wb: WorkbookModel, name: str) -> list[tuple[str, int]]:
    """Return forward impacted cells for every cell resolved by a named range
    OR every cell that references the named range directly (by name).
    """
    target = next((n for n in wb.named_ranges if n.name.upper() == name.upper()), None)
    if not target:
        return []
    impacted: dict[str, int] = {}
    # Cells that reference the named range directly
    for ref, cell in wb.cells.items():
        if target.name in cell.named_ranges_used:
            impacted.setdefault(ref, 1)
            for r, d in forward_impacted(wb, ref):
                if r not in impacted or d + 1 < impacted[r]:
                    impacted[r] = d + 1
    # Resolved refs
    for r in target.resolved_refs:
        if ":" in r:
            continue
        if r in wb.cells:
            for rr, d in forward_impacted(wb, r):
                if rr not in impacted or d < impacted[rr]:
                    impacted[rr] = d
    return sorted(impacted.items(), key=lambda x: (x[1], x[0]))
