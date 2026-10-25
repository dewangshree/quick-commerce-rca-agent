"""LangGraph investigation workflow.

    validate ─► classify ─┬─► analytics ─┬─► retrieve ─► correlate ─► validate_rca ─► report
                          │              └──────────────►┘
                          ├─► retrieve (documentation)
                          └─► report (clarification / out of scope / rejected)

Deterministic nodes produce all numbers. The local LLM is optional and used
only for (a) intent classification when rules cannot decide and (b) a short
narrative that must pass a number-grounding check.
"""
import json
import time
import uuid
from datetime import date
from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.exc import OperationalError

from qc_rca.analytics import service
from qc_rca.analytics.rca import StoreDayRCA
from qc_rca.config import get_settings
from qc_rca.llm import ollama
from qc_rca.observability import get_logger, investigation_id_var, log_event
from qc_rca.rag.store import retrieve
from qc_rca.workflow import evidence as evmod
from qc_rca.workflow.schemas import (
    ANALYTICS_INTENTS,
    RCA_INTENTS,
    Confidence,
    Entities,
    Intent,
    InvestigationReport,
    InvestigationState,
    LLMInfo,
)
from qc_rca.workflow.understanding import (
    LLM_INTENT_SYSTEM,
    QuestionRejected,
    apply_context,
    classify_intent,
    context_from_history,
    extract_entities,
    parse_llm_intent,
    validate_question,
)

log = get_logger("workflow")


def _timed(name):
    def deco(fn):
        def wrapper(state: InvestigationState):
            start = time.perf_counter()
            try:
                out = fn(state) or {}
            except Exception as e:  # noqa: BLE001 - a node failure must not crash the investigation
                log_event(log, "node failed", node=name, error=f"{type(e).__name__}: {e}"[:300])
                out = {"errors": [*state.get("errors", []), f"{name}: internal error ({type(e).__name__})"]}
            timings = dict(state.get("timings", {}))
            timings[name] = round((time.perf_counter() - start) * 1000, 1)
            out["timings"] = timings
            return out
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


@lru_cache(maxsize=1)
def _known_cities_cached(_bucket: int) -> tuple[list[str], list[str]]:
    # Default/baseline logic uses real dates only; synthetic demo days are never treated as history.
    return [c["normalized_name"] for c in service.list_cities()], service.real_dates() or service.available_dates()


def known_cities_and_dates() -> tuple[list[str], list[str]]:
    return _known_cities_cached(int(time.time() // 300))


# ── nodes ────────────────────────────────────────────────────────────────────

@_timed("validate")
def validate_node(state: InvestigationState):
    try:
        q = validate_question(state["question"], get_settings().max_question_chars)
    except QuestionRejected as e:
        return {"status": "rejected", "intent": Intent.out_of_scope.value, "intent_source": "validation",
                "errors": [str(e)]}
    return {"question": q, "status": "running"}


@_timed("classify")
def classify_node(state: InvestigationState):
    cities, dates = known_cities_and_dates()
    context = context_from_history(state.get("history", []), cities)
    ents = extract_entities(state["question"], cities, context, default_date=dates[-1] if dates else None)
    intent, source, usage = classify_intent(state["question"], ents, context), "rules", []
    if intent is None:
        try:
            data, u = ollama.chat_json(LLM_INTENT_SYSTEM, state["question"], max_retries=1)
            usage.append({**u, "purpose": "intent"})
            intent, source = parse_llm_intent(data), "llm"
        except ollama.LLMUnavailable as e:
            log_event(log, "llm intent fallback unavailable", error=str(e)[:200])
        if intent is None:
            intent, source = Intent.clarification_needed, "rules"
        elif intent in (Intent.store_rca, Intent.hourly_drilldown) and not (ents.stores or context.get("store")):
            intent = Intent.clarification_needed
    ents = apply_context(intent, ents, context)
    if intent in (Intent.store_rca, Intent.hourly_drilldown) and not ents.stores:
        intent = Intent.clarification_needed
    if intent is Intent.hourly_drilldown and ents.hour_from is None:
        ents.hour_from, ents.hour_to, ents.hour_label = 0, 23, "all hours"
    needs_retrieval = intent is Intent.documentation or intent in RCA_INTENTS
    return {"entities": ents.model_dump(), "intent": intent.value, "intent_source": source,
            "needs_retrieval": needs_retrieval, "llm_usage": [*state.get("llm_usage", []), *usage]}


def _call(fn, *args, **kwargs):
    """One bounded retry for transient DB errors."""
    try:
        return fn(*args, **kwargs)
    except OperationalError:
        return fn(*args, **kwargs)


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=str))


@_timed("analytics")
def analytics_node(state: InvestigationState):
    intent, e = Intent(state["intent"]), Entities.model_validate(state["entities"])
    day = date.fromisoformat(e.date) if e.date else None
    out: dict = {}
    errors: list[dict] = []
    if day is None:
        return {"analytics": {}, "analytics_errors": [{"error": "No data loaded (no dates available)."}]}
    try:
        if intent in RCA_INTENTS:
            store = e.stores[0]
            out["rca"] = _call(service.store_day_rca, store, day).model_dump()
            out["hour_rows"] = _call(service.store_hours, store, day)["hours"]
            if intent is Intent.hourly_drilldown:
                out["hours"] = _call(service.store_hours, store, day, e.hour_from, e.hour_to)
        elif intent is Intent.store_comparison:
            out["comparison"] = _call(service.store_comparison, e.stores[:5], day)
        elif intent is Intent.city_summary:
            if not e.cities:
                errors.append({"error": "Which city? No city recognised in the question."})
            else:
                out["city_summary"] = _call(service.city_summary, e.cities[0], day)
        elif intent is Intent.city_comparison:
            out["city_comparison"] = _call(service.city_comparison, day, e.cities or None)
        elif intent is Intent.top_stores:
            out["top_stores"] = _call(service.top_breaching_stores, day, e.cities[0] if e.cities else None, 10)
        elif intent is Intent.sla_overview:
            out["overview"] = _call(service.sla_overview, day, e.cities[0] if e.cities else None)
            out["by_hour"] = _call(service.breach_by_hour, day, e.cities[0] if e.cities else None)
    except service.NotFoundError as err:
        errors.append({"error": str(err), "suggestions": err.suggestions})
    return {"analytics": _jsonable(out), "analytics_errors": errors}


def _retrieval_query(state: InvestigationState) -> tuple[str, list[str] | None, str | None]:
    intent = Intent(state["intent"])
    if intent is Intent.documentation:
        return state["question"], None, None
    rca = (state.get("analytics") or {}).get("rca")
    flags = sorted((rca or {}).get("factor_counts", {}).keys())
    base = "RCA playbook OR2A breach " + " ".join(evmod.FACTOR_KEYWORDS.get(f, f) for f in flags)
    incident = "incident " + " ".join(flags) if flags else None
    return base, ["playbook", "metric_definition", "sla", "sop"], incident


@_timed("retrieve")
def retrieve_node(state: InvestigationState):
    query, doc_types, incident_query = _retrieval_query(state)
    k = get_settings().rag_top_k
    res = retrieve(query, k=k, doc_types=doc_types)
    chunks = [c.model_dump() for c in res.chunks]
    warnings = [res.warning] if res.warning else []
    if incident_query:
        inc = retrieve(incident_query, k=2, doc_types=["incident"])
        chunks += [c.model_dump() for c in inc.chunks]
        if inc.warning and inc.warning not in warnings:
            warnings.append(inc.warning)
    return {"retrieval": {"query": query, "mode": res.mode, "chunks": chunks, "warnings": warnings}}


@_timed("correlate")
def correlate_node(state: InvestigationState):
    intent = Intent(state["intent"])
    ev = evmod.EvidenceBuilder()
    cites = evmod.citations_from_retrieval(state.get("retrieval"), ev)
    analytics = state.get("analytics") or {}
    metrics, factors = [], []
    if intent in RCA_INTENTS and "rca" in analytics:
        e = state["entities"]
        window = (e["hour_from"], e["hour_to"]) if intent is Intent.hourly_drilldown else None
        metrics, factors = evmod.rca_evidence(StoreDayRCA.model_validate(analytics["rca"]), ev, cites, window)
    elif "overview" in analytics:
        o = analytics["overview"]
        src = "store_hourly_metrics (op: sla_overview)"
        for name, unit in (("total_orders", "orders"), ("breach_rate", "ratio"), ("weighted_avg_or2a", "minutes"),
                           ("problem_hours", "store-hours")):
            metrics.append(evmod.ObservedMetric(name=name, value=o[name], unit=unit,
                                                evidence_id=ev.add("M", "metric", f"{name} = {o[name]}", src)))
    for key, op in (("city_summary", "city_summary"), ("city_comparison", "city_comparison"),
                    ("top_stores", "top_breaching_stores"), ("comparison", "store_comparison")):
        if key in analytics:
            rows = analytics[key]["stores"] if key == "city_summary" else analytics[key]
            ev.add("M", "metric", f"{len(rows)} row(s) from {op}", f"store_hourly_metrics (op: {op})",
                   entities=state["entities"])
    return {"evidence": [i.model_dump() for i in ev.items],
            "factors": [f.model_dump() for f in factors],
            "validation": {"metrics": [m.model_dump() for m in metrics],
                           "citations": [c.model_dump() for c in cites]}}


@_timed("validate_rca")
def validate_rca_node(state: InvestigationState):
    intent = Intent(state["intent"])
    analytics = state.get("analytics") or {}
    ev = evmod.EvidenceBuilder()
    ev.items = [evmod.EvidenceItem.model_validate(i) for i in state.get("evidence", [])]
    factors = [evmod.ContributingFactor.model_validate(f) for f in state.get("factors", [])]
    cites = [evmod.Citation.model_validate(c) for c in state["validation"]["citations"]]
    rca = StoreDayRCA.model_validate(analytics["rca"]) if "rca" in analytics else None
    checks = evmod.validate(intent, rca, analytics.get("hour_rows"), factors, cites, ev)
    return {"validation": {**state["validation"], "checks": [c.model_dump() for c in checks]}}


NARRATIVE_SYSTEM = (
    "You write a 2-3 sentence summary for a delivery operations manager. Use ONLY facts and numbers from "
    "the JSON evidence. Do not add numbers, causes or recommendations that are not in it. Say 'contributing "
    "factor', never 'caused by'. No markdown headings."
)


def _narrative(state: InvestigationState, report: InvestigationReport) -> tuple[str | None, bool | None, list, str]:
    if Intent(report.intent) not in ANALYTICS_INTENTS | {Intent.documentation} or report.status != "completed":
        return None, None, [], "not applicable"
    if not get_settings().llm_narrative_enabled:
        return None, None, [], "narrative disabled (LLM_NARRATIVE_ENABLED=false); deterministic report only"
    facts = {
        "scope": report.scope,
        "metrics": [m.model_dump() for m in report.observed_metrics],
        "factors": [{"factor": f.factor, "hours": f.hours, "share_of_breached_orders": f.share_of_breached_orders}
                    for f in report.contributing_factors],
        "evidence": [i.summary for i in report.evidence][:12],
    }
    evidence_text = json.dumps(facts, default=str) + " " + report.answer_markdown
    usage, feedback = [], ""
    for _ in range(2):  # bounded: one retry with feedback
        try:
            text, u = ollama.chat(NARRATIVE_SYSTEM, f"Question: {report.question}\nEvidence JSON: "
                                  f"{json.dumps(facts, default=str)[:3500]}{feedback}")
        except ollama.LLMUnavailable as e:
            return None, None, usage, f"local model unavailable: {e}"[:200]
        usage.append({**u, "purpose": "narrative"})
        ok, bad = evmod.grounded_numbers(text, evidence_text)
        if ok and "caused by" not in text.lower():
            return text.strip(), True, usage, "narrative passed number-grounding check"
        feedback = (f"\nYour previous answer used numbers not in the evidence ({', '.join(bad[:5])}) or causal "
                    "language. Use only the evidence numbers.")
    return None, False, usage, "narrative discarded: failed number-grounding check"


@_timed("report")
def report_node(state: InvestigationState):
    s = get_settings()
    intent = Intent(state.get("intent", Intent.out_of_scope.value))
    ents = state.get("entities") or {}
    analytics = state.get("analytics") or {}
    validation = state.get("validation") or {}
    errors = list(state.get("errors", []))
    a_errors = state.get("analytics_errors", [])
    cites = [evmod.Citation.model_validate(c) for c in validation.get("citations", [])]
    factors = [evmod.ContributingFactor.model_validate(f) for f in state.get("factors", [])]
    ev_items = [evmod.EvidenceItem.model_validate(i) for i in state.get("evidence", [])]
    status = state.get("status", "running")

    if status == "rejected":
        answer = errors[-1] if errors else "Question rejected."
    elif intent is Intent.out_of_scope:
        status, answer = "completed", ("I can help with quick-commerce operations: city and store performance, "
                                       "OR2A SLA breaches, root-cause analysis and the operations playbook.")
    elif intent is Intent.clarification_needed:
        status = "needs_clarification"
        answer = ("Please name a store (e.g. STORE_101) or a city (e.g. Bangalore), and optionally a date "
                  "and hours (e.g. 'morning hours').")
    elif a_errors:
        status = "completed"
        answer = "\n".join(f"⚠️ {x['error']}" + (f" — did you mean: {', '.join(x['suggestions'])}?"
                                                 if x.get("suggestions") else "") for x in a_errors)
    else:
        status = "completed" if not errors else "partial"
        answer = evmod.render_answer(intent, analytics, ents, cites, factors)

    rca = StoreDayRCA.model_validate(analytics["rca"]) if "rca" in analytics else None
    missing = list(rca.missing_data) if rca else []
    if intent is Intent.hourly_drilldown and rca and ents.get("hour_from") is not None:
        missing = [m for m in missing if not m.startswith("hour ")
                   or ents["hour_from"] <= int(m.split(":")[0].split()[1]) <= ents["hour_to"]]
    retrieval = state.get("retrieval") or {}
    limitations = list(retrieval.get("warnings", []))
    dq = [i for i in ev_items if i.kind == "data_quality"]
    if dq:
        limitations.append(f"{len(dq)} hour(s) where current_capacity_booked ≠ booked_size/current_size; "
                           "booking-gap flags follow the source column.")
    if missing:
        limitations.append(f"{len(missing)} missing data point(s) prevented some checks.")
    _, dates = known_cities_and_dates()
    single_date = len(dates) <= 1
    synthetic = False
    if ents.get("date") and intent in ANALYTICS_INTENTS:
        try:
            synthetic = service.is_synthetic_date(date.fromisoformat(ents["date"]))
        except ValueError:
            synthetic = False
    if synthetic:
        answer = (f"> ⚠️ SYNTHETIC DEMO DATA — {ents['date']} was generated from the real 2026-04-22 data "
                  "for demonstration. These are not real operational results.\n\n" + answer)
        limitations.insert(0, f"{ents['date']} is SYNTHETIC demo data generated by scripts/generate_synthetic_days.py, "
                              "not real operations data.")
    if intent in RCA_INTENTS:
        limitations.append("Store-hour aggregates only; order-level data and operational logs are not available.")
        if single_date:
            limitations.append("Only one real date is loaded: no baseline or recurrence analysis is possible.")
    if any(c.is_sample for c in cites):
        limitations.append("Some cited documents are SAMPLE documents created for demonstration, not company policy.")
    checks = [evmod.ValidationCheck.model_validate(c) for c in validation.get("checks", [])]
    if intent not in ANALYTICS_INTENTS and intent is not Intent.documentation or status != "completed":
        level = "n/a"
    elif any(not c.passed for c in checks):
        level = "low"
    elif intent in RCA_INTENTS:
        level = "low" if synthetic else "medium" if (missing or dq or single_date) else "high"
    else:
        level = "low" if synthetic else "high" if not limitations else "medium"

    report = InvestigationReport(
        investigation_id=state["investigation_id"], question=state["question"], intent=intent.value,
        intent_source=state.get("intent_source", "rules"), status=status,
        scope={k: ents.get(k) for k in ("stores", "cities", "date", "date_assumed", "hour_from", "hour_to",
                                        "hour_label", "used_context")} if ents else {},
        answer_markdown=answer,
        observed_metrics=[evmod.ObservedMetric.model_validate(m) for m in validation.get("metrics", [])],
        contributing_factors=factors, evidence=ev_items, citations=cites, missing_data=missing,
        confidence=Confidence(level=level, limitations=limitations),
        next_steps=evmod.next_steps(intent, factors, missing, len(dq), single_date) if intent in RCA_INTENTS else [],
        corrective_actions=evmod.corrective_actions(factors, cites),
        validation=checks, llm=LLMInfo(used=False, model=s.llm_model),
        data={k: v for k, v in analytics.items() if k != "rca"}, errors=errors + [x["error"] for x in a_errors],
    )
    narrative, grounded, usage, note = _narrative(state, report)
    all_usage = [*state.get("llm_usage", []), *usage]
    report.narrative = narrative
    report.llm = LLMInfo(used=bool(all_usage), model=s.llm_model, narrative_grounded=grounded, note=note,
                         usage=all_usage)
    timings = dict(state.get("timings", {}))
    report.timings_ms = timings
    return {"report": _jsonable(report.model_dump()), "status": status, "llm_usage": all_usage}


# ── graph ────────────────────────────────────────────────────────────────────

def route_after_validate(state: InvestigationState) -> str:
    return "report" if state.get("status") == "rejected" else "classify"


def route_after_classify(state: InvestigationState) -> str:
    intent = Intent(state["intent"])
    if intent in ANALYTICS_INTENTS:
        return "analytics"
    if intent is Intent.documentation:
        return "retrieve"
    return "report"


def route_after_analytics(state: InvestigationState) -> str:
    if state.get("analytics_errors"):
        return "report"
    return "retrieve" if state.get("needs_retrieval") else "correlate"


def build_graph(checkpointer=None):
    g = StateGraph(InvestigationState)
    g.add_node("validate", validate_node)
    g.add_node("classify", classify_node)
    g.add_node("analytics", analytics_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("correlate", correlate_node)
    g.add_node("validate_rca", validate_rca_node)
    g.add_node("report", report_node)
    g.add_edge(START, "validate")
    g.add_conditional_edges("validate", route_after_validate, ["classify", "report"])
    g.add_conditional_edges("classify", route_after_classify, ["analytics", "retrieve", "report"])
    g.add_conditional_edges("analytics", route_after_analytics, ["retrieve", "correlate", "report"])
    g.add_edge("retrieve", "correlate")
    g.add_edge("correlate", "validate_rca")
    g.add_edge("validate_rca", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)


_graph = None
_checkpointer = None


def get_checkpointer():
    """Postgres checkpointer when available, otherwise in-memory."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer
    s = get_settings()
    if s.checkpoint_enabled:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg import Connection
            from psycopg.rows import dict_row

            url = s.database_url.replace("postgresql+psycopg://", "postgresql://")
            conn = Connection.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
            saver = PostgresSaver(conn)
            saver.setup()
            _checkpointer = saver
            log_event(log, "using postgres checkpointer")
            return _checkpointer
        except Exception as e:  # noqa: BLE001
            log_event(log, "postgres checkpointer unavailable; using in-memory", error=f"{type(e).__name__}: {e}"[:200])
    _checkpointer = InMemorySaver()
    return _checkpointer


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph(get_checkpointer())
    return _graph


def run_investigation(question: str, history: list[dict] | None = None, thread_id: str | None = None,
                      investigation_id: str | None = None) -> dict:
    iid = investigation_id or str(uuid.uuid4())
    token = investigation_id_var.set(iid)
    start = time.perf_counter()
    try:
        state: InvestigationState = {"investigation_id": iid, "question": question, "history": history or [],
                                     "errors": [], "timings": {}, "llm_usage": []}
        config = {"configurable": {"thread_id": f"inv-{iid}"}, "recursion_limit": 20}
        result = get_graph().invoke(state, config)
        report = result["report"]
        report["timings_ms"]["total"] = round((time.perf_counter() - start) * 1000, 1)
        log_event(log, "investigation finished", intent=report["intent"], status=report["status"],
                  latency_ms=report["timings_ms"]["total"], llm_used=report["llm"]["used"])
        return report
    finally:
        investigation_id_var.reset(token)
