import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import rag  # noqa: E402


def test_payment_failed_finds_policy_or_offers():
    results = rag.search("payment failed")
    assert results
    top_sources = [r["source"] for r in results[:2]]
    assert any(s in ("retention_policy.md", "offers.md") for s in top_sources)


def test_cancel_returns_pause_guidance():
    results = rag.search("cancel")
    assert results[0]["source"] == "retention_policy.md"
    assert "pause" in results[0]["text"].lower()


def test_zero_relevance_dropped_and_shape():
    assert rag.search("zzzqqq") == []
    r = rag.search("yoga")[0]
    assert set(r) == {"source", "text", "relevance"} and r["relevance"] > 0
