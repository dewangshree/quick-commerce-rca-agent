"""Document ingestion and hybrid retrieval over PostgreSQL (pgvector + full-text).

Retrieval = reciprocal-rank fusion of
  * cosine similarity on pgvector embeddings (when the embedding model works), and
  * ``websearch_to_tsquery`` full-text ranking (always available).
If embeddings are unavailable the result is lexical-only and says so
(``mode="keyword"``), rather than failing.
"""
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from qc_rca.config import get_settings
from qc_rca.db.models import Document, DocumentChunk
from qc_rca.db.session import get_readonly_engine
from qc_rca.llm.ollama import LLMUnavailable, embed, embedding_label
from qc_rca.observability import get_logger, log_event
from qc_rca.rag.chunking import chunk_document, load_document

log = get_logger("rag")
RRF_K = 60


class RetrievedChunk(BaseModel):
    chunk_id: int
    source_path: str
    title: str
    heading: str | None
    doc_type: str
    is_sample: bool
    content: str
    score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None


class RetrievalResult(BaseModel):
    query: str
    mode: str  # hybrid | keyword | vector
    chunks: list[RetrievedChunk]
    warning: str | None = None


def ingest_documents(session: Session, root: Path | None = None, with_embeddings: bool = True) -> dict:
    """Idempotently load all markdown under the knowledge base. Unchanged docs are skipped."""
    s = get_settings()
    root = Path(root or s.knowledge_base_dir)
    stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "chunks": 0, "embedded": False,
             "warning": None}
    seen = set()
    for path in sorted(root.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        doc = load_document(path, root)
        seen.add(doc.source_path)
        existing = session.scalar(select(Document).where(Document.source_path == doc.source_path))
        needs_embed = with_embeddings and existing is not None and existing.embedding_model != embedding_label()
        if existing and existing.sha256 == doc.sha256 and not needs_embed:
            stats["unchanged"] += 1
            continue
        chunks = chunk_document(doc, s.rag_chunk_size, s.rag_chunk_overlap)
        vectors, model = [None] * len(chunks), None
        if with_embeddings:
            try:
                vectors, model = embed([c.content for c in chunks]), embedding_label()
                stats["embedded"] = True
            except LLMUnavailable as e:
                stats["warning"] = f"embeddings unavailable, stored for keyword search only: {e}"
        if existing:
            session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == existing.id))
            existing.title, existing.doc_type, existing.is_sample = doc.title, doc.doc_type, doc.is_sample
            existing.sha256, existing.embedding_model = doc.sha256, model
            target = existing
            stats["updated"] += 1
        else:
            target = Document(source_path=doc.source_path, title=doc.title, doc_type=doc.doc_type,
                              is_sample=doc.is_sample, sha256=doc.sha256, embedding_model=model)
            session.add(target)
            stats["added"] += 1
        session.flush()
        for c, v in zip(chunks, vectors):
            session.add(DocumentChunk(document_id=target.id, chunk_index=c.index, heading=c.heading,
                                      content=c.content, extra=c.meta, embedding=v))
        stats["chunks"] += len(chunks)
    for stale in session.scalars(select(Document).where(Document.source_path.not_in(sorted(seen) or [""]))):
        session.delete(stale)
        stats["removed"] += 1
    log_event(log, "documents ingested", **stats)
    return stats


def _filters(doc_types: list[str] | None, include_samples: bool) -> tuple[str, dict]:
    clauses, params = [], {}
    if doc_types:
        clauses.append("d.doc_type = ANY(:doc_types)")
        params["doc_types"] = list(doc_types)
    if not include_samples:
        clauses.append("d.is_sample = false")
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def retrieve(query: str, k: int | None = None, doc_types: list[str] | None = None, include_samples: bool = True,
             use_vectors: bool = True, engine: Engine | None = None) -> RetrievalResult:
    s = get_settings()
    k = k or s.rag_top_k
    engine = engine or get_readonly_engine()
    where, params = _filters(doc_types, include_samples)
    pool = k * 3
    cols = ("c.id, d.source_path, d.title, c.heading, d.doc_type, d.is_sample, c.content")
    keyword_sql = text(f"""
        SELECT {cols}, ts_rank_cd(c.tsv, q) AS score
        FROM document_chunks c JOIN documents d ON d.id = c.document_id,
             websearch_to_tsquery('english', :q) q
        WHERE c.tsv @@ q {where}
        ORDER BY score DESC, c.id LIMIT :pool""")
    # OR-semantics fallback: websearch_to_tsquery ANDs terms, which is strict for questions.
    keyword_or_sql = text(f"""
        SELECT {cols}, ts_rank_cd(c.tsv, q) AS score
        FROM document_chunks c JOIN documents d ON d.id = c.document_id,
             to_tsquery('english', :q_or) q
        WHERE c.tsv @@ q {where}
        ORDER BY score DESC, c.id LIMIT :pool""")
    vector_sql = text(f"""
        SELECT {cols}, 1 - (c.embedding <=> CAST(:qvec AS vector)) AS score
        FROM document_chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.embedding IS NOT NULL {where}
        ORDER BY c.embedding <=> CAST(:qvec AS vector), c.id LIMIT :pool""")

    rows: dict[int, dict] = {}
    warning, mode = None, "keyword"
    with engine.connect() as conn:
        kw = conn.execute(keyword_sql, {"q": query, "pool": pool, **params}).mappings().all()
        if len(kw) < k:
            terms = conn.execute(text("SELECT lexeme FROM unnest(to_tsvector('english', :q))"), {"q": query}).scalars()
            terms = [t for t in terms if t.isalnum()]
            if terms:
                q_or = " | ".join(terms)
                extra = conn.execute(keyword_or_sql, {"q_or": q_or, "pool": pool, **params}).mappings().all()
                kw = list(kw) + [r for r in extra if r["id"] not in {x["id"] for x in kw}]
        for rank, r in enumerate(kw, 1):
            rows.setdefault(r["id"], dict(r))["keyword_rank"] = rank
        if use_vectors:
            try:
                qvec = embed([query])[0]
                vec = conn.execute(vector_sql, {"qvec": str(qvec), "pool": pool, **params}).mappings().all()
                for rank, r in enumerate(vec, 1):
                    rows.setdefault(r["id"], dict(r))["vector_rank"] = rank
                mode = "hybrid" if vec else "keyword"
                if not vec:
                    warning = "no embedded chunks found; keyword search only (run document ingestion with Ollama up)"
            except LLMUnavailable as e:
                warning = f"embedding model unavailable, keyword search only: {e}"

    fused = []
    for r in rows.values():
        score = sum(1.0 / (RRF_K + r[key]) for key in ("vector_rank", "keyword_rank") if r.get(key))
        fused.append(RetrievedChunk(
            chunk_id=r["id"], source_path=r["source_path"], title=r["title"], heading=r["heading"],
            doc_type=r["doc_type"], is_sample=r["is_sample"], content=r["content"], score=round(score, 6),
            vector_rank=r.get("vector_rank"), keyword_rank=r.get("keyword_rank"),
        ))
    fused.sort(key=lambda c: (-c.score, c.chunk_id))
    return RetrievalResult(query=query, mode=mode, chunks=fused[:k], warning=warning)


def corpus_stats(engine: Engine | None = None) -> dict:
    engine = engine or get_readonly_engine()
    with engine.connect() as c:
        docs = c.execute(select(Document.doc_type, Document.is_sample, func.count()).group_by(
            Document.doc_type, Document.is_sample)).all()
        chunks = c.scalar(select(func.count()).select_from(DocumentChunk))
        embedded = c.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.embedding.is_not(None)))
    return {"documents": [{"doc_type": d, "is_sample": s, "count": n} for d, s, n in docs],
            "chunks": chunks, "embedded_chunks": embedded}
