"""Unit tests for the citation auditor."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rosetta.auditor import audit, AuditResult
from rosetta.conversation import ToolCall
from rosetta.models import (
    AuditFinding, CellModel, DependencyGraphSummary,
    NamedRangeModel, SheetModel, WorkbookModel
)


def make_wb() -> WorkbookModel:
    """Minimal fixture workbook with known values."""
    cells = {
        "P&L Summary!G32": CellModel(
            sheet="P&L Summary", coord="G32", ref="P&L Summary!G32",
            value=142300.0, formula="G18-G25+B15+B16",
            semantic_label="Adjusted EBITDA"
        ),
        "P&L Summary!G18": CellModel(
            sheet="P&L Summary", coord="G18", ref="P&L Summary!G18",
            value=487500.0, formula="SUM(...)",
            semantic_label="Total Gross Profit"
        ),
        "Assumptions!B15": CellModel(
            sheet="Assumptions", coord="B15", ref="Assumptions!B15",
            value=8000.0, semantic_label="Owner Compensation"
        ),
        "Assumptions!B2": CellModel(
            sheet="Assumptions", coord="B2", ref="Assumptions!B2",
            value=0.058, semantic_label="FloorPlanRate"
        ),
    }
    return WorkbookModel(
        workbook_id="wb_test",
        filename="test.xlsx",
        sheets=[SheetModel(name="P&L Summary"), SheetModel(name="Assumptions")],
        named_ranges=[
            NamedRangeModel(
                name="FloorPlanRate", scope="workbook", raw_value="Assumptions!$B$2",
                resolved_refs=["Assumptions!B2"], current_value=0.058
            ),
        ],
        cells=cells,
        graph_summary=DependencyGraphSummary(
            total_formula_cells=2, max_depth=2, cross_sheet_edges=1
        ),
        findings=[
            AuditFinding(severity="warning", category="stale_assumption",
                         message="FloorPlanRate last updated 2023-01-01"),
        ],
    )


def make_tool_log(entries: list[dict]) -> list[ToolCall]:
    """Build a ToolCall list from simplified dicts."""
    return [
        ToolCall(turn_id=1, tool_name=e.get("name", "get_cell"),
                 input=e.get("input", {}), output=e.get("output", {}))
        for e in entries
    ]


# --- Test cases ---

def test_fully_grounded_answer():
    wb = make_wb()
    log = make_tool_log([
        {"name": "get_cell", "output": {"ref": "P&L Summary!G32", "value": 142300.0}},
        {"name": "get_cell", "output": {"ref": "P&L Summary!G18", "value": 487500.0}},
        {"name": "get_cell", "output": {"ref": "Assumptions!B15", "value": 8000.0}},
    ])
    answer = ("Adjusted EBITDA is in P&L Summary!G32 and equals $142,300. "
              "It's Total Gross Profit (P&L Summary!G18: $487,500) plus "
              "Owner Compensation (Assumptions!B15: $8,000).")
    result = audit(answer, log, wb)
    assert result.status == "passed", f"Violations: {result.violations}"
    assert "142300" in " ".join(result.verified_numbers) or "$142,300" in result.verified_numbers


def test_hallucinated_number_caught():
    wb = make_wb()
    log = make_tool_log([
        {"name": "get_cell", "output": {"ref": "P&L Summary!G32", "value": 142300.0}},
    ])
    answer = "Adjusted EBITDA is in P&L Summary!G32 and equals $999,999."
    result = audit(answer, log, wb)
    assert result.status == "failed"
    assert any("999" in v for v in result.violations), f"Violations were: {result.violations}"


def test_hallucinated_cell_ref_caught():
    wb = make_wb()
    log = make_tool_log([
        {"name": "get_cell", "output": {"ref": "P&L Summary!G32", "value": 142300.0}},
    ])
    answer = "See the details in Fabricated!ZZ999 — value is $142,300."
    result = audit(answer, log, wb)
    assert result.status == "failed"
    assert any("Fabricated!ZZ999" in v for v in result.violations), f"Violations were: {result.violations}"


def test_qualitative_stale_allowed_if_finding_exists():
    wb = make_wb()  # has a stale_assumption finding
    log = make_tool_log([
        {"name": "list_findings", "output": {"count": 1,
                                             "findings": [{"category": "stale_assumption",
                                                           "message": "..."}]}},
    ])
    answer = "The FloorPlanRate is stale — last updated 2023-01-01."
    result = audit(answer, log, wb)
    assert result.status == "passed", f"Violations: {result.violations}"


def test_qualitative_rejected_if_no_finding():
    # Build a wb without any findings of that category
    wb = make_wb()
    wb.findings = []  # strip all findings
    log = make_tool_log([])
    answer = "This formula is volatile and depends on hidden sheets."
    result = audit(answer, log, wb)
    assert result.status == "failed", f"Expected fail but passed with verified: {result.verified_qualitative}"


def test_zero_always_allowed():
    wb = make_wb()
    log = make_tool_log([])
    answer = "The tier gate fails and the payout is $0."
    result = audit(answer, log, wb)
    assert result.status == "passed", f"Violations: {result.violations}"


def test_rounded_display_tolerance():
    """Tool returned $487,532.17 but answer says '$487,500' (rounded)."""
    wb = make_wb()
    log = make_tool_log([
        {"name": "get_cell", "output": {"ref": "P&L Summary!G18", "value": 487532.17}},
    ])
    answer = "Total Gross Profit (P&L Summary!G18) is about $487,500."
    result = audit(answer, log, wb)
    assert result.status == "passed", f"Violations: {result.violations}"


def test_percent_matches_fraction():
    """'5.8%' in answer should match 0.058 in a tool result."""
    wb = make_wb()
    log = make_tool_log([
        {"name": "resolve_named_range", "output": {"name": "FloorPlanRate", "current_value": 0.058}},
    ])
    answer = "FloorPlanRate is 5.8%."
    result = audit(answer, log, wb)
    assert result.status == "passed", f"Violations: {result.violations}"


if __name__ == "__main__":
    import sys
    passed = 0
    failed = 0
    for name in list(globals()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                print(f"  ✓ {name}")
                passed += 1
            except AssertionError as e:
                print(f"  ✗ {name}: {e}")
                failed += 1
            except Exception as e:
                print(f"  ✗ {name}: {type(e).__name__}: {e}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
