"""RAG: chunking, metadata filtering, sample labelling, retrieval evaluation (hit@k)."""
import json

import pytest

from qc_rca.config import ROOT_DIR, get_settings
from qc_rca.rag.chunking import chunk_document, load_document
from qc_rca.rag.store import retrieve
from tests.conftest import OLLAMA_OK, requires_ollama

# Representative questions → document that must be retrieved in the top k.
EVAL_SET = [
    ("What is OR2A?", "reference/order_ready_to_assignment.md"),
    ("How do I calculate weighted breach rate for a store-day?", "reference/quick_commerce_rca_logic.md"),
    ("When is a demand spike flagged?", "reference/quick_commerce_rca_logic.md"),
    ("What does man_hour mean and how is it computed?", "reference/quick_commerce_orders_gold.md"),
    ("rider_hours_per_hour unit minutes", "reference/quick_commerce_orders_gold.md"),
    ("What should we do about rider no-shows?", "samples/sop_rider_supply.md"),
    ("How to handle a sustained pileup", "samples/sop_pileup_and_demand.md"),
    ("severity bands red amber green", "samples/sla_definitions.md"),
    ("past incident where no-shows led to a pileup", "samples/incident_sample_01_evening_pileup.md"),
    ("historical morning booking gap incident", "samples/incident_sample_02_morning_booking_gap.md"),
]


def test_chunking_keeps_headings_and_sample_flag():
    root = get_settings().knowledge_base_dir
    doc = load_document(root / "samples" / "sla_definitions.md", root)
    assert doc.is_sample and doc.doc_type == "sla"
    chunks = chunk_document(doc, size=300, overlap=50)
    assert len(chunks) >= 4 and all(c.meta["is_sample"] for c in chunks)
    assert any(c.heading == "Severity bands for a store-day" for c in chunks)
    ref = load_document(root / "reference" / "quick_commerce_rca_logic.md", root)
    assert not ref.is_sample and ref.doc_type == "playbook"


def test_metadata_filter_and_sample_exclusion():
    res = retrieve("pileup", k=5, doc_types=["incident"], use_vectors=False)
    assert res.chunks and all(c.doc_type == "incident" for c in res.chunks)
    res = retrieve("pileup", k=8, include_samples=False, use_vectors=False)
    assert res.chunks and not any(c.is_sample for c in res.chunks)


def _hit_rate(use_vectors: bool, k: int = 4):
    rows = []
    for q, expected in EVAL_SET:
        res = retrieve(q, k=k, use_vectors=use_vectors)
        rank = next((i + 1 for i, c in enumerate(res.chunks) if c.source_path == expected), None)
        rows.append({"query": q, "expected": expected, "rank": rank, "mode": res.mode})
    return sum(r["rank"] is not None for r in rows) / len(rows), rows


def test_keyword_retrieval_eval():
    rate, rows = _hit_rate(use_vectors=False)
    _record("keyword", rate, rows)
    assert rate >= 0.7, rows


@requires_ollama
def test_hybrid_retrieval_eval():
    rate, rows = _hit_rate(use_vectors=True)
    _record("hybrid", rate, rows)
    assert all(r["mode"] == "hybrid" for r in rows)
    assert rate >= 0.8, rows


def _record(mode, rate, rows):
    out = ROOT_DIR / "docs" / "evaluation" / "rag_eval_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(out.read_text()) if out.exists() else {}
    data[mode] = {"k": 4, "hit_rate": rate, "rows": rows}
    out.write_text(json.dumps(data, indent=2))


def test_keyword_only_when_embeddings_unavailable(monkeypatch):
    from qc_rca.llm import ollama

    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://127.0.0.1:9")
    ollama.reset_clients()
    res = retrieve("What is OR2A?")
    assert res.mode == "keyword" and res.warning
    assert res.chunks
    ollama.reset_clients()


@pytest.mark.skipif(not OLLAMA_OK, reason="needs embeddings")
def test_chunks_are_embedded():
    from qc_rca.rag.store import corpus_stats

    st = corpus_stats()
    assert st["chunks"] > 0 and st["embedded_chunks"] == st["chunks"]
