"""Probe the configured local model's capabilities before relying on them.

Measures (on small labelled sets, results written to docs/evaluation/llm_probe_results.json):
  1. instruction following: JSON intent classification accuracy / valid-JSON rate
  2. tool calling: correct tool name + arguments via LangChain bind_tools
  3. structured output: with_structured_output(Pydantic) validity
  4. grounded narrative: share of narratives whose numbers all appear in the evidence
Usage: python scripts/llm_probe.py
"""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.tools import tool  # noqa: E402
from langchain_ollama import ChatOllama  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from qc_rca.config import ROOT_DIR, get_settings  # noqa: E402
from qc_rca.llm.ollama import embed, ollama_status  # noqa: E402
from qc_rca.workflow.evidence import grounded_numbers  # noqa: E402
from qc_rca.workflow.understanding import LLM_INTENT_SYSTEM, parse_llm_intent  # noqa: E402

INTENT_SET = [
    ("How did Bangalore do on 2026-04-22?", "city_summary"),
    ("Why did STORE_101 underperform that day?", "store_rca"),
    ("Walk me through the morning hours at STORE_101.", "hourly_drilldown"),
    ("Compare STORE_101 and STORE_102.", "store_comparison"),
    ("Which cities had the highest breach rate?", "city_comparison"),
    ("What is OR2A?", "documentation"),
    ("Explain the RCA playbook.", "documentation"),
    ("Which stores were the worst performers?", "top_stores"),
    ("Give me the overall SLA numbers for the network.", "sla_overview"),
    ("What's the weather in Paris?", "out_of_scope"),
    ("How bad was it in Chennai?", "city_summary"),
    ("Root cause for store 45 please", "store_rca"),
    ("Show the hourly breakdown for STORE_003", "hourly_drilldown"),
    ("What does a booking gap mean?", "documentation"),
    ("Mumbai vs Pune performance", "city_comparison"),
    ("Tell me a joke", "out_of_scope"),
]


@tool
def rca_tool(store: str, date: str) -> str:
    """Run root cause analysis for a store (e.g. STORE_101) on a date (YYYY-MM-DD)."""
    return "ok"


@tool
def city_summary_tool(city: str, date: str) -> str:
    """Summarise store performance for a city on a date (YYYY-MM-DD)."""
    return "ok"


@tool
def docs_tool(topic: str) -> str:
    """Look up documentation: one of 'rca_logic', 'or2a', 'gold_schema'."""
    return "ok"


TOOL_SET = [
    ("Why did STORE_101 underperform on 2026-04-22?", "rca_tool", {"store": "STORE_101", "date": "2026-04-22"}),
    ("How did Chennai do on 2026-04-22?", "city_summary_tool", {"city": "Chennai", "date": "2026-04-22"}),
    ("What is OR2A? Check the docs.", "docs_tool", {"topic": "or2a"}),
    ("Run RCA for STORE_045 for 2026-04-22", "rca_tool", {"store": "STORE_045", "date": "2026-04-22"}),
    ("Summarise Noida on 2026-04-22", "city_summary_tool", {"city": "Noida", "date": "2026-04-22"}),
    ("Explain the RCA logic documentation", "docs_tool", {"topic": "rca_logic"}),
]


class Extraction(BaseModel):
    store: str | None = Field(description="store code like STORE_101, or null")
    city: str | None = Field(description="city name, or null")
    date: str | None = Field(description="YYYY-MM-DD or null")


STRUCT_SET = [
    ("Why did STORE_101 underperform on 2026-04-22?", {"store": "STORE_101", "city": None, "date": "2026-04-22"}),
    ("How did Bangalore do on 2026-04-22?", {"store": None, "city": "Bangalore", "date": "2026-04-22"}),
    ("Hours at STORE_007 in Noida", {"store": "STORE_007", "city": "Noida", "date": None}),
    ("Chennai yesterday", {"store": None, "city": "Chennai", "date": None}),
]

NARRATIVE_EVIDENCE = [
    {"store": "STORE_101", "date": "2026-04-22", "total_orders": 397, "weighted_breach_rate": "8.6%",
     "weighted_avg_or2a_min": 21.0, "problem_hours": 5,
     "factors": [{"factor": "booking gap", "hours": [10, 12, 19], "share_of_breached_orders": "70.6%"}]},
    {"city": "Bangalore", "date": "2026-04-22", "worst_store": "STORE_091", "worst_breach_rate": "81.2%",
     "worst_avg_or2a_min": 873.2, "stores": 30},
]


def main():
    s = get_settings()
    status = ollama_status()
    if not status["reachable"] or not status["llm_installed"]:
        print(json.dumps(status, indent=2))
        sys.exit("Ollama or the configured model is not available; nothing to probe.")
    llm = ChatOllama(model=s.llm_model, base_url=s.ollama_base_url, temperature=0, num_ctx=s.llm_num_ctx,
                     client_kwargs={"timeout": s.llm_timeout_s})
    results = {"model": s.llm_model, "embedding_model": s.embedding_model, "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    # 1. intent JSON
    json_llm = llm.bind(format="json")
    rows, lat = [], []
    for q, expected in INTENT_SET:
        t = time.perf_counter()
        raw = json_llm.invoke([("system", LLM_INTENT_SYSTEM), ("human", q)]).content
        lat.append((time.perf_counter() - t) * 1000)
        try:
            got = parse_llm_intent(json.loads(raw))
            valid = True
        except Exception:  # noqa: BLE001
            got, valid = None, False
        rows.append({"q": q, "expected": expected, "got": got.value if got else None, "valid_json": valid, "raw": raw[:120]})
    results["intent_classification"] = {
        "n": len(rows), "accuracy": sum(r["got"] == r["expected"] for r in rows) / len(rows),
        "valid_enum_rate": sum(r["got"] is not None for r in rows) / len(rows),
        "median_latency_ms": round(statistics.median(lat)), "rows": rows}

    # 2. tool calling
    tool_llm = llm.bind_tools([rca_tool, city_summary_tool, docs_tool])
    rows = []
    for q, name, args in TOOL_SET:
        msg = tool_llm.invoke([("human", q)])
        calls = getattr(msg, "tool_calls", []) or []
        first = calls[0] if calls else {}
        rows.append({"q": q, "expected_tool": name, "got_tool": first.get("name"), "args": first.get("args"),
                     "name_ok": first.get("name") == name,
                     "args_ok": first.get("args") == args})
    results["tool_calling"] = {"n": len(rows), "tool_name_accuracy": sum(r["name_ok"] for r in rows) / len(rows),
                               "exact_args_accuracy": sum(r["args_ok"] for r in rows) / len(rows), "rows": rows}

    # 3. structured output
    struct_llm = llm.with_structured_output(Extraction)
    rows = []
    for q, expected in STRUCT_SET:
        try:
            got = struct_llm.invoke([("human", f"Extract entities from: {q}")])
            got_d = got.model_dump() if got else None
            valid = got is not None
        except Exception as e:  # noqa: BLE001
            got_d, valid = {"error": type(e).__name__}, False
        rows.append({"q": q, "expected": expected, "got": got_d, "valid": valid, "exact": got_d == expected})
    results["structured_output"] = {"n": len(rows), "valid_rate": sum(r["valid"] for r in rows) / len(rows),
                                    "exact_match_rate": sum(r["exact"] for r in rows) / len(rows), "rows": rows}

    # 4. grounded narrative
    from qc_rca.workflow.graph import NARRATIVE_SYSTEM
    rows = []
    for ev in NARRATIVE_EVIDENCE:
        for _ in range(2):
            text = llm.invoke([("system", NARRATIVE_SYSTEM), ("human", f"Evidence JSON: {json.dumps(ev)}")]).content
            ok, bad = grounded_numbers(text, json.dumps(ev))
            rows.append({"evidence": list(ev)[:3], "grounded": ok, "ungrounded_numbers": bad, "text": text[:300],
                         "causal_language": "caused by" in text.lower()})
    results["grounded_narrative"] = {"n": len(rows), "grounded_rate": sum(r["grounded"] for r in rows) / len(rows),
                                     "rows": rows}

    # embeddings
    t = time.perf_counter()
    try:
        v = embed(["pileup carried orders", "rider no-show utilization"])
        results["embeddings"] = {"ok": True, "dim": len(v[0]), "latency_ms": round((time.perf_counter() - t) * 1000)}
    except Exception as e:  # noqa: BLE001
        results["embeddings"] = {"ok": False, "error": str(e)[:200]}

    out = ROOT_DIR / "docs" / "evaluation" / "llm_probe_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    summary = {k: {kk: vv for kk, vv in v.items() if kk != "rows"} if isinstance(v, dict) else v
               for k, v in results.items()}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
