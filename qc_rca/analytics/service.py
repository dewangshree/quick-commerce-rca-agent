"""Allowlisted analytics operations.

There is no free-form SQL anywhere in the platform. Each public function here
is one allowed operation, built with SQLAlchemy Core (bound parameters only)
and executed on the read-only engine. ``OPERATIONS`` is the allowlist used by
the workflow and the MCP server.
"""
import difflib
import re
from datetime import date

from sqlalchemy import Engine, case, distinct, func, select

from qc_rca.analytics.rca import HourInput, StoreDayRCA, analyze_store_day, is_problem
from qc_rca.db.models import City, RiderSlot, Store, StoreHourlyMetric as M
from qc_rca.db.session import get_readonly_engine
from qc_rca.ingestion import normalize_city

STORE_CODE_RE = re.compile(r"^STORE_\d{1,6}$")


class NotFoundError(LookupError):
    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []


def _engine(engine: Engine | None) -> Engine:
    return engine or get_readonly_engine()


def _weighted_or2a():
    # Hours without avg_or2a are excluded from numerator and denominator.
    num = func.sum(M.avg_or2a * M.total_orders)
    den = func.sum(case((M.avg_or2a.is_not(None), M.total_orders), else_=0))
    return num / func.nullif(den, 0)


def _breach_rate():
    return func.sum(M.breached_count) * 1.0 / func.nullif(func.sum(M.total_orders), 0)


def _problem_hour():
    return func.sum(case(((M.is_problem_hour.is_(True)) | (M.breached_rate > 0), 1), else_=0))


# ── lookups ──────────────────────────────────────────────────────────────────

def list_cities(engine: Engine | None = None) -> list[dict]:
    q = (select(City.name, City.normalized_name, func.count(Store.id).label("stores"))
         .join(Store, Store.city_id == City.id).group_by(City.id).order_by(City.name))
    with _engine(engine).connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def list_stores(city: str | None = None, engine: Engine | None = None) -> list[dict]:
    q = select(Store.code, City.name.label("city")).join(City).order_by(Store.code)
    if city:
        q = q.where(City.normalized_name == resolve_city(city, engine))
    with _engine(engine).connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def available_dates(engine: Engine | None = None) -> list[str]:
    with _engine(engine).connect() as c:
        return [d.isoformat() for d in c.scalars(select(M.charge_date).distinct().order_by(M.charge_date))]


def dataset_dates(engine: Engine | None = None) -> list[dict]:
    """Loaded dates with a flag for generated (synthetic) demo data."""
    q = (select(M.charge_date, func.bool_or(M.is_synthetic).label("synthetic"))
         .group_by(M.charge_date).order_by(M.charge_date))
    with _engine(engine).connect() as c:
        return [{"date": d.isoformat(), "synthetic": bool(s)} for d, s in c.execute(q)]


def is_synthetic_date(day: date, engine: Engine | None = None) -> bool:
    with _engine(engine).connect() as c:
        return bool(c.scalar(select(func.bool_or(M.is_synthetic)).where(M.charge_date == day)))


def real_dates(engine: Engine | None = None) -> list[str]:
    return [d["date"] for d in dataset_dates(engine) if not d["synthetic"]]


def daily_trend(city: str | None = None, store: str | None = None, engine: Engine | None = None) -> list[dict]:
    q = (
        select(
            M.charge_date.label("date"),
            func.bool_or(M.is_synthetic).label("synthetic"),
            func.sum(M.total_orders).label("total_orders"),
            func.sum(M.breached_count).label("breached_count"),
            _breach_rate().label("breach_rate"),
            _weighted_or2a().label("weighted_avg_or2a"),
            _problem_hour().label("problem_hours"),
        )
        .join(Store, Store.id == M.store_id).join(City, City.id == Store.city_id)
        .group_by(M.charge_date).order_by(M.charge_date)
    )
    if city:
        q = q.where(City.normalized_name == resolve_city(city, engine))
    if store:
        q = q.where(Store.code == resolve_store(store, engine)[0])
    with _engine(engine).connect() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
    for r in rows:
        r["date"] = r["date"].isoformat()
    return rows


def resolve_city(city: str, engine: Engine | None = None) -> str:
    """Return the normalized city name or raise NotFoundError with suggestions."""
    norm = normalize_city(city)
    with _engine(engine).connect() as c:
        names = list(c.scalars(select(City.normalized_name)))
    if norm in names:
        return norm
    prefix = [n for n in names if n.startswith(norm) or norm.startswith(n)]
    if len(prefix) == 1:
        return prefix[0]
    raise NotFoundError(f"Unknown city '{city}'", difflib.get_close_matches(norm, names, n=3, cutoff=0.4) or prefix)


def resolve_store(store: str, engine: Engine | None = None) -> tuple[str, str]:
    code = store.strip().upper()
    if re.fullmatch(r"\d{1,6}", code):
        code = f"STORE_{int(code):03d}"
    with _engine(engine).connect() as c:
        row = c.execute(select(Store.code, City.name).join(City).where(Store.code == code)).first()
        if row:
            return row[0], row[1]
        codes = list(c.scalars(select(Store.code)))
    raise NotFoundError(f"Unknown store '{store}'", difflib.get_close_matches(code, codes, n=3, cutoff=0.6))


# ── store level ──────────────────────────────────────────────────────────────

def _hour_rows(store_code: str, day: date, engine: Engine | None, hour_from: int = 0, hour_to: int = 23):
    q = (
        select(
            M.hour, M.total_orders, M.breached_count, M.breached_rate, M.is_problem_hour, M.avg_or2a, M.o2a,
            M.order_projection, M.pileup_flag, M.pileup_count, M.completed_order_count, M.cancelled_order_count,
            RiderSlot.slot_name, RiderSlot.slot_type, RiderSlot.current_capacity_booked, RiderSlot.man_hour,
            RiderSlot.noshow_count, RiderSlot.current_size, RiderSlot.booked_size, RiderSlot.original_size,
        )
        .join(Store, Store.id == M.store_id)
        .join(RiderSlot, RiderSlot.id == M.slot_id)
        .where(Store.code == store_code, M.charge_date == day, M.hour.between(hour_from, hour_to))
        .order_by(M.hour)
    )
    with _engine(engine).connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def store_hours(store: str, day: date, hour_from: int = 0, hour_to: int = 23,
                engine: Engine | None = None) -> dict:
    code, city = resolve_store(store, engine)
    rows = _hour_rows(code, day, engine, hour_from, hour_to)
    for r in rows:
        r["problem_hour"] = is_problem(_to_input(r))
    return {"store": code, "city": city, "date": day.isoformat(), "hour_from": hour_from, "hour_to": hour_to,
            "hours": rows}


def _to_input(r: dict) -> HourInput:
    return HourInput(
        hour=int(r["hour"]), total_orders=r["total_orders"], breached_count=r["breached_count"],
        breached_rate=r["breached_rate"], is_problem_hour=r["is_problem_hour"], avg_or2a=r["avg_or2a"],
        order_projection=r["order_projection"], pileup_flag=r["pileup_flag"], pileup_count=r["pileup_count"],
        current_capacity_booked=r["current_capacity_booked"], man_hour=r["man_hour"],
        noshow_count=r["noshow_count"], current_size=r["current_size"], booked_size=r["booked_size"],
        slot_name=r["slot_name"], slot_type=r["slot_type"],
    )


def store_day_rca(store: str, day: date, engine: Engine | None = None) -> StoreDayRCA:
    code, city = resolve_store(store, engine)
    rows = _hour_rows(code, day, engine)
    if not rows:
        raise NotFoundError(f"No data for {code} on {day.isoformat()}", available_dates(engine))
    return analyze_store_day(code, day.isoformat(), [_to_input(r) for r in rows], city=city)


def store_comparison(stores: list[str], day: date, engine: Engine | None = None) -> list[dict]:
    codes = [resolve_store(s, engine)[0] for s in stores]
    out = []
    for code in codes:
        rca = store_day_rca(code, day, engine)
        out.append({"store": code, "city": rca.city, **rca.summary.model_dump(), "factor_counts": rca.factor_counts})
    return out


# ── city / network level ─────────────────────────────────────────────────────

def city_summary(city: str, day: date, engine: Engine | None = None) -> dict:
    """Per-store roll-up for one city (replaces the original get_city_summary)."""
    norm = resolve_city(city, engine)
    q = (
        select(
            Store.code.label("store"),
            func.sum(M.total_orders).label("total_orders"),
            func.sum(M.breached_count).label("breached_count"),
            _breach_rate().label("breach_rate"),
            _weighted_or2a().label("weighted_avg_or2a"),
            _problem_hour().label("problem_hours"),
            func.count().label("hours_observed"),
        )
        .join(Store, Store.id == M.store_id).join(City, City.id == Store.city_id)
        .where(City.normalized_name == norm, M.charge_date == day)
        .group_by(Store.code)
        .order_by(_breach_rate().desc().nulls_last(), Store.code)
    )
    with _engine(engine).connect() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
        name = c.scalar(select(City.name).where(City.normalized_name == norm))
    return {"city": name, "date": day.isoformat(), "stores": rows}


def city_comparison(day: date, cities: list[str] | None = None, engine: Engine | None = None) -> list[dict]:
    q = (
        select(
            City.name.label("city"),
            func.count(distinct(M.store_id)).label("stores"),
            func.sum(M.total_orders).label("total_orders"),
            func.sum(M.breached_count).label("breached_count"),
            _breach_rate().label("breach_rate"),
            _weighted_or2a().label("weighted_avg_or2a"),
            _problem_hour().label("problem_hours"),
            func.sum(case((M.pileup_flag.is_(True), 1), else_=0)).label("pileup_hours"),
        )
        .join(Store, Store.id == M.store_id).join(City, City.id == Store.city_id)
        .where(M.charge_date == day)
        .group_by(City.name)
        .order_by(_breach_rate().desc().nulls_last())
    )
    if cities:
        q = q.where(City.normalized_name.in_([resolve_city(c, engine) for c in cities]))
    with _engine(engine).connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def sla_overview(day: date, city: str | None = None, engine: Engine | None = None) -> dict:
    base = (select(
        func.count(distinct(M.store_id)).label("stores"),
        func.sum(M.total_orders).label("total_orders"),
        func.sum(M.breached_count).label("breached_count"),
        _breach_rate().label("breach_rate"),
        _weighted_or2a().label("weighted_avg_or2a"),
        _problem_hour().label("problem_hours"),
        func.count().label("store_hours"),
        func.sum(case((M.pileup_flag.is_(True), 1), else_=0)).label("pileup_hours"),
        func.sum(M.cancelled_order_count).label("cancelled_orders"),
    ).join(Store, Store.id == M.store_id).join(City, City.id == Store.city_id).where(M.charge_date == day))
    if city:
        base = base.where(City.normalized_name == resolve_city(city, engine))
    with _engine(engine).connect() as c:
        row = dict(c.execute(base).one()._mapping)
    row.update({"date": day.isoformat(), "city": city})
    return row


def breach_by_hour(day: date, city: str | None = None, store: str | None = None,
                   engine: Engine | None = None) -> list[dict]:
    q = (
        select(
            M.hour,
            func.sum(M.total_orders).label("total_orders"),
            func.sum(M.breached_count).label("breached_count"),
            _breach_rate().label("breach_rate"),
            _weighted_or2a().label("weighted_avg_or2a"),
            func.sum(case((M.pileup_flag.is_(True), 1), else_=0)).label("pileup_stores"),
            func.sum(M.order_projection).label("order_projection"),
        )
        .join(Store, Store.id == M.store_id).join(City, City.id == Store.city_id)
        .where(M.charge_date == day).group_by(M.hour).order_by(M.hour)
    )
    if city:
        q = q.where(City.normalized_name == resolve_city(city, engine))
    if store:
        q = q.where(Store.code == resolve_store(store, engine)[0])
    with _engine(engine).connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def top_breaching_stores(day: date, city: str | None = None, limit: int = 10, min_orders: int = 50,
                         engine: Engine | None = None) -> list[dict]:
    q = (
        select(
            Store.code.label("store"), City.name.label("city"),
            func.sum(M.total_orders).label("total_orders"),
            func.sum(M.breached_count).label("breached_count"),
            _breach_rate().label("breach_rate"),
            _weighted_or2a().label("weighted_avg_or2a"),
            _problem_hour().label("problem_hours"),
        )
        .join(Store, Store.id == M.store_id).join(City, City.id == Store.city_id)
        .where(M.charge_date == day)
        .group_by(Store.code, City.name)
        .having(func.sum(M.total_orders) >= min_orders)
        .order_by(_breach_rate().desc().nulls_last(), Store.code)
        .limit(max(1, min(limit, 100)))
    )
    if city:
        q = q.where(City.normalized_name == resolve_city(city, engine))
    with _engine(engine).connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


OPERATIONS = {
    "list_cities": list_cities,
    "list_stores": list_stores,
    "available_dates": available_dates,
    "store_hours": store_hours,
    "store_day_rca": store_day_rca,
    "store_comparison": store_comparison,
    "city_summary": city_summary,
    "city_comparison": city_comparison,
    "sla_overview": sla_overview,
    "breach_by_hour": breach_by_hour,
    "daily_trend": daily_trend,
    "dataset_dates": dataset_dates,
    "top_breaching_stores": top_breaching_stores,
}
