"""Deterministic RCA rules (knowledge_base/reference/quick_commerce_rca_logic.md).

Pure functions over store-hour metrics: no database, no LLM. The LangGraph
workflow, the API and the MCP server all call this module, so the numbers in
any answer come from here, not from a model.

Differences from the original tools.run_rca (deliberate bug fixes, covered by
tests/test_rca_regression.py):
* Missing ``man_hour`` (booked minutes = 0) was coerced to 0 and reported as a
  UTILIZATION GAP. It is now UNKNOWN and listed as missing data.
* Missing ``avg_or2a`` crashed the store-day roll-up (TypeError). Those hours
  are now excluded from the weighted average and listed as missing data.
* Sustained pileup now requires clock-consecutive hours (row adjacency was used
  before; on the shipped dataset the results are identical).
"""
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

DEMAND_SPIKE_RATIO = 1.10
SUSTAINED_PILEUP_HOURS = 3
BOOKING_GAP_THRESHOLD = 0.90
UTILIZATION_GAP_THRESHOLD = 0.85
CAPACITY_RATIO_TOLERANCE = 0.011


@dataclass(frozen=True)
class HourInput:
    hour: int
    total_orders: int
    breached_count: int
    breached_rate: float
    is_problem_hour: bool
    avg_or2a: float | None
    order_projection: float | None
    pileup_flag: bool
    pileup_count: int
    current_capacity_booked: float | None
    man_hour: float | None
    noshow_count: int
    current_size: int
    booked_size: int
    slot_name: str | None = None
    slot_type: str | None = None


class CheckStatus(str, Enum):
    yes = "YES"
    no = "NO"
    unknown = "UNKNOWN"


class DemandCheck(BaseModel):
    status: CheckStatus
    total_orders: int
    order_projection: float | None
    deviation_pct: float | None


class PileupCheck(BaseModel):
    status: CheckStatus
    pileup_count: int
    consecutive_hours: int
    sustained: bool


class BookingCheck(BaseModel):
    status: CheckStatus
    booked_size: int
    current_size: int
    current_capacity_booked: float | None
    recomputed_ratio: float | None
    data_quality_note: str | None = None


class UtilizationCheck(BaseModel):
    status: CheckStatus
    man_hour: float | None
    noshow_count: int


class HourFinding(BaseModel):
    hour: int
    avg_or2a: float | None
    total_orders: int
    breached_count: int
    breached_rate: float
    slot_name: str | None
    slot_type: str | None
    demand: DemandCheck
    pileup: PileupCheck
    booking: BookingCheck
    utilization: UtilizationCheck
    flags: list[str]
    missing_data: list[str]


class StoreDaySummary(BaseModel):
    total_orders: int
    breached_count: int
    weighted_breach_rate: float | None
    weighted_avg_or2a: float | None
    or2a_order_coverage: float | None  # share of orders whose hour has an avg_or2a value
    problem_hours: int
    hours_observed: int


class StoreDayRCA(BaseModel):
    store: str
    city: str | None
    date: str
    summary: StoreDaySummary
    hours: list[HourFinding]
    factor_counts: dict[str, int]
    missing_data: list[str]
    thresholds: dict[str, float]


def weighted_rollup(hours: Sequence[HourInput]) -> StoreDaySummary:
    total = sum(h.total_orders for h in hours)
    breached = sum(h.breached_count for h in hours)
    with_or2a = [h for h in hours if h.avg_or2a is not None]
    or2a_orders = sum(h.total_orders for h in with_or2a)
    return StoreDaySummary(
        total_orders=total,
        breached_count=breached,
        weighted_breach_rate=breached / total if total else None,
        weighted_avg_or2a=(sum(h.avg_or2a * h.total_orders for h in with_or2a) / or2a_orders) if or2a_orders else None,
        or2a_order_coverage=(or2a_orders / total) if total else None,
        problem_hours=sum(1 for h in hours if is_problem(h)),
        hours_observed=len(hours),
    )


def is_problem(h: HourInput) -> bool:
    return bool(h.is_problem_hour) or (h.breached_rate or 0) > 0


def check_demand(h: HourInput) -> DemandCheck:
    if h.order_projection is None or h.order_projection <= 0:
        return DemandCheck(status=CheckStatus.unknown, total_orders=h.total_orders, order_projection=h.order_projection,
                           deviation_pct=None)
    spike = h.total_orders > h.order_projection * DEMAND_SPIKE_RATIO
    return DemandCheck(
        status=CheckStatus.yes if spike else CheckStatus.no,
        total_orders=h.total_orders,
        order_projection=h.order_projection,
        deviation_pct=(h.total_orders - h.order_projection) / h.order_projection * 100,
    )


def check_pileup(hours: Sequence[HourInput], idx: int) -> PileupCheck:
    h = hours[idx]
    consec, i = 1, idx - 1
    while i >= 0 and hours[i].pileup_flag and hours[i + 1].hour - hours[i].hour == 1:
        consec += 1
        i -= 1
    return PileupCheck(
        status=CheckStatus.yes if h.pileup_flag else CheckStatus.no,
        pileup_count=h.pileup_count,
        consecutive_hours=consec if h.pileup_flag else 0,
        sustained=bool(h.pileup_flag) and consec >= SUSTAINED_PILEUP_HOURS,
    )


def check_booking(h: HourInput) -> BookingCheck:
    recomputed = h.booked_size / h.current_size if h.current_size else None
    note = None
    if h.current_capacity_booked is not None and recomputed is not None and \
            abs(recomputed - h.current_capacity_booked) > CAPACITY_RATIO_TOLERANCE:
        note = (f"current_capacity_booked={h.current_capacity_booked:.2f} does not equal "
                f"booked_size/current_size={recomputed:.2f}; rule uses the source column")
    if h.current_capacity_booked is None:
        status = CheckStatus.unknown
    else:
        status = CheckStatus.yes if h.current_capacity_booked < BOOKING_GAP_THRESHOLD else CheckStatus.no
    return BookingCheck(status=status, booked_size=h.booked_size, current_size=h.current_size,
                        current_capacity_booked=h.current_capacity_booked, recomputed_ratio=recomputed,
                        data_quality_note=note)


def check_utilization(h: HourInput) -> UtilizationCheck:
    if h.man_hour is None:
        return UtilizationCheck(status=CheckStatus.unknown, man_hour=None, noshow_count=h.noshow_count)
    gap = h.man_hour < UTILIZATION_GAP_THRESHOLD
    return UtilizationCheck(status=CheckStatus.yes if gap else CheckStatus.no, man_hour=h.man_hour,
                            noshow_count=h.noshow_count)


def analyze_hour(hours: Sequence[HourInput], idx: int) -> HourFinding:
    h = hours[idx]
    demand, pileup = check_demand(h), check_pileup(hours, idx)
    booking, util = check_booking(h), check_utilization(h)
    flags, missing = [], []
    if demand.status is CheckStatus.yes:
        flags.append("demand spike")
    if pileup.status is CheckStatus.yes:
        flags.append("pileup (sustained)" if pileup.sustained else "pileup")
    if booking.status is CheckStatus.yes:
        flags.append("booking gap")
    if util.status is CheckStatus.yes:
        flags.append("utilization gap")
    if h.avg_or2a is None:
        missing.append(f"hour {h.hour}: avg_or2a not available")
    if demand.status is CheckStatus.unknown:
        missing.append(f"hour {h.hour}: order_projection not available, demand check not evaluated")
    if booking.status is CheckStatus.unknown:
        missing.append(f"hour {h.hour}: current_capacity_booked not available")
    if util.status is CheckStatus.unknown:
        missing.append(f"hour {h.hour}: man_hour not available (booked hours = 0), utilization check not evaluated")
    return HourFinding(
        hour=h.hour, avg_or2a=h.avg_or2a, total_orders=h.total_orders, breached_count=h.breached_count,
        breached_rate=h.breached_rate, slot_name=h.slot_name, slot_type=h.slot_type,
        demand=demand, pileup=pileup, booking=booking, utilization=util, flags=flags, missing_data=missing,
    )


def analyze_store_day(store: str, date: str, hours: Sequence[HourInput], city: str | None = None) -> StoreDayRCA:
    hours = sorted(hours, key=lambda h: h.hour)
    findings = [analyze_hour(hours, i) for i, h in enumerate(hours) if is_problem(h)]
    counts: dict[str, int] = {}
    for f in findings:
        for flag in f.flags:
            key = "pileup" if flag.startswith("pileup") else flag
            counts[key] = counts.get(key, 0) + 1
    sustained = sum(1 for f in findings if f.pileup.sustained)
    if sustained:
        counts["sustained pileup"] = sustained
    missing = [m for f in findings for m in f.missing_data]
    no_or2a = [h.hour for h in hours if h.avg_or2a is None]
    if no_or2a:
        missing.append(f"store-day: avg_or2a missing for hour(s) {no_or2a}; excluded from weighted avg OR2A")
    return StoreDayRCA(
        store=store, city=city, date=date, summary=weighted_rollup(hours), hours=findings,
        factor_counts=counts, missing_data=missing,
        thresholds={
            "demand_spike_ratio": DEMAND_SPIKE_RATIO,
            "sustained_pileup_hours": SUSTAINED_PILEUP_HOURS,
            "booking_gap_threshold": BOOKING_GAP_THRESHOLD,
            "utilization_gap_threshold": UTILIZATION_GAP_THRESHOLD,
        },
    )


def _fmt_or2a(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f}"


def render_markdown(rca: StoreDayRCA) -> str:
    """Render in the playbook output format (same layout as the original tool)."""
    s = rca.summary
    lines = [
        f"## RCA Report — {rca.store} — {rca.date}",
        "",
        "**Store-Day Summary**",
        f"- Total orders: {s.total_orders}",
        f"- Weighted breach rate: {s.weighted_breach_rate:.1%}" if s.weighted_breach_rate is not None
        else "- Weighted breach rate: n/a",
        f"- Weighted avg OR2A: {_fmt_or2a(s.weighted_avg_or2a)} min",
        f"- Problem hours: {s.problem_hours}",
        "",
    ]
    if not rca.hours:
        lines.append("✅ No problem hours detected. Store performance was within SLA.")
        return "\n".join(lines)
    for f in rca.hours:
        d, p, b, u = f.demand, f.pileup, f.booking, f.utilization
        if d.status is CheckStatus.unknown:
            demand_str = f"UNKNOWN — projection not available ({d.total_orders} orders)"
        else:
            demand_str = (f"{d.status.value} — {d.total_orders} orders vs {d.order_projection:.0f} projected "
                          f"({d.deviation_pct:+.1f}%)")
        pileup_str = f"{p.status.value} — {p.pileup_count} orders carried from previous hour"
        if p.sustained:
            pileup_str += " [SUSTAINED PILEUP — 3+ consecutive hours]"
        ratio = "n/a" if b.current_capacity_booked is None else f"{b.current_capacity_booked:.1%}"
        booking_str = f"{b.booked_size} of {b.current_size} slots booked ({ratio})"
        if u.status is CheckStatus.unknown:
            util_str = f"man_hour ratio not available — booked hours = 0 ({u.noshow_count} no-shows)"
        else:
            util_str = f"man_hour ratio {u.man_hour:.2f} ({u.noshow_count} no-shows)"
        summary = ", ".join(f.flags) if f.flags else "no clear root cause identified"
        lines += [
            f"### {rca.store} — Hour {f.hour} — avg OR2A: {_fmt_or2a(f.avg_or2a)} min",
            "",
            f"1. Demand Spike: {demand_str}",
            f"2. Pileup: {pileup_str}",
            "3. Supply:",
            f"   a. Booking: {booking_str}",
            f"   b. Utilization: {util_str}",
            "",
            f"**Summary**: OR2A was {_fmt_or2a(f.avg_or2a)} min due to {summary}.",
            "",
        ]
    return "\n".join(lines)
