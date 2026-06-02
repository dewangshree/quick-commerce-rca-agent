import os
import json
from database import run_query
import asyncio
from mcp_client import get_mcp_tools

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")


# ── Docs Tool ─────────────────────────────────────────────────────────────────

def read_docs(filename: str) -> str:
    safe_names = {
        "rca_logic": "quick_commerce_rca_logic.md",
        "or2a": "order_ready_to_assignment.md",
        "gold_schema": "quick_commerce_orders_gold.md",
    }

    fname = safe_names.get(filename, filename)

    async def _read():
        tools = await get_mcp_tools()

        for tool in tools:
            if tool.name == "read_file":
                result = await tool.ainvoke(
                    {"path": f"./docs/{fname}"}
                )
                return str(result)[:900]

        return f"Unable to read {fname} through MCP."

    return asyncio.run(_read())


def get_all_docs() -> str:
    """Return all documentation concatenated."""
    out = []
    for fname in os.listdir(DOCS_DIR):
        if fname.endswith(".md"):
            with open(os.path.join(DOCS_DIR, fname)) as f:
                out.append(f"## {fname}\n\n{f.read()}")
    return "\n\n---\n\n".join(out)


# ── SQL Tool ──────────────────────────────────────────────────────────────────

def sql_query(query: str) -> str:
    """Execute SQL against the orders table and return JSON string."""
    result = run_query(query)
    if isinstance(result, dict) and "error" in result:
        return f"SQL Error: {result['error']}"
    if not result:
        return "No records found."
    return json.dumps(result, default=str)


# ── RCA Tool ──────────────────────────────────────────────────────────────────

def run_rca(store: str, date: str) -> str:
    """
    Run the full RCA playbook for a store on a given date.
    Returns formatted markdown output per the RCA format spec.
    """
    # Fetch all hours for this store+date
    rows = run_query(f"""
        SELECT *
        FROM orders
        WHERE store = '{store}'
          AND charge_date = '{date}'
        ORDER BY hour
    """)

    if isinstance(rows, dict) and "error" in rows:
        return f"Database error: {rows['error']}"
    if not rows:
        return f"No data found for store '{store}' on {date}. Check store name and date."

    # Store-day summary
    total = sum(r["total_orders"] for r in rows)
    if total == 0:
        return f"No orders found for store '{store}' on {date}."

    weighted_breached_rate = sum(r["breached_count"] for r in rows) / total
    weighted_avg_or2a = sum(r["avg_or2a"] * r["total_orders"] for r in rows) / total

    problem_hours = [r for r in rows if r.get("is_problem_hour", 0) == 1 or r.get("breached_rate", 0) > 0]

    output_lines = [
        f"## RCA Report — {store} — {date}",
        f"",
        f"**Store-Day Summary**",
        f"- Total orders: {total}",
        f"- Weighted breach rate: {weighted_breached_rate:.1%}",
        f"- Weighted avg OR2A: {weighted_avg_or2a:.1f} min",
        f"- Problem hours: {len(problem_hours)}",
        f"",
    ]

    if not problem_hours:
        output_lines.append("✅ No problem hours detected. Store performance was within SLA.")
        return "\n".join(output_lines)

    # Per-hour RCA
    for r in problem_hours:
        hour = int(r.get("hour", 0))
        avg_or2a = r.get("avg_or2a", 0) or 0
        total_orders = r.get("total_orders", 0) or 0
        order_projection = r.get("order_projection") or 0
        pileup_flag = r.get("pileup_flag", 0) or 0
        pileup_count = r.get("pileup_count", 0) or 0
        current_capacity_booked = r.get("current_capacity_booked") or 0
        man_hour = r.get("man_hour") or 0
        noshow_count = r.get("noshow_count", 0) or 0
        current_size = r.get("current_size", 0) or 0
        booked_size = r.get("booked_size", 0) or 0

        # Check 1: Demand Spike
        if order_projection > 0:
            demand_spike = total_orders > order_projection * 1.10
            demand_pct = ((total_orders - order_projection) / order_projection * 100) if order_projection else 0
            demand_str = f"{'YES' if demand_spike else 'NO'} — {total_orders} orders vs {order_projection:.0f} projected ({demand_pct:+.1f}%)"
        else:
            demand_spike = False
            demand_str = f"NO — projection not available ({total_orders} orders)"

        # Check 2: Pileup
        pileup = pileup_flag == 1
        # Check sustained pileup (3+ consecutive hours)
        hour_idx = rows.index(r)
        consec = 1
        i = hour_idx - 1
        while i >= 0 and rows[i].get("pileup_flag", 0) == 1:
            consec += 1
            i -= 1
        sustained = consec >= 3
        pileup_str = f"{'YES' if pileup else 'NO'} — {pileup_count} orders carried from previous hour"
        if pileup and sustained:
            pileup_str += " [SUSTAINED PILEUP — 3+ consecutive hours]"

        # Check 3a: Booking Gap
        booking_gap = current_capacity_booked < 0.90
        booking_str = f"{booked_size} of {current_size} slots booked ({current_capacity_booked:.1%})"

        # Check 3b: Utilization Gap
        util_gap = man_hour < 0.85
        util_str = f"man_hour ratio {man_hour:.2f} ({noshow_count} no-shows)"

        # Summary flags
        flags = []
        if demand_spike:
            flags.append("demand spike")
        if pileup:
            flags.append("pileup" + (" (sustained)" if sustained else ""))
        if booking_gap:
            flags.append("booking gap")
        if util_gap:
            flags.append("utilization gap")
        summary = ", ".join(flags) if flags else "no clear root cause identified"

        output_lines += [
            f"### {store} — Hour {hour} — avg OR2A: {avg_or2a:.1f} min",
            f"",
            f"1. Demand Spike: {demand_str}",
            f"2. Pileup: {pileup_str}",
            f"3. Supply:",
            f"   a. Booking: {booking_str}",
            f"   b. Utilization: {util_str}",
            f"",
            f"**Summary**: OR2A was {avg_or2a:.1f} min due to {summary}.",
            f"",
        ]

    return "\n".join(output_lines)


def get_city_summary(city: str, date: str) -> str:
    """Get store-level summary for a city on a date."""
    rows = run_query(f"""
        SELECT 
            store,
            SUM(total_orders) as total_orders,
            SUM(breached_count) as total_breached,
            ROUND(SUM(breached_count)*1.0/NULLIF(SUM(total_orders),0)*100, 1) as breach_rate_pct,
            ROUND(SUM(avg_or2a * total_orders)/NULLIF(SUM(total_orders),0), 1) as weighted_avg_or2a,
            SUM(is_problem_hour) as problem_hours,
            COUNT(*) as total_hours
        FROM orders
        WHERE city_lower = '{city.lower().strip()}'
          AND charge_date = '{date}'
        GROUP BY store
        ORDER BY breach_rate_pct DESC
    """)

    if isinstance(rows, dict) and "error" in rows:
        return f"Database error: {rows['error']}"
    if not rows:
        # Try partial match
        rows = run_query(f"""
            SELECT DISTINCT city FROM orders 
            WHERE city_lower LIKE '%{city.lower().strip().split()[0]}%'
        """)
        cities = [r["city"] for r in rows] if rows else []
        return f"No data for city '{city}' on {date}. Available cities with similar names: {cities}"

    lines = [f"## {city} — {date}", ""]
    for r in rows:
        status = "🔴" if (r["breach_rate_pct"] or 0) > 20 else "🟡" if (r["breach_rate_pct"] or 0) > 5 else "🟢"
        lines.append(
            f"{status} **{r['store']}**: {r['total_orders']} orders, "
            f"{r['breach_rate_pct'] or 0}% breach rate, "
            f"avg OR2A {r['weighted_avg_or2a'] or 0} min, "
            f"{r['problem_hours']} problem hours"
        )
    return "\n".join(lines)
