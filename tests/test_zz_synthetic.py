"""Synthetic demo days must be flagged and labelled everywhere.

Named test_zz_* so it runs last: it adds a synthetic date to the test database.
"""
from datetime import date

import pytest

from qc_rca.analytics import service
from qc_rca.config import ROOT_DIR, get_settings
from qc_rca.db.session import session_scope
from qc_rca.ingestion import ingest_csv
from qc_rca.workflow.graph import run_investigation

SYN = ROOT_DIR / "data" / "synthetic" / "SYNTHETIC_quick_commerce_orders_gold_20260421.csv"
pytestmark = pytest.mark.skipif(not SYN.exists(), reason="run scripts/generate_synthetic_days.py first")


@pytest.fixture(scope="module", autouse=True)
def synthetic_day():
    with session_scope() as s:
        # filename alone triggers the flag, even without synthetic=True
        res = ingest_csv(s, SYN)
    assert res.status in ("success", "skipped")


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_enabled", False)


def test_dates_are_flagged():
    info = {d["date"]: d["synthetic"] for d in service.dataset_dates()}
    assert info == {"2026-04-21": True, "2026-04-22": False}
    assert service.real_dates() == ["2026-04-22"]
    assert service.is_synthetic_date(date(2026, 4, 21))


def test_real_day_unchanged_by_synthetic_rows():
    assert service.store_day_rca("STORE_101", date(2026, 4, 22)).summary.total_orders == 397


def test_default_date_is_latest_real_date():
    r = run_investigation("Why did STORE_101 underperform?")
    assert r["scope"]["date"] == "2026-04-22" and "SYNTHETIC" not in r["answer_markdown"]
    assert any("Only one real date" in x for x in r["confidence"]["limitations"])


def test_synthetic_report_is_labelled():
    r = run_investigation("Why did STORE_101 underperform on 2026-04-21?")
    assert r["answer_markdown"].startswith("> ⚠️ SYNTHETIC DEMO DATA")
    assert "SYNTHETIC" in r["confidence"]["limitations"][0]
    assert r["confidence"]["level"] == "low"


def test_daily_trend_marks_synthetic():
    rows = service.daily_trend()
    assert [(x["date"], x["synthetic"]) for x in rows] == [("2026-04-21", True), ("2026-04-22", False)]
