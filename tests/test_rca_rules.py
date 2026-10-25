"""Unit tests for the deterministic RCA rules (no database)."""
from qc_rca.analytics.rca import CheckStatus, HourInput, analyze_store_day, render_markdown, weighted_rollup


def h(hour, **kw):
    base = dict(hour=hour, total_orders=100, breached_count=10, breached_rate=0.1, is_problem_hour=True,
                avg_or2a=5.0, order_projection=100.0, pileup_flag=False, pileup_count=0,
                current_capacity_booked=0.95, man_hour=0.9, noshow_count=0, current_size=20, booked_size=19)
    base.update(kw)
    return HourInput(**base)


def flags(rca, hour):
    return next(x for x in rca.hours if x.hour == hour).flags


def test_demand_spike_threshold_is_strictly_above_110_percent():
    rca = analyze_store_day("S", "d", [h(1, total_orders=110), h(2, total_orders=111)])
    assert "demand spike" not in flags(rca, 1)
    assert "demand spike" in flags(rca, 2)


def test_booking_and_utilization_thresholds():
    rca = analyze_store_day("S", "d", [h(1, current_capacity_booked=0.90, man_hour=0.85),
                                       h(2, current_capacity_booked=0.89, man_hour=0.84)])
    assert flags(rca, 1) == []
    assert flags(rca, 2) == ["booking gap", "utilization gap"]


def test_sustained_pileup_needs_three_consecutive_clock_hours():
    rows = [h(8, pileup_flag=True, pileup_count=3), h(9, pileup_flag=True, pileup_count=2),
            h(10, pileup_flag=True, pileup_count=4), h(13, pileup_flag=True, pileup_count=1),
            h(14, pileup_flag=True, pileup_count=1)]
    rca = analyze_store_day("S", "d", rows)
    assert flags(rca, 10) == ["pileup (sustained)"]
    assert flags(rca, 9) == ["pileup"]
    assert flags(rca, 14) == ["pileup"]  # 13-14 only; gap at 11-12 breaks the run
    assert rca.factor_counts["sustained pileup"] == 1


def test_missing_man_hour_is_unknown_not_a_gap():
    rca = analyze_store_day("S", "d", [h(1, man_hour=None)])
    f = rca.hours[0]
    assert f.utilization.status is CheckStatus.unknown
    assert "utilization gap" not in f.flags
    assert any("man_hour not available" in m for m in rca.missing_data)


def test_missing_avg_or2a_excluded_from_weighting():
    s = weighted_rollup([h(1, avg_or2a=10.0, total_orders=100), h(2, avg_or2a=None, total_orders=100)])
    assert s.weighted_avg_or2a == 10.0
    assert s.or2a_order_coverage == 0.5
    assert s.total_orders == 200


def test_weighted_rollup_weights_by_orders():
    s = weighted_rollup([h(1, total_orders=10, breached_count=10, avg_or2a=100.0),
                         h(2, total_orders=90, breached_count=0, breached_rate=0, avg_or2a=0.0,
                           is_problem_hour=False)])
    assert s.weighted_breach_rate == 0.1
    assert s.weighted_avg_or2a == 10.0
    assert s.problem_hours == 1


def test_capacity_mismatch_is_reported_as_data_quality():
    rca = analyze_store_day("S", "d", [h(1, booked_size=24, current_size=20, current_capacity_booked=0.95)])
    assert rca.hours[0].booking.data_quality_note


def test_no_problem_hours_message():
    md = render_markdown(analyze_store_day("S", "d", [h(1, breached_count=0, breached_rate=0, is_problem_hour=False)]))
    assert "No problem hours detected" in md
