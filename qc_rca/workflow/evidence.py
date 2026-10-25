"""Evidence correlation, RCA validation and deterministic answer rendering.

Everything numeric in a report is produced here from analytics results.
Contributing factors are reported as rule-based co-occurrence with breached
orders; they are never described as proven causes.
"""
import re

from qc_rca.analytics.rca import (
    BOOKING_GAP_THRESHOLD,
    DEMAND_SPIKE_RATIO,
    UTILIZATION_GAP_THRESHOLD,
    StoreDayRCA,
    render_markdown,
)
from qc_rca.workflow.schemas import (
    Citation,
    ContributingFactor,
    CorrectiveAction,
    EvidenceItem,
    Intent,
    ObservedMetric,
    ValidationCheck,
)

FACTOR_KEYWORDS = {
    "demand spike": "demand spike forecast projection",
    "pileup": "pileup backlog carried orders",
    "sustained pileup": "sustained pileup escalation",
    "booking gap": "booking gap slot fill capacity booked",
    "utilization gap": "utilization gap man_hour no-show",
}

FACTOR_ACTIONS = {
    "demand spike": "Review the hourly order forecast for the affected hours and add buffer capacity where "
                    "actuals exceeded projection by more than 10%.",
    "pileup": "Clear backlog before the next hour starts: prioritise assignment of carried-over orders and "
              "check whether assignment was blocked (dispatch/system issues).",
    "sustained pileup": "Escalate sustained (3+ hour) pileups to the city operations lead during the shift "
                        "rather than after it.",
    "booking gap": "Increase slot fill for the affected slots (open more slots earlier, review incentives "
                   "for under-booked peak slots).",
    "utilization gap": "Follow up on no-shows and short shifts for the affected slots; confirm riders who "
                       "booked actually logged in.",
}


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1%}"


def _num(v: float | None, nd: int = 1) -> str:
    return "n/a" if v is None else f"{v:.{nd}f}"


class EvidenceBuilder:
    def __init__(self):
        self.items: list[EvidenceItem] = []
        self.counters = {"M": 0, "D": 0, "H": 0, "Q": 0}

    def add(self, prefix: str, kind: str, summary: str, source: str, **details) -> str:
        self.counters[prefix] += 1
        eid = f"{prefix}{self.counters[prefix]}"
        self.items.append(EvidenceItem(id=eid, kind=kind, summary=summary, source=source, details=details))
        return eid


def citations_from_retrieval(retrieval: dict | None, ev: EvidenceBuilder) -> list[Citation]:
    cites = []
    for ch in (retrieval or {}).get("chunks", []):
        prefix = "H" if ch["doc_type"] == "incident" else "D"
        label = " [SAMPLE DOCUMENT]" if ch["is_sample"] else ""
        cid = ev.add(prefix, "incident" if prefix == "H" else "document",
                     f"{ch['title']} — {ch['heading'] or 'intro'}{label}", ch["source_path"],
                     chunk_id=ch["chunk_id"], score=ch["score"], retrieval_mode=retrieval.get("mode"))
        excerpt = re.sub(r"\s+", " ", ch["content"])[:400]
        cites.append(Citation(id=cid, source_path=ch["source_path"], title=ch["title"], heading=ch["heading"],
                              doc_type=ch["doc_type"], is_sample=ch["is_sample"], excerpt=excerpt))
    return cites


def _cites_for(factor: str, cites: list[Citation]) -> list[str]:
    words = set(FACTOR_KEYWORDS.get(factor, factor).split())
    out = []
    for c in cites:
        text = (c.excerpt + " " + (c.heading or "")).lower()
        if any(w in text for w in words):
            out.append(c.id)
    return out[:3]


def rca_evidence(rca: StoreDayRCA, ev: EvidenceBuilder, cites: list[Citation], hour_window: tuple | None = None):
    s = rca.summary
    src = "store_hourly_metrics ⨝ rider_slots (op: store_day_rca)"
    metrics = [
        ObservedMetric(name="total_orders", value=s.total_orders, unit="orders",
                       evidence_id=ev.add("M", "metric", f"{rca.store} handled {s.total_orders} orders on {rca.date}",
                                          src, store=rca.store, date=rca.date)),
        ObservedMetric(name="weighted_breach_rate", value=s.weighted_breach_rate, unit="ratio",
                       evidence_id=ev.add("M", "metric",
                                          f"Weighted breach rate {_pct(s.weighted_breach_rate)} "
                                          f"({s.breached_count} of {s.total_orders} orders breached)", src)),
        ObservedMetric(name="weighted_avg_or2a", value=s.weighted_avg_or2a, unit="minutes",
                       evidence_id=ev.add("M", "metric", f"Order-weighted avg OR2A {_num(s.weighted_avg_or2a)} min",
                                          src, or2a_order_coverage=s.or2a_order_coverage)),
        ObservedMetric(name="problem_hours", value=s.problem_hours, unit="hours",
                       evidence_id=ev.add("M", "metric",
                                          f"{s.problem_hours} of {s.hours_observed} observed hours had breaches", src)),
    ]
    hours = rca.hours
    if hour_window:
        hours = [h for h in hours if hour_window[0] <= h.hour <= hour_window[1]]
    total_breached = sum(h.breached_count for h in rca.hours) or 0
    factor_hours: dict[str, list] = {}
    hour_ev: dict[int, str] = {}
    for h in hours:
        parts = []
        if h.demand.deviation_pct is not None:
            parts.append(f"orders {h.total_orders} vs projection {h.demand.order_projection:.0f} "
                         f"({h.demand.deviation_pct:+.1f}%)")
        parts.append(f"pileup_count {h.pileup.pileup_count}")
        parts.append(f"capacity booked {_pct(h.booking.current_capacity_booked)}")
        parts.append("man_hour n/a" if h.utilization.man_hour is None else f"man_hour {h.utilization.man_hour:.2f}")
        hour_ev[h.hour] = ev.add(
            "M", "metric",
            f"Hour {h.hour}: {h.breached_count}/{h.total_orders} breached, avg OR2A {_num(h.avg_or2a)} min; "
            + "; ".join(parts), src, hour=h.hour, flags=h.flags)
        if h.booking.data_quality_note:
            ev.add("Q", "data_quality", f"Hour {h.hour}: {h.booking.data_quality_note}", src, hour=h.hour)
        for flag in h.flags:
            key = "pileup" if flag.startswith("pileup") else flag
            factor_hours.setdefault(key, []).append(h)
            if h.pileup.sustained and key == "pileup":
                factor_hours.setdefault("sustained pileup", []).append(h)

    factors = []
    for name, hs in factor_hours.items():
        breached = sum(h.breached_count for h in hs)
        share = breached / total_breached if total_breached else None
        strength = ("present in hours holding most breached orders" if share and share >= 0.5 else
                    "present in some breached hours" if share and share >= 0.2 else
                    "present in a minority of breached hours")
        factors.append(ContributingFactor(
            factor=name, hours=[h.hour for h in hs], breached_orders_in_flagged_hours=breached,
            share_of_breached_orders=share, evidence_ids=[hour_ev[h.hour] for h in hs],
            citation_ids=_cites_for(name, cites),
            assessment=f"Rule triggered in {len(hs)} problem hour(s); {strength} "
                       f"({_pct(share)} of the store-day's breached orders). Co-occurrence, not proven cause.",
        ))
    factors.sort(key=lambda f: (-(f.share_of_breached_orders or 0), f.factor))
    return metrics, factors


def corrective_actions(factors: list[ContributingFactor], cites: list[Citation]) -> list[CorrectiveAction]:
    out = []
    for f in factors[:4]:
        cids = [c for c in f.citation_ids if c.startswith("D")]
        samples = {c.id for c in cites if c.is_sample}
        note = ("Rule-derived suggestion; cited procedure is a SAMPLE document, not company policy."
                if cids and all(c in samples for c in cids) else
                "Rule-derived suggestion based on the RCA playbook thresholds.")
        out.append(CorrectiveAction(action=FACTOR_ACTIONS.get(f.factor, f"Investigate {f.factor}."),
                                    addresses=f.factor, citation_ids=cids, source_note=note))
    return out


def next_steps(intent: Intent, factors: list[ContributingFactor], missing: list[str], dq_count: int,
               single_date: bool) -> list[str]:
    steps = []
    if factors:
        top = factors[0]
        steps.append(f"Pull order-level OR2A for hours {top.hours[:6]} to confirm whether '{top.factor}' "
                     f"coincides with the slowest assignments (aggregates can hide the distribution).")
    if any(f.factor in ("booking gap", "utilization gap") for f in factors):
        steps.append("Check rider login/attendance logs for the flagged slots to separate under-booking from no-shows.")
    if any(f.factor.endswith("pileup") for f in factors):
        steps.append("Review dispatch/assignment system logs for the hour preceding each pileup.")
    if not factors and intent in (Intent.store_rca, Intent.hourly_drilldown):
        steps.append("No playbook rule triggered; inspect order-level data and store incident logs for causes "
                     "outside demand, pileup and rider supply (e.g. system outages, packing delays).")
    if missing:
        steps.append("Backfill or explain the missing fields listed under Missing data before final sign-off.")
    if dq_count:
        steps.append("Reconcile current_capacity_booked with booked_size/current_size with the data owner.")
    if single_date:
        steps.append("Compare against a baseline of previous days; only one real date is loaded, so trends and "
                     "recurrence cannot be assessed.")
    return steps


def validate(intent: Intent, rca: StoreDayRCA | None, hour_rows: list[dict] | None,
             factors: list[ContributingFactor], cites: list[Citation], ev: EvidenceBuilder) -> list[ValidationCheck]:
    checks = []
    ids = {i.id for i in ev.items}
    checks.append(ValidationCheck(
        name="factors_have_metric_evidence",
        passed=all(f.evidence_ids and all(e in ids for e in f.evidence_ids) for f in factors),
        detail=f"{len(factors)} factor(s) checked"))
    checks.append(ValidationCheck(
        name="citations_resolve", passed=all(c in ids for f in factors for c in f.citation_ids),
        detail=f"{len(cites)} citation(s) retrieved"))
    if rca is not None and hour_rows is not None:
        total = sum(r["total_orders"] for r in hour_rows)
        checks.append(ValidationCheck(name="rollup_matches_hourly_rows", passed=total == rca.summary.total_orders,
                                      detail=f"sum(hourly total_orders)={total}, summary={rca.summary.total_orders}"))
        by_hour = {int(r["hour"]): r for r in hour_rows}
        mismatches = []
        for h in rca.hours:
            r = by_hour[h.hour]
            proj = r["order_projection"]
            expect = {
                "demand spike": bool(proj and r["total_orders"] > proj * DEMAND_SPIKE_RATIO),
                "booking gap": r["current_capacity_booked"] is not None
                and r["current_capacity_booked"] < BOOKING_GAP_THRESHOLD,
                "utilization gap": r["man_hour"] is not None and r["man_hour"] < UTILIZATION_GAP_THRESHOLD,
                "pileup": bool(r["pileup_flag"]),
            }
            got = {k: any(f.startswith(k) for f in h.flags) for k in expect}
            if got != expect:
                mismatches.append(h.hour)
        checks.append(ValidationCheck(name="flags_recomputed_from_raw_rows", passed=not mismatches,
                                      detail="all flags reproduced" if not mismatches else f"mismatch hours {mismatches}"))
    return checks


NUM_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?")


def grounded_numbers(text: str, evidence_text: str) -> tuple[bool, list[str]]:
    """Every number in ``text`` must appear in the evidence (to 1 dp / as a percentage)."""
    allowed = set()
    for n in NUM_RE.findall(evidence_text):
        v = abs(float(n))  # "2026-04-22" yields "-22"; dates are not negative numbers
        allowed.update({f"{v:.1f}", f"{round(v):d}", f"{v * 100:.1f}", f"{round(v * 100):d}"})
    bad = []
    for n in NUM_RE.findall(text):
        v = abs(float(n))
        if f"{v:.1f}" not in allowed and f"{round(v):d}" not in allowed:
            bad.append(n)
    return not bad, bad


# ── deterministic answer rendering ───────────────────────────────────────────

def _table(rows: list[dict], cols: list[tuple[str, str, callable]]) -> str:
    head = "| " + " | ".join(c[1] for c in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(c[2](r.get(c[0])) for c in cols) + " |" for r in rows]
    return "\n".join([head, sep, *body])


PLAIN_FACTOR = {
    "demand spike": "more orders than forecast (over 10% above plan)",
    "pileup": "unassigned orders piling up from the previous hour",
    "sustained pileup": "orders piling up for 3 or more hours in a row",
    "booking gap": "not enough riders booked (under 90% of slots filled)",
    "utilization gap": "booked riders not on the road enough (no-shows or short shifts)",
}
SHORT_FACTOR = {
    "demand spike": "more orders than forecast", "pileup": "orders piling up", "sustained pileup": "long pile-ups",
    "booking gap": "too few riders booked", "utilization gap": "rider no-shows / short shifts",
}
SMALL_SAMPLE = 50


def _status(rate: float | None) -> str:
    if rate is None:
        return "No data"
    return "Poor" if rate > 0.20 else "Watch" if rate > 0.05 else "Good"


def _orders(n) -> str:
    return f"{int(n):,}"


def _late(breached, total) -> str:
    return f"{_orders(breached)} of {_orders(total)} orders ({pct(breached / total if total else None)})"


pct, num = _pct, _num


def _store_rows(rows: list[dict], with_city: bool = False, rate_key: str = "breach_rate") -> str:
    cols = [("store", "Store", str)]
    if with_city:
        cols.append(("city", "City", str))
    cols += [(rate_key, "Status", _status), ("total_orders", "Orders", _orders),
             (rate_key, "Late orders", pct), ("weighted_avg_or2a", "Avg wait for a rider (min)", num),
             ("problem_hours", "Hours with late orders", str)]
    return _table(rows, cols)


def plain_summary(intent: Intent, analytics: dict, entities: dict, factors: list[ContributingFactor]) -> str:
    """One or two everyday-language sentences that answer the question before any table."""
    if intent in (Intent.store_rca, Intent.hourly_drilldown) and "rca" in analytics:
        rca = StoreDayRCA.model_validate(analytics["rca"])
        s = rca.summary
        if not s.total_orders:
            return f"{rca.store} had no orders on {rca.date}."
        text = (f"On {rca.date}, {rca.store} ({rca.city}) had {_late(s.breached_count, s.total_orders)} wait for a "
                f"rider longer than the target. On average an order waited {num(s.weighted_avg_or2a)} minutes "
                f"to get a rider, and {s.problem_hours} of {s.hours_observed} hours had late orders.")
        if factors:
            top = factors[0]
            text += (f" The most common warning sign was {PLAIN_FACTOR.get(top.factor, top.factor)}, seen in "
                     f"{len(top.hours)} of those hours.")
        elif rca.hours:
            text += " None of the standard warning signs (demand, pile-ups, rider supply) explain it."
        else:
            text += " Performance was within target."
        if s.total_orders < SMALL_SAMPLE:
            text += f" Note: with only {s.total_orders} orders, these numbers can swing a lot."
        return text
    if intent is Intent.store_comparison and "comparison" in analytics:
        rows = sorted(analytics["comparison"], key=lambda r: -(r["weighted_breach_rate"] or 0))
        parts = [f"{r['store']} had {pct(r['weighted_breach_rate'])} late orders out of {_orders(r['total_orders'])}"
                 for r in rows]
        text = "; ".join(parts) + "."
        if len(rows) >= 2 and (rows[0]["weighted_breach_rate"] or 0) > (rows[-1]["weighted_breach_rate"] or 0):
            text = f"{rows[0]['store']} did worse. " + text
        small = [r["store"] for r in rows if r["total_orders"] < SMALL_SAMPLE]
        if small:
            text += f" {', '.join(small)} had very few orders, so its numbers say little."
        return text
    if intent is Intent.city_summary and "city_summary" in analytics:
        cs = analytics["city_summary"]
        rows = cs["stores"]
        total = sum(r["total_orders"] for r in rows)
        breached = sum(r["breached_count"] for r in rows)
        bands = {b: sum(1 for r in rows if _status(r["breach_rate"]) == b) for b in ("Poor", "Watch", "Good")}
        worst = rows[0] if rows else None
        text = (f"{cs['city']} had {len(rows)} stores and {_orders(total)} orders on {cs['date']}; "
                f"{_late(breached, total)} were late getting a rider. "
                f"{bands['Poor']} stores were in poor shape (over 20% late), {bands['Watch']} need watching "
                f"(5–20% late) and {bands['Good']} were fine (5% or less).")
        if worst:
            text += f" The worst was {worst['store']}, with {pct(worst['breach_rate'])} of its orders late."
        return text
    if intent is Intent.city_comparison and "city_comparison" in analytics:
        rows = analytics["city_comparison"]
        if not rows:
            return "No city data for this date."
        best, worst = rows[-1], rows[0]
        return (f"{worst['city']} had the highest share of late orders ({pct(worst['breach_rate'])}) and "
                f"{best['city']} the lowest ({pct(best['breach_rate'])}).")
    if intent is Intent.top_stores and "top_stores" in analytics:
        rows = analytics["top_stores"]
        if not rows:
            return "No stores matched."
        return (f"These are the {len(rows)} stores with the highest share of late orders (stores with at least "
                f"50 orders). The worst, {rows[0]['store']} in {rows[0]['city']}, had {pct(rows[0]['breach_rate'])} "
                f"of its orders late.")
    if intent is Intent.sla_overview and "overview" in analytics:
        o = analytics["overview"]
        where = o.get("city") or "all cities"
        return (f"Across {where} on {o['date']}, {_late(o['breached_count'], o['total_orders'])} were late getting "
                f"a rider. On average an order waited {num(o['weighted_avg_or2a'])} minutes for a rider.")
    return ""


def render_answer(intent: Intent, analytics: dict, entities: dict, cites: list[Citation],
                  factors: list[ContributingFactor]) -> str:
    ident = str
    lines: list[str] = []
    summary = plain_summary(intent, analytics, entities, factors)
    if summary:
        lines.append(f"In short: {summary}\n")
    if entities.get("date_assumed") and intent is not Intent.documentation:
        lines.append(f"_Showing {entities.get('date')}, the latest real date, because no date was mentioned._\n")
    if intent in (Intent.store_rca, Intent.hourly_drilldown) and "rca" in analytics:
        rca = StoreDayRCA.model_validate(analytics["rca"])
        if factors:
            lines.append("### Warning signs found")
            for f in factors:
                lines.append(f"- {PLAIN_FACTOR.get(f.factor, f.factor).capitalize()} — in {len(f.hours)} hour(s) "
                             f"({', '.join(f'{h}:00' for h in f.hours[:8])}), covering "
                             f"{pct(f.share_of_breached_orders)} of the late orders. "
                             f"[{', '.join(f.evidence_ids[:3])}]")
            lines.append("\n_These are patterns that appeared alongside the delays, not proven causes._\n")
        if intent is Intent.hourly_drilldown and "hours" in analytics:
            h = analytics["hours"]
            lines.append(f"### Hour by hour — {h['hour_from']}:00 to {h['hour_to']}:59")
            lines.append(_table(h["hours"], [
                ("hour", "Hour", lambda v: f"{v}:00"), ("total_orders", "Orders", ident),
                ("breached_count", "Late", ident), ("breached_rate", "Late %", pct),
                ("avg_or2a", "Avg wait for a rider (min)", num), ("order_projection", "Forecast", lambda v: num(v, 0)),
                ("pileup_count", "Carried over", ident), ("current_capacity_booked", "Rider slots filled", pct),
                ("noshow_count", "No-shows", ident),
            ]))
            lines.append("")
            window = (h["hour_from"], h["hour_to"])
            rca = rca.model_copy(update={"hours": [x for x in rca.hours if window[0] <= x.hour <= window[1]]})
        lines.append("### Detailed check per problem hour (RCA playbook)")
        lines.append(render_markdown(rca).split("\n", 1)[1].strip())
    elif intent is Intent.store_comparison and "comparison" in analytics:
        lines.append(f"### Side by side — {entities.get('date')}")
        lines.append(_table(analytics["comparison"], [
            ("store", "Store", ident), ("city", "City", ident), ("weighted_breach_rate", "Status", _status),
            ("total_orders", "Orders", _orders), ("weighted_breach_rate", "Late orders", pct),
            ("weighted_avg_or2a", "Avg wait for a rider (min)", num), ("problem_hours", "Hours with late orders", ident),
            ("factor_counts", "Warning signs (hours)",
             lambda v: ", ".join(f"{SHORT_FACTOR.get(k, k)} ({n} h)" for k, n in (v or {}).items()) or "none"),
        ]))
    elif intent is Intent.city_summary and "city_summary" in analytics:
        cs = analytics["city_summary"]
        rows = cs["stores"]
        lines.append(f"### Worst stores in {cs['city']}")
        lines.append(_store_rows(rows[:10]))
        if len(rows) > 10:
            lines.append(f"\n_Showing the 10 worst of {len(rows)} stores. Use the SLA breaches page for the full list._")
    elif intent is Intent.city_comparison and "city_comparison" in analytics:
        lines.append(f"### Cities — {entities.get('date')}")
        lines.append(_table(analytics["city_comparison"], [
            ("city", "City", ident), ("breach_rate", "Status", _status), ("stores", "Stores", ident),
            ("total_orders", "Orders", _orders), ("breach_rate", "Late orders", pct),
            ("weighted_avg_or2a", "Avg wait for a rider (min)", num),
            ("pileup_hours", "Store-hours with pile-ups", ident),
        ]))
    elif intent is Intent.top_stores and "top_stores" in analytics:
        lines.append(f"### Stores with the most late orders — {entities.get('date')}")
        lines.append(_store_rows(analytics["top_stores"], with_city=True))
    elif intent is Intent.sla_overview and "overview" in analytics:
        o = analytics["overview"]
        lines.append("### Key numbers")
        lines += [f"- Stores: {o['stores']}", f"- Orders: {_orders(o['total_orders'])}",
                  f"- Late orders: {_orders(o['breached_count'])} ({pct(o['breach_rate'])})",
                  f"- Average wait for a rider: {num(o['weighted_avg_or2a'])} min",
                  f"- Store-hours with late orders: {o['problem_hours']} of {o['store_hours']}",
                  f"- Store-hours with pile-ups: {o['pileup_hours']}"]
    elif intent is Intent.documentation:
        if not cites:
            lines.append("I could not find this in the knowledge base.")
        else:
            lines.append("Here is what the documentation says:\n")
            for c in cites:
                tag = " _(sample document)_" if c.is_sample else ""
                lines.append(f"- {c.title} — {c.heading or 'overview'} [{c.id}]{tag}: {c.excerpt}")
    if cites and intent is not Intent.documentation:
        lines.append("\n### Sources")
        for c in cites:
            tag = " (sample document)" if c.is_sample else ""
            lines.append(f"- [{c.id}] {c.source_path} — {c.heading or c.title}{tag}")
    return "\n".join(lines).strip()
