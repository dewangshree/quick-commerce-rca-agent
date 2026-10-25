"""Ingestion: validation, idempotency, transactional rollback, schema constraints."""
import csv
import shutil

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from qc_rca.config import get_settings
from qc_rca.db.models import City, IngestionRun, RiderSlot, Store, StoreHourlyMetric
from qc_rca.db.session import get_engine, session_scope
from qc_rca.ingestion import GoldCsvRow, ingest_csv, normalize_city, parse_csv


def counts():
    with session_scope() as s:
        return tuple(s.scalar(select(func.count()).select_from(m)) for m in (City, Store, RiderSlot, StoreHourlyMetric))


def test_loaded_counts_match_csv():
    assert counts() == (9, 233, 2280, 3813)


def test_reingest_same_file_is_skipped():
    with session_scope() as s:
        assert ingest_csv(s, get_settings().data_csv_path).status == "skipped"
    assert counts() == (9, 233, 2280, 3813)


def test_forced_reingest_is_idempotent():
    with session_scope() as s:
        assert ingest_csv(s, get_settings().data_csv_path, force=True).status == "success"
    assert counts() == (9, 233, 2280, 3813)


def test_invalid_file_is_rejected_without_writes(tmp_path):
    bad = tmp_path / "bad.csv"
    shutil.copy(get_settings().data_csv_path, bad)
    rows = list(csv.DictReader(open(bad)))
    rows[5]["breached_count"] = str(int(rows[5]["total_orders"]) + 1)
    rows[6]["hour"] = "25.0"
    rows[7]["store"] = "STORE_999"
    with open(bad, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    before = counts()
    with session_scope() as s:
        res = ingest_csv(s, bad)
    assert res.status == "failed" and len(res.errors) == 2
    assert counts() == before  # STORE_999 row not written
    with session_scope() as s:
        assert s.scalar(select(IngestionRun).where(IngestionRun.status == "failed")) is not None


def test_row_model_normalises_blanks_and_rejects_fractional_hour():
    row = next(csv.DictReader(open(get_settings().data_csv_path)))
    row["man_hour"] = ""
    assert GoldCsvRow.model_validate(row).man_hour is None
    row["hour"] = "7.5"
    with pytest.raises(ValueError):
        GoldCsvRow.model_validate(row)


def test_duplicate_keys_detected(tmp_path):
    p = tmp_path / "dup.csv"
    lines = open(get_settings().data_csv_path).read().splitlines()
    p.write_text("\n".join([lines[0], lines[1], lines[1]]) + "\n")
    _, errors = parse_csv(p)
    assert any("duplicate" in e for e in errors)


def test_check_constraint_enforced_in_database():
    with pytest.raises(IntegrityError):
        with get_engine().begin() as c:
            c.execute(text("UPDATE store_hourly_metrics SET breached_count = total_orders + 1 WHERE id = "
                           "(SELECT min(id) FROM store_hourly_metrics)"))


def test_city_normalisation():
    assert normalize_city("Mumbai  north ") == "mumbai north"
