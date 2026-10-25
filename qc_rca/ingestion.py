"""Idempotent CSV ingestion of the quick-commerce gold table into PostgreSQL.

* Every row is validated with Pydantic before anything is written.
* The whole file is loaded in one transaction; any error rolls everything back.
* Upserts (``ON CONFLICT DO UPDATE``) on natural keys make re-runs safe.
* A file whose SHA-256 was already ingested successfully is skipped unless
  ``force=True``.
"""
import csv
import hashlib
import re
from datetime import date, datetime, time, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from qc_rca.db.models import City, IngestionRun, RiderSlot, Store, StoreHourlyMetric
from qc_rca.observability import get_logger, log_event

log = get_logger("ingestion")


def normalize_city(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def _blank_to_none(v):
    return None if isinstance(v, str) and v.strip() == "" else v


class GoldCsvRow(BaseModel):
    """One store-hour row of quick_commerce_orders_gold."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    charge_date: date
    store: str = Field(min_length=1, max_length=64)
    city: str = Field(min_length=1, max_length=120)
    hour: float = Field(ge=0, le=23)
    hour_start: datetime
    hour_end: datetime
    total_orders: int = Field(ge=0)
    breached_count: int = Field(ge=0)
    breached_rate: float = Field(ge=0, le=1)
    is_problem_hour: int = Field(ge=0, le=1)
    pileup_count: int = Field(ge=0)
    pileup_flag: int = Field(ge=0, le=1)
    avg_or2a: float | None = None
    o2a: float | None = None
    completed_order_count: int = Field(ge=0)
    cancelled_order_count: int = Field(ge=0)
    rto_order_count: int = Field(ge=0)
    order_projection: float | None = None
    slot_name: str
    slot_start: time
    slot_end: time
    slot_type: str
    orginal_size: int = Field(ge=0)
    current_size: int = Field(ge=0)
    booked_size: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    incompleted_count: int = Field(ge=0)
    noshow_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    orginal_capacity_booked: float
    current_capacity_booked: float
    rider_hours_per_hour: int | None = None
    booked_hours_per_hour: int = Field(ge=0)
    man_hour: float | None = None

    _blank = field_validator("*", mode="before")(_blank_to_none)

    @field_validator("hour")
    @classmethod
    def whole_hour(cls, v: float) -> float:
        if v != int(v):
            raise ValueError("hour must be a whole number")
        return v

    @model_validator(mode="after")
    def consistent(self):
        if self.breached_count > self.total_orders:
            raise ValueError("breached_count exceeds total_orders")
        return self


class IngestionResult(BaseModel):
    status: str
    source: str
    sha256: str
    rows: int = 0
    stores: int = 0
    cities: int = 0
    synthetic: bool = False
    errors: list[str] = []


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def parse_csv(path: Path, max_errors: int = 20) -> tuple[list[GoldCsvRow], list[str]]:
    rows, errors = [], []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for line_no, raw in enumerate(reader, start=2):
            try:
                rows.append(GoldCsvRow.model_validate(raw))
            except ValidationError as e:
                errors.append(f"line {line_no}: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
                if len(errors) >= max_errors:
                    break
    keys = [(r.store, r.charge_date, int(r.hour)) for r in rows]
    if len(keys) != len(set(keys)):
        errors.append("duplicate (store, charge_date, hour) keys in file")
    return rows, errors


def _upsert(session: Session, model, values: list[dict], conflict_cols: list[str]) -> None:
    if not values:
        return
    stmt = insert(model).values(values)
    update_cols = {c: stmt.excluded[c] for c in values[0] if c not in conflict_cols}
    session.execute(stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols))


def ingest_csv(session: Session, path: Path, force: bool = False, synthetic: bool = False) -> IngestionResult:
    path = Path(path)
    # Generated demo files are always flagged, even if --synthetic was forgotten.
    synthetic = synthetic or path.name.upper().startswith("SYNTHETIC")
    kind = "orders_csv_synthetic" if synthetic else "orders_csv"
    digest = file_sha256(path)
    prior = session.scalar(
        select(IngestionRun).where(IngestionRun.sha256 == digest, IngestionRun.status == "success").limit(1)
    )
    if prior and not force:
        log_event(log, "ingestion skipped: file already ingested", sha256=digest, source=path.name)
        return IngestionResult(status="skipped", source=path.name, sha256=digest, rows=prior.row_count)

    rows, errors = parse_csv(path)
    if errors:
        session.add(IngestionRun(source_name=path.name, kind=kind, sha256=digest, status="failed", error="; ".join(errors)[:4000],
                                 finished_at=datetime.now(timezone.utc)))
        log_event(log, "ingestion validation failed", source=path.name, errors=errors[:5])
        return IngestionResult(status="failed", source=path.name, sha256=digest, errors=errors)

    # Dimensions
    cities = {r.city: normalize_city(r.city) for r in rows}
    _upsert(session, City, [{"name": n, "normalized_name": nn} for n, nn in cities.items()], ["name"])
    city_ids = dict(session.execute(select(City.name, City.id)).all())

    stores = {r.store: city_ids[r.city] for r in rows}
    _upsert(session, Store, [{"code": c, "city_id": cid} for c, cid in stores.items()], ["code"])
    store_ids = dict(session.execute(select(Store.code, Store.id)).all())

    slots: dict[tuple, dict] = {}
    for r in rows:
        key = (store_ids[r.store], r.charge_date, r.slot_name)
        slot = {
            "store_id": key[0], "charge_date": r.charge_date, "slot_name": r.slot_name,
            "slot_start": r.slot_start, "slot_end": r.slot_end, "slot_type": r.slot_type,
            "original_size": r.orginal_size, "current_size": r.current_size, "booked_size": r.booked_size,
            "completed_count": r.completed_count, "incompleted_count": r.incompleted_count,
            "noshow_count": r.noshow_count, "cancelled_count": r.cancelled_count,
            "original_capacity_booked": r.orginal_capacity_booked,
            "current_capacity_booked": r.current_capacity_booked,
            "rider_minutes": r.rider_hours_per_hour, "booked_minutes": r.booked_hours_per_hour,
            "man_hour": r.man_hour,
        }
        if key in slots and slots[key] != slot:
            raise ValueError(f"inconsistent slot capacity for {r.store} {r.charge_date} {r.slot_name}")
        slots[key] = slot
    slot_list = list(slots.values())
    for i in range(0, len(slot_list), 1000):
        _upsert(session, RiderSlot, slot_list[i:i + 1000], ["store_id", "charge_date", "slot_name"])
    slot_ids = {
        (sid, d, n): pk
        for pk, sid, d, n in session.execute(
            select(RiderSlot.id, RiderSlot.store_id, RiderSlot.charge_date, RiderSlot.slot_name)
        ).all()
    }

    facts = []
    for r in rows:
        sid = store_ids[r.store]
        facts.append({
            "store_id": sid, "slot_id": slot_ids[(sid, r.charge_date, r.slot_name)],
            "charge_date": r.charge_date, "hour": int(r.hour),
            "hour_start": r.hour_start, "hour_end": r.hour_end,
            "total_orders": r.total_orders, "breached_count": r.breached_count, "breached_rate": r.breached_rate,
            "is_problem_hour": bool(r.is_problem_hour), "pileup_count": r.pileup_count,
            "pileup_flag": bool(r.pileup_flag), "avg_or2a": r.avg_or2a, "o2a": r.o2a,
            "completed_order_count": r.completed_order_count, "cancelled_order_count": r.cancelled_order_count,
            "rto_order_count": r.rto_order_count, "order_projection": r.order_projection,
            "is_synthetic": synthetic,
        })
    for i in range(0, len(facts), 1000):
        _upsert(session, StoreHourlyMetric, facts[i:i + 1000], ["store_id", "charge_date", "hour"])

    session.add(IngestionRun(source_name=path.name, kind=kind, sha256=digest, status="success", row_count=len(rows),
                             finished_at=datetime.now(timezone.utc)))
    log_event(log, "ingestion complete", source=path.name, rows=len(rows), stores=len(stores), cities=len(cities))
    return IngestionResult(status="success", source=path.name, sha256=digest, rows=len(rows),
                           stores=len(stores), cities=len(cities), synthetic=synthetic)
