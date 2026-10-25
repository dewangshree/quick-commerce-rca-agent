"""Question validation, entity extraction and intent classification.

Deterministic rules run first. The local LLM is consulted only when rules
cannot decide, and its answer is accepted only if it is a valid enum value
(see docs/EVALUATION.md for why: qwen2.5:1.5b's classification was tested
before being relied on).
"""
import re

from qc_rca.workflow.schemas import Entities, Intent

HOUR_WINDOWS = {
    "early morning": (5, 7), "morning": (6, 11), "afternoon": (12, 16), "evening": (17, 20),
    "night": (21, 23), "late night": (22, 23), "lunch": (12, 14), "dinner": (19, 22),
}
CITY_ALIASES = {
    "bengaluru": "bangalore", "blr": "bangalore", "mumbai": "mumbai north", "bombay": "mumbai north",
    "delhi": "new delhi", "pune": "pune city east", "hyderabad": "hyderabad city", "gurugram": "gurgaon",
}
STORE_RE = re.compile(r"\bstore[\s_\-#]*(\d{1,6})\b", re.I)
DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
HOUR_RANGE_RE = re.compile(r"\b(?:between|from)?\s*(\d{1,2})\s*(am|pm)?\s*(?:-|to|and)\s*(\d{1,2})\s*(am|pm)?\b", re.I)
SINGLE_HOUR_RE = re.compile(r"\b(?:hour\s+(\d{1,2})|(\d{1,2})\s*(am|pm))\b", re.I)
INJECTION_RE = re.compile(
    r"(ignore (all|previous|prior) instructions|system prompt|drop\s+table|delete\s+from|insert\s+into|"
    r"update\s+\w+\s+set|;\s*--|<script)", re.I)

DOC_WORDS = re.compile(r"\b(what is|what are|what does|what should|what to do|what can we do|how to|"
                       r"how (is|are|do|does|should|can)|steps|define|definition|meaning|explain|"
                       r"playbook|threshold|thresholds|sla definition|procedure|sop|policy|escalat\w*|"
                       r"past incident|historical|previous incident|incident report|documentation|docs?)\b", re.I)
RCA_WORDS = re.compile(r"\b(why|rca|root cause|underperform\w*|diagnos\w*|investigat\w*|what went wrong|"
                       r"cause|problem|issue|bad|poor\w*)\b", re.I)
HOURLY_WORDS = re.compile(r"\b(walk me through|hourly|hour by hour|hours|hour|timeline)\b", re.I)
COMPARE_WORDS = re.compile(r"\b(compare|comparison|versus|vs\.?|against|difference between)\b", re.I)
TOP_WORDS = re.compile(r"\b(worst|top|highest|most|best|lowest|rank\w*|which stores)\b", re.I)
OVERVIEW_WORDS = re.compile(r"\b(overview|overall|network|all cities|summary|kpis?|sla|breach(es)?)\b", re.I)
GREETING_RE = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay)\b[\s!.]*$", re.I)
FOLLOWUP_RE = re.compile(r"^\s*(what about|and|how about|now|same for|also)\b", re.I)


class QuestionRejected(ValueError):
    pass


def validate_question(question: str, max_chars: int) -> str:
    q = (question or "").strip()
    if not q:
        raise QuestionRejected("Question is empty.")
    if len(q) > max_chars:
        raise QuestionRejected(f"Question exceeds {max_chars} characters.")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in q):
        raise QuestionRejected("Question contains control characters.")
    if INJECTION_RE.search(q):
        raise QuestionRejected("Question contains instructions or SQL that this assistant does not execute. "
                               "Ask about store, city or SLA performance instead.")
    return q


def _to24(h: int, ampm: str | None) -> int:
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h < 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
    return h


def extract_entities(question: str, known_cities: list[str], context: dict | None = None,
                     default_date: str | None = None) -> Entities:
    context = context or {}
    q = question.lower()
    e = Entities()
    e.stores = list(dict.fromkeys(f"STORE_{int(m):03d}" for m in STORE_RE.findall(question)))

    norm_q = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", q)) + " "
    cities: list[str] = []
    for city in sorted(known_cities, key=len, reverse=True):
        if f" {city} " in norm_q and city not in cities:
            cities.append(city)
    for alias, target in CITY_ALIASES.items():
        if f" {alias} " in norm_q and target in known_cities and target not in cities:
            if not any(target.startswith(c) or c.startswith(target) for c in cities):
                cities.append(target)
    # bare first token, e.g. "Mumbai" -> "mumbai north" if unique
    for city in known_cities:
        first = city.split()[0]
        if city not in cities and f" {first} " in norm_q and sum(c.split()[0] == first for c in known_cities) == 1 \
                and not any(c.split()[0] == first for c in cities):
            cities.append(city)
    e.cities = cities

    if m := DATE_RE.search(question):
        e.date = m.group(0)
    elif context.get("date"):
        e.date, e.used_context = context["date"], e.used_context + ["date"]
    else:
        e.date, e.date_assumed = default_date, default_date is not None

    q_nodate = DATE_RE.sub(" ", question)
    for label in sorted(HOUR_WINDOWS, key=len, reverse=True):
        if re.search(rf"\b{label}\b", q):
            e.hour_from, e.hour_to = HOUR_WINDOWS[label]
            e.hour_label = label
            break
    if e.hour_from is None and (m := HOUR_RANGE_RE.search(q_nodate)):
        a, b = _to24(int(m.group(1)), m.group(2) or m.group(4)), _to24(int(m.group(3)), m.group(4))
        if 0 <= a <= 23 and 0 <= b <= 23 and a <= b and ("hour" in q or m.group(2) or m.group(4) or "between" in q):
            e.hour_from, e.hour_to, e.hour_label = a, b, f"{a}:00–{b}:59"
    if e.hour_from is None and (m := SINGLE_HOUR_RE.search(q_nodate)):
        h = int(m.group(1)) if m.group(1) else _to24(int(m.group(2)), m.group(3))
        if 0 <= h <= 23:
            e.hour_from = e.hour_to = h
            e.hour_label = f"hour {h}"
    return e


def classify_intent(question: str, e: Entities, context: dict | None = None) -> Intent | None:
    """Rule-based intent. Returns None when the rules cannot decide."""
    context = context or {}
    q = question.strip()
    if GREETING_RE.match(q):
        return Intent.out_of_scope
    doc = bool(DOC_WORDS.search(q))
    has_store, has_city = bool(e.stores), bool(e.cities)

    if COMPARE_WORDS.search(q):
        if len(e.stores) >= 2:
            return Intent.store_comparison
        if len(e.cities) >= 2 or re.search(r"\bcities\b", q, re.I):
            return Intent.city_comparison
        if re.search(r"\b(them|both|these)\b", q, re.I) and len(context.get("recent_stores", [])) >= 2:
            return Intent.store_comparison
    if doc and not (has_store and RCA_WORDS.search(q)):
        return Intent.documentation
    if has_store:
        if e.hour_from is not None or HOURLY_WORDS.search(q):
            return Intent.hourly_drilldown
        return Intent.store_rca
    if TOP_WORDS.search(q) and re.search(r"\bstores?\b", q, re.I):
        return Intent.top_stores
    if has_city:
        return Intent.city_summary
    if re.search(r"\bcities\b", q, re.I):
        return Intent.city_comparison
    # Follow-ups that rely on conversation context
    if context.get("store") and (e.hour_from is not None or HOURLY_WORDS.search(q)):
        return Intent.hourly_drilldown
    if context.get("store") and (RCA_WORDS.search(q) or FOLLOWUP_RE.match(q)):
        return Intent.store_rca
    if OVERVIEW_WORDS.search(q):
        return Intent.sla_overview
    return None


def apply_context(intent: Intent, e: Entities, context: dict) -> Entities:
    """Fill missing entities from the conversation (e.g. 'walk me through the morning hours')."""
    if intent in (Intent.store_rca, Intent.hourly_drilldown) and not e.stores and context.get("store"):
        e.stores = [context["store"]]
        e.used_context.append("store")
    if intent is Intent.store_comparison and len(e.stores) < 2 and len(context.get("recent_stores", [])) >= 2:
        e.stores = list(dict.fromkeys(e.stores + context["recent_stores"]))[:5]
        e.used_context.append("stores")
    if intent is Intent.city_summary and not e.cities and context.get("city"):
        e.cities = [context["city"]]
        e.used_context.append("city")
    return e


def context_from_history(history: list[dict], known_cities: list[str]) -> dict:
    """Most-recent-wins entity context from earlier user turns."""
    ctx: dict = {"recent_stores": []}
    for msg in history:
        if msg.get("role") != "user":
            continue
        e = extract_entities(msg.get("content", ""), known_cities)
        if e.stores:
            ctx["store"] = e.stores[-1]
            ctx["recent_stores"] = list(dict.fromkeys([*ctx["recent_stores"], *e.stores]))[-5:]
        if e.cities:
            ctx["city"] = e.cities[-1]
        if DATE_RE.search(msg.get("content", "")):
            ctx["date"] = e.date
    return ctx


LLM_INTENT_SYSTEM = (
    "You classify questions for a quick-commerce delivery operations assistant. "
    "Reply with JSON only: {\"intent\": <one of: " + ", ".join(i.value for i in Intent) + ">}. "
    "store_rca = why a store underperformed; hourly_drilldown = hour-by-hour store detail; "
    "city_summary = how a city did; city_comparison = compare cities; store_comparison = compare stores; "
    "sla_overview = overall SLA/breach numbers; top_stores = worst/best stores ranking; "
    "documentation = definitions, playbooks, procedures, past incidents; out_of_scope = unrelated."
)


def parse_llm_intent(data: dict) -> Intent | None:
    value = str(data.get("intent", "")).strip().lower()
    try:
        return Intent(value)
    except ValueError:
        return None
