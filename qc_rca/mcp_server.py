"""MCP server exposing restricted, read-only operations-intelligence tools.

Replaces the original generic filesystem MCP server (which exposed arbitrary
file reads under ./docs via npx). Every tool here:
* validates its inputs with Pydantic constraints,
* runs on the read-only database engine (``default_transaction_read_only`` +
  ``statement_timeout``),
* is bounded by ``MCP_TOOL_TIMEOUT_S``,
* returns ``{"error": ...}`` for expected failures and never leaks internals.

Run (stdio):  python -m qc_rca.mcp_server
"""
import os
import sys
from datetime import date
from typing import Annotated, Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from qc_rca.analytics import service
from qc_rca.analytics.rca import render_markdown
from qc_rca.observability import configure_logging, get_logger
from qc_rca.rag.store import retrieve

log = get_logger("mcp")
TOOL_TIMEOUT_S = float(os.getenv("MCP_TOOL_TIMEOUT_S", "20"))
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

StoreCode = Annotated[str, Field(pattern=r"^(?i:store)_?\d{1,6}$", description="Store code, e.g. STORE_101")]
CityName = Annotated[str, Field(min_length=2, max_length=60, pattern=r"^[A-Za-z .\-]+$")]
IsoDate = Annotated[date, Field(description="Date in YYYY-MM-DD")]
Hour = Annotated[int, Field(ge=0, le=23)]

mcp = MCPServer(
    name="qc-ops-intelligence",
    instructions="Read-only quick-commerce SLA analytics, deterministic RCA and knowledge-base search. "
                 "All numbers come from the database; RCA factors are rule-based co-occurrences, not proven causes.",
)


async def _guarded(fn, *args) -> Any:
    try:
        with anyio.fail_after(TOOL_TIMEOUT_S):
            return await anyio.to_thread.run_sync(fn, *args, abandon_on_cancel=True)
    except service.NotFoundError as e:
        return {"error": str(e), "suggestions": e.suggestions}
    except TimeoutError:
        return {"error": f"tool timed out after {TOOL_TIMEOUT_S:.0f}s"}
    except Exception:  # noqa: BLE001
        log.exception("mcp tool failed")
        return {"error": "internal error while running tool"}


def _norm_store(s: str) -> str:
    digits = "".join(ch for ch in s if ch.isdigit())
    return f"STORE_{int(digits):03d}"


@mcp.tool(annotations=READ_ONLY)
async def store_analytics(store: StoreCode, day: IsoDate, hour_from: Hour = 0, hour_to: Hour = 23) -> dict:
    """Hour-by-hour metrics (orders, breaches, OR2A, pileup, rider capacity) for one store on one date."""
    if hour_from > hour_to:
        return {"error": "hour_from must be <= hour_to"}
    return await _guarded(service.store_hours, _norm_store(store), day, hour_from, hour_to)


@mcp.tool(annotations=READ_ONLY)
async def rca_analysis(store: StoreCode, day: IsoDate) -> dict:
    """Deterministic RCA playbook (demand, pileup, booking, utilization) for a store-day."""
    def run():
        rca = service.store_day_rca(_norm_store(store), day)
        return {"rca": rca.model_dump(), "markdown": render_markdown(rca)}
    return await _guarded(run)


@mcp.tool(annotations=READ_ONLY)
async def city_comparison(day: IsoDate, cities: Annotated[list[CityName], Field(max_length=10)] | None = None) -> dict:
    """Compare cities on orders, weighted breach rate, weighted OR2A and problem hours."""
    res = await _guarded(service.city_comparison, day, cities)
    return res if isinstance(res, dict) else {"cities": res}


@mcp.tool(annotations=READ_ONLY)
async def city_store_summary(city: CityName, day: IsoDate) -> dict:
    """Per-store roll-up for one city on one date, worst breach rate first."""
    return await _guarded(service.city_summary, city, day)


@mcp.tool(annotations=READ_ONLY)
async def sla_metrics(day: IsoDate, city: CityName | None = None) -> dict:
    """Network or city SLA KPIs plus breach rate by hour of day."""
    def run():
        return {"overview": service.sla_overview(day, city), "by_hour": service.breach_by_hour(day, city)}
    return await _guarded(run)


@mcp.tool(annotations=READ_ONLY)
async def knowledge_search(
    query: Annotated[str, Field(min_length=2, max_length=300)],
    k: Annotated[int, Field(ge=1, le=8)] = 4,
    doc_type: Literal["metric_definition", "schema", "playbook", "sla", "sop", "incident"] | None = None,
) -> dict:
    """Hybrid (vector + keyword) search over the operations knowledge base, with source citations.
    Results flagged is_sample=true are demonstration documents, not company policy."""
    def run():
        return retrieve(query, k=k, doc_types=[doc_type] if doc_type else None).model_dump()
    return await _guarded(run)


def main() -> None:
    configure_logging(os.getenv("LOG_LEVEL", "WARNING"), stream=sys.stderr)  # stdout is the MCP channel
    mcp.run("stdio")


if __name__ == "__main__":
    main()
