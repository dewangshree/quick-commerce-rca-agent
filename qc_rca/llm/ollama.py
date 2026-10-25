"""Local model access through Ollama (via langchain-ollama).

Nothing here ever calls a paid or cloud API. If Ollama is down or the model is
not installed, callers get ``LLMUnavailable`` and fall back to deterministic
behaviour.
"""
import json
import time
from functools import lru_cache

import httpx
from langchain_core.messages import HumanMessage, SystemMessage

from qc_rca.config import get_settings
from qc_rca.observability import get_logger, log_event

log = get_logger("llm")


class LLMUnavailable(RuntimeError):
    pass


def ollama_status(timeout: float = 2.0) -> dict:
    """Report whether Ollama is reachable and which configured models are installed."""
    s = get_settings()
    try:
        r = httpx.get(f"{s.ollama_base_url}/api/tags", timeout=timeout)
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
    except Exception as e:  # noqa: BLE001 - any failure means "unavailable"
        return {"reachable": False, "error": type(e).__name__, "llm_model": s.llm_model,
                "embedding_model": s.embedding_model, "llm_installed": False, "embedding_installed": False}

    def installed(m: str) -> bool:
        return m in names or f"{m}:latest" in names
    return {"reachable": True, "llm_model": s.llm_model, "embedding_model": s.embedding_model,
            "llm_installed": installed(s.llm_model), "embedding_installed": installed(s.embedding_model),
            "installed_models": sorted(names)}


@lru_cache
def _chat_model(json_mode: bool):
    from langchain_ollama import ChatOllama

    s = get_settings()
    return ChatOllama(
        model=s.llm_model, base_url=s.ollama_base_url, temperature=0, num_ctx=s.llm_num_ctx,
        format="json" if json_mode else None, client_kwargs={"timeout": s.llm_timeout_s},
        keep_alive="5m",
    )


def chat(system: str, user: str, json_mode: bool = False, max_retries: int | None = None) -> tuple[str, dict]:
    """Single bounded LLM call. Returns (text, usage). Raises LLMUnavailable."""
    s = get_settings()
    if not s.llm_enabled:
        raise LLMUnavailable("LLM disabled by configuration (LLM_ENABLED=false)")
    retries = s.llm_max_retries if max_retries is None else max_retries
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        start = time.perf_counter()
        try:
            msg = _chat_model(json_mode).invoke([SystemMessage(content=system), HumanMessage(content=user)])
            meta = getattr(msg, "usage_metadata", None) or {}
            usage = {
                "model": s.llm_model,
                "input_tokens": meta.get("input_tokens"),
                "output_tokens": meta.get("output_tokens"),
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "attempts": attempt + 1,
            }
            log_event(log, "llm call", **usage, json_mode=json_mode)
            return str(msg.content), usage
        except Exception as e:  # noqa: BLE001
            last_err = e
            log_event(log, "llm call failed", attempt=attempt + 1, error=f"{type(e).__name__}: {e}"[:300])
    raise LLMUnavailable(f"{type(last_err).__name__}: {last_err}"[:300])


def chat_json(system: str, user: str, max_retries: int | None = None) -> tuple[dict, dict]:
    text, usage = chat(system, user, json_mode=True, max_retries=max_retries)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"model returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise LLMUnavailable("model returned JSON that is not an object")
    return data, usage


@lru_cache
def _embedder():
    from langchain_ollama import OllamaEmbeddings

    s = get_settings()
    return OllamaEmbeddings(model=s.embedding_model, base_url=s.ollama_base_url,
                            client_kwargs={"timeout": s.embedding_timeout_s})


@lru_cache
def _fastembed():
    from fastembed import TextEmbedding  # only installed in the hosted image

    s = get_settings()
    return TextEmbedding(model_name=s.fastembed_model, threads=1,
                         cache_dir=str(s.fastembed_cache_dir) if s.fastembed_cache_dir else None)


def embedding_label() -> str:
    s = get_settings()
    return s.fastembed_model if s.embedding_backend == "fastembed" else s.embedding_model


def embeddings_available() -> bool:
    s = get_settings()
    if s.embedding_backend == "fastembed":
        try:
            _fastembed()
            return True
        except Exception:  # noqa: BLE001
            return False
    return bool(ollama_status().get("embedding_installed"))


def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    try:
        if s.embedding_backend == "fastembed":
            vectors = [v.tolist() for v in _fastembed().embed(texts, batch_size=16)]
        else:
            vectors = _embedder().embed_documents(texts)
    except Exception as e:  # noqa: BLE001
        raise LLMUnavailable(f"embedding failed: {type(e).__name__}: {e}"[:300]) from e
    if any(len(v) != s.embedding_dim for v in vectors):
        raise LLMUnavailable(f"embedding dimension mismatch: expected {s.embedding_dim}")
    return vectors


def reset_clients() -> None:
    _chat_model.cache_clear()
    _embedder.cache_clear()
    _fastembed.cache_clear()
