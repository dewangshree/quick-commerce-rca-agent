"""Generate SYNTHETIC demo days from the one real day of data.

The output is NOT real operational data. Each day is a seeded random variation of
2026-04-22 (demand level, breach rate, pileup, rider supply), written to
data/synthetic/SYNTHETIC_*.csv and meant to be ingested with `--synthetic`, which
flags every row so the UI and reports label it.

Usage: python scripts/generate_synthetic_days.py [--days 6]
"""
import argparse
import csv
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "quick_commerce_orders_gold_20260422.csv"
OUT_DIR = ROOT / "data" / "synthetic"
SOURCE_DATE = "2026-04-22"


def fnum(v):
    return None if v == "" else float(v)


def fmt(v, nd=None):
    if v is None:
        return ""
    return str(v) if nd is None else f"{v:.{nd}f}"


def make_day(rows, day: date, seed: int) -> list[dict]:
    rng = random.Random(seed)
    day_s = day.isoformat()
    demand = rng.uniform(0.85, 1.12)          # network demand level for the day
    stress = rng.uniform(0.6, 1.5)            # how bad assignment delays are
    store_f = defaultdict(lambda: (rng.uniform(0.9, 1.1), rng.uniform(0.7, 1.4)))
    slot_cache: dict[tuple, dict] = {}
    out = []
    for r in rows:
        sf, ss = store_f[r["store"]]
        n = dict(r)
        n["charge_date"] = day_s
        n["hour_start"] = r["hour_start"].replace(SOURCE_DATE, day_s)
        n["hour_end"] = r["hour_end"].replace(SOURCE_DATE, day_s)

        total = max(1, round(int(r["total_orders"]) * demand * sf * rng.uniform(0.9, 1.1)))
        rate = min(1.0, float(r["breached_rate"]) * stress * ss * rng.uniform(0.7, 1.3))
        if rate > 0 and rng.random() < 0.15:
            rate = 0.0
        breached = min(total, round(rate * total))
        cancelled = min(total, round(int(r["cancelled_order_count"]) * rng.uniform(0.5, 1.5)))
        rto = min(total - cancelled, round(int(r["rto_order_count"]) * rng.uniform(0.5, 1.5)))
        pile = round(int(r["pileup_count"]) * stress * rng.uniform(0.5, 1.5)) if breached else 0
        avg = fnum(r["avg_or2a"])
        if avg is not None:
            avg = 0.0 if breached == 0 else max(0.1, (avg or 1.0) * stress * ss * rng.uniform(0.7, 1.3))
        o2a = fnum(r["o2a"])
        if o2a is not None:
            o2a = o2a * rng.uniform(0.8, 1.2)
        proj = float(r["order_projection"]) * rng.uniform(0.95, 1.05)
        n.update({
            "total_orders": total, "breached_count": breached, "breached_rate": fmt(breached / total, 17),
            "is_problem_hour": int(breached > 0), "pileup_count": pile, "pileup_flag": int(pile > 0),
            "avg_or2a": fmt(avg), "o2a": fmt(o2a), "completed_order_count": total - cancelled - rto,
            "cancelled_order_count": cancelled, "rto_order_count": rto, "order_projection": fmt(round(proj), 1),
        })

        # Rider capacity is per (store, slot): vary it once and reuse for every hour of the slot.
        key = (r["store"], r["slot_name"])
        if key not in slot_cache:
            size = int(r["current_size"])
            booked = max(0, min(round(size * 1.2), int(r["booked_size"]) + rng.randint(-3, 2)))
            noshow = max(0, int(r["noshow_count"]) + rng.randint(-1, 2))
            booked_min = int(r["booked_hours_per_hour"])
            rider_min = fnum(r["rider_hours_per_hour"])
            if rider_min is not None and booked_min:
                rider_min = round(min(booked_min * 1.05, rider_min * rng.uniform(0.85, 1.08)))
            slot_cache[key] = {
                "booked_size": booked, "noshow_count": noshow,
                "current_capacity_booked": fmt(round(booked / size, 2) if size else 0.0),
                "orginal_capacity_booked": fmt(round(booked / int(r["orginal_size"]), 2) if int(r["orginal_size"]) else 0.0),
                "rider_hours_per_hour": "" if rider_min is None else int(rider_min),
                "man_hour": "" if rider_min is None or not booked_min else fmt(rider_min / booked_min, 20),
            }
        n.update(slot_cache[key])
        out.append(n)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=6)
    args = p.parse_args()
    rows = list(csv.DictReader(open(SOURCE, newline="")))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = date.fromisoformat(SOURCE_DATE)
    for i in range(args.days, 0, -1):
        day = base - timedelta(days=i)
        path = OUT_DIR / f"SYNTHETIC_quick_commerce_orders_gold_{day:%Y%m%d}.csv"
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(make_day(rows, day, seed=int(day.strftime("%Y%m%d"))))
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
