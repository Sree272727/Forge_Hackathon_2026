"""Semantic search verification tests.

Runs full ingest pipeline against the dealership fixture then validates:
  - Semantic search returns relevant cells for fuzzy queries
  - Three-tier find_cells falls through correctly
  - Known-bad queries return empty, not garbage
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rosetta.parser import parse_workbook
from rosetta.audit import audit_workbook
from rosetta.cell_context import build_cell_contexts
from rosetta.embeddings import QdrantIndex
from rosetta.tools import execute_tool


def test_semantic_search_quality():
    """Verify top-5 semantic results for key queries contain relevant cells."""
    wb = parse_workbook("data/dealership_financial_model.xlsx")
    wb.findings = audit_workbook(wb)
    contexts = build_cell_contexts(wb)
    assert len(contexts) > 500, f"Expected 500+ contexts, got {len(contexts)}"
    upserted = QdrantIndex.upsert_cells(wb.workbook_id, contexts)
    assert upserted == len(contexts)

    # Each: (query, list of acceptable substrings — any one match passes)
    test_cases = [
        ("EBITDA", ["ebitda"]),
        ("adjusted earnings", ["ebitda"]),                   # fuzzy → Adjusted EBITDA
        ("floor plan rate", ["floor plan"]),                 # → Floor Plan Interest
        ("owner comp addback", ["ownercompens", "owner"]),   # → OwnerCompensationAddback
        ("vehicle gross", ["gross", "vehicle"]),             # basic keyword-ish
    ]

    for query, expected_substrings in test_cases:
        hits = QdrantIndex.search(wb.workbook_id, query, limit=10)
        haystack = (
            " ".join((h.get("label") or "") for h in hits).lower()
            + " "
            + " ".join((h.get("context") or "") for h in hits).lower()
        )
        found = any(sub in haystack for sub in expected_substrings)
        assert found, (f"Semantic search for '{query}' didn't surface any of "
                       f"{expected_substrings}. Top labels: "
                       f"{[h.get('label') for h in hits[:3]]}")


def test_three_tier_auto_fallback():
    """auto tier should use the cheapest tier that produces results."""
    wb = parse_workbook("data/dealership_financial_model.xlsx")
    wb.findings = audit_workbook(wb)
    QdrantIndex.upsert_cells(wb.workbook_id, build_cell_contexts(wb))

    # Tier 1: exact cell ref
    r = execute_tool(wb, "find_cells", {"keyword": "P&L Summary!B32", "tier": "auto"})
    assert r["tier_used"] == "exact"
    assert r["count"] == 1
    assert r["matches"][0]["ref"] == "P&L Summary!B32"

    # Tier 2: keyword match on semantic_label
    r = execute_tool(wb, "find_cells", {"keyword": "EBITDA", "tier": "auto"})
    assert r["tier_used"] == "keyword"
    assert r["count"] > 0

    # Tier 3: only semantic (word not present in any label)
    r = execute_tool(wb, "find_cells",
                     {"keyword": "bottom-line cash generation",
                      "tier": "auto"})
    assert r["tier_used"] in ("semantic", "none"), f"got {r['tier_used']}"


def test_semantic_threshold_rejects_garbage():
    """Low-similarity matches should be dropped."""
    wb = parse_workbook("data/dealership_financial_model.xlsx")
    wb.findings = audit_workbook(wb)
    QdrantIndex.upsert_cells(wb.workbook_id, build_cell_contexts(wb))

    r = execute_tool(wb, "find_cells",
                     {"keyword": "asjkdfhaskdfhj random gibberish xyzzy plugh",
                      "tier": "semantic"})
    # If we got results, all scores should be above our threshold (0.55).
    # If threshold is too loose, the test will fail — that signals a tuning problem.
    for m in r["matches"]:
        assert m["score"] >= 0.55, f"Low-score match leaked: {m}"


if __name__ == "__main__":
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
