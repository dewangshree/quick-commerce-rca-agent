"""The PostgreSQL implementation must reproduce the ORIGINAL SQLite tools.run_rca /
get_city_summary outputs (tests/fixtures/golden_original_outputs.json, captured
from git HEAD before the migration), except for the documented bug fixes:

1. hours with missing man_hour were reported as "man_hour ratio 0.00" + UTILIZATION GAP;
   they are now "not available" and not flagged;
2. STORE_008 (one hour with null avg_or2a) crashed the original with TypeError.
"""
import json
import re
from datetime import date
from pathlib import Path

import pytest

from qc_rca.analytics import service
from qc_rca.analytics.rca import render_markdown

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "golden_original_outputs.json").read_text())
DAY = date(2026, 4, 22)
HOUR_HDR = re.compile(r"^### (STORE_\d+) — Hour (\d+) —")


def _strip_util(summary_line: str) -> str:
    m = re.match(r"^(\*\*Summary\*\*: OR2A was .* min due to )(.*)\.$", summary_line)
    flags = [f for f in m.group(2).split(", ") if f != "utilization gap"]
    return m.group(1) + (", ".join(flags) if flags else "no clear root cause identified") + "."


def test_golden_fixture_covers_all_stores():
    assert len(GOLDEN["rca"]) == 233
    assert GOLDEN["rca"]["STORE_008"].startswith("EXCEPTION: TypeError")


@pytest.mark.parametrize("store", sorted(GOLDEN["rca"]))
def test_store_rca_matches_original(store):
    new = render_markdown(service.store_day_rca(store, DAY))
    old = GOLDEN["rca"][store]
    if old.startswith("EXCEPTION"):
        assert store == "STORE_008"
        assert "Weighted avg OR2A" in new  # no longer crashes
        return
    rows = {r["hour"]: r for r in service.store_hours(store, DAY)["hours"]}
    new_lines, old_lines = new.split("\n"), old.split("\n")
    assert len(new_lines) == len(old_lines)
    hour = None
    for n, o in zip(new_lines, old_lines):
        if m := HOUR_HDR.match(o):
            hour = int(m.group(2))
        if n == o:
            continue
        # Only allowed difference: an hour whose man_hour is missing.
        assert hour is not None and rows[hour]["man_hour"] is None, f"{store} h{hour}: {o!r} -> {n!r}"
        if "Utilization" in o:
            assert "man_hour ratio 0.00" in o and "not available" in n
        else:
            assert n == _strip_util(o)


def _parse_city(md: str) -> dict:
    out = {}
    for line in md.split("\n"):
        m = re.match(r"^\S+ \*\*(STORE_\d+)\*\*: (\d+) orders, ([\d.]+)% breach rate, avg OR2A ([\d.]+) min, "
                     r"(\d+) problem hours", line)
        if m:
            out[m.group(1)] = (int(m.group(2)), float(m.group(3)), float(m.group(4)), int(m.group(5)))
    return out


@pytest.mark.parametrize("city", sorted(GOLDEN["city"]))
def test_city_summary_matches_original(city):
    old = _parse_city(GOLDEN["city"][city])
    new = service.city_summary(city, DAY)
    assert {r["store"] for r in new["stores"]} == set(old)
    for r in new["stores"]:
        o = old[r["store"]]
        assert r["total_orders"] == o[0]
        assert round(r["breach_rate"] * 100, 1) == o[1]
        assert r["problem_hours"] == o[3]
        # Original divided SUM(avg_or2a*orders) by ALL orders, including hours with null OR2A.
        # Only STORE_008 (null OR2A hours) may differ; elsewhere identical to 1 dp.
        if r["store"] != "STORE_008":
            assert round(r["weighted_avg_or2a"], 1) == o[2]


def test_mumbai_lookup_now_resolves():
    # Original exact match on "mumbai  north" (double space) failed for "Mumbai north".
    assert service.city_summary("Mumbai north", DAY)["city"] == "Mumbai  north"
    assert service.city_summary("mumbai", DAY)["stores"]
