"""Rosetta demo walkthrough — runs all the judge-facing demo flows end to end.

Usage:
    python3 demo.py                    # runs on dealership workbook
    python3 demo.py energy             # runs on energy workbook
    python3 demo.py all                # both

Output is printed to stdout. If ANTHROPIC_API_KEY is set, answers are polished
via Claude; otherwise the deterministic grounded answers are shown.
"""
from __future__ import annotations

import sys
from pathlib import Path

from rosetta.audit import audit_workbook
from rosetta.evaluator import Evaluator
from rosetta.graph import backward_trace, forward_impacted_for_named_range
from rosetta.parser import parse_workbook
from rosetta.qa import answer

HERE = Path(__file__).parent
DEALER = HERE / "data" / "dealership_financial_model.xlsx"
ENERGY = HERE / "data" / "energy_portfolio_model.xlsx"

SEPARATOR = "\n" + "─" * 80 + "\n"


def banner(text: str):
    print(SEPARATOR)
    print(f"  {text}")
    print(SEPARATOR)


def ingest(path: Path):
    banner(f"INGESTING: {path.name}")
    wb = parse_workbook(path)
    wb.findings = audit_workbook(wb)
    print(f"  Sheets ({len(wb.sheets)}): {[s.name for s in wb.sheets]}")
    print(f"  Hidden sheets: {[s.name for s in wb.sheets if s.hidden] or '(none)'}")
    print(f"  Total cells parsed: {len(wb.cells)}")
    print(f"  Formula cells: {wb.graph_summary.total_formula_cells}")
    print(f"  Cross-sheet references: {wb.graph_summary.cross_sheet_edges}")
    print(f"  Max dependency depth: {wb.graph_summary.max_depth}")
    print(f"  Circular references: {len(wb.graph_summary.circular_references)}")
    for cr in wb.graph_summary.circular_references:
        print(f"    • {' → '.join(cr.chain)} ({'intentional' if cr.intentional else 'error'})")
    print(f"  Named ranges ({len(wb.named_ranges)}):")
    for nr in wb.named_ranges[:12]:
        print(f"    • {nr.name} → {nr.resolved_refs} (value={nr.current_value})")
    print(f"  Hidden rows: " + ", ".join(f"{s.name}:{s.hidden_rows}" for s in wb.sheets if s.hidden_rows) or "")
    print(f"  Audit findings: {len(wb.findings)}")
    return wb


def ask(wb, q: str):
    banner(f"Q: {q}")
    r = answer(wb, q)
    print(f"[intent: {r.answer_type}, confidence: {r.confidence}]")
    print(r.answer)
    if r.warnings:
        print()
        for w in r.warnings:
            print(f"  ⚠ {w}")


def run_dealership():
    wb = ingest(DEALER)
    ask(wb, "What was total gross profit in March?")
    ask(wb, "How is Adjusted EBITDA calculated?")
    ask(wb, "What is the floor plan rate and where is it used?")
    ask(wb, "What would happen to Used Car gross if the floor plan rate went to 7%?")
    ask(wb, "Why is the Service Absorption Rate formula circular?")
    ask(wb, "Are there any stale assumptions?")
    ask(wb, "Row 23 on the Used Vehicle sheet looks different — is something wrong?")
    ask(wb, "Are there any hidden dependencies I should know about?")
    ask(wb, "Compare the structure of the sheets in this workbook.")


def run_energy():
    wb = ingest(ENERGY)
    ask(wb, "What is the total portfolio expected revenue?")
    ask(wb, "How is the performance ratio calculated for Site Alpha?")
    ask(wb, "Is the system loss calculation additive or multiplicative for Site Alpha?")
    ask(wb, "Are there any volatile or INDIRECT formulas?")
    ask(wb, "If AnnualDegradationRate went to 1%, what would change?")
    ask(wb, "Which sites have assumption overrides?")
    ask(wb, "Are there any stale assumptions?")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "dealer"
    if which in ("dealer", "dealership", "all"):
        run_dealership()
    if which in ("energy", "all"):
        run_energy()


if __name__ == "__main__":
    main()
