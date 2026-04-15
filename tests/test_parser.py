"""Smoke tests for parser + graph + evaluator + qa on demo fixtures."""
from pathlib import Path

from rosetta.audit import audit_workbook
from rosetta.evaluator import Evaluator
from rosetta.graph import backward_trace, forward_impacted_for_named_range
from rosetta.parser import parse_workbook
from rosetta.qa import answer

DATA = Path(__file__).parent.parent / "data"


def test_dealer_parse():
    wb = parse_workbook(DATA / "dealership_financial_model.xlsx")
    assert len(wb.sheets) == 6
    assert wb.graph_summary.total_formula_cells > 200
    assert wb.graph_summary.cross_sheet_edges > 15
    assert len(wb.named_ranges) >= 8
    # Circular
    assert len(wb.graph_summary.circular_references) >= 1
    # Key cell
    assert "P&L Summary!B32" in wb.cells
    assert wb.cells["P&L Summary!B32"].formula is not None


def test_dealer_audit():
    wb = parse_workbook(DATA / "dealership_financial_model.xlsx")
    findings = audit_workbook(wb)
    categories = {f.category for f in findings}
    assert "stale_assumption" in categories
    assert "hardcoded_anomaly" in categories
    assert "circular" in categories


def test_dealer_trace():
    wb = parse_workbook(DATA / "dealership_financial_model.xlsx")
    t = backward_trace(wb, "P&L Summary!B32")
    assert t.formula is not None
    # Has multiple children
    assert len(t.children) >= 3
    # Some child should resolve to Assumptions sheet (named ranges)
    flat = [c.ref for c in t.children]
    assert any("Assumptions!" in r for r in flat)


def test_dealer_whatif_floorplan():
    wb = parse_workbook(DATA / "dealership_financial_model.xlsx")
    ev = Evaluator(wb, overrides={"Assumptions!B2": 0.07})
    impacted = forward_impacted_for_named_range(wb, "FloorPlanRate")
    assert len(impacted) > 50
    # Used Vehicle H2 (floor plan interest) should change
    assert ev.value_of("Used Vehicle!H2") != wb.cells["Used Vehicle!H2"].value


def test_dealer_qa_ebitda():
    wb = parse_workbook(DATA / "dealership_financial_model.xlsx")
    r = answer(wb, "How is Adjusted EBITDA calculated?")
    assert r.answer_type == "formula"
    assert "Adjusted EBITDA" in r.answer or "B32" in r.answer
    assert "B18" in r.answer  # Total Gross Profit reference
    assert "OwnerCompensationAddback" in r.answer


def test_energy_parse_and_vlookup():
    wb = parse_workbook(DATA / "energy_portfolio_model.xlsx")
    assert len(wb.sheets) == 7
    # VLOOKUP evaluated successfully for Site Alpha Performance Ratio
    assert wb.cells["Portfolio Summary!H2"].value not in (None, 0, 0.0)
    # Multiplicative system loss produces correct value
    loss = wb.cells["System Losses!H2"].value
    assert loss is not None and 0 < loss < 0.5


def test_energy_qa_performance_alpha():
    wb = parse_workbook(DATA / "energy_portfolio_model.xlsx")
    r = answer(wb, "How is the performance ratio calculated for Site Alpha?")
    assert "Alpha" in r.answer
    assert "H2" in r.answer
    assert "VLOOKUP" in r.answer or "System Loss" in r.answer
