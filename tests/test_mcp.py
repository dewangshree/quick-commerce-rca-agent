"""MCP server tests through a real MCP client session (in-memory transport) and over stdio."""
import json
import sys

import anyio
import pytest
from mcp import Client, StdioServerParameters

from qc_rca import mcp_server
from qc_rca.config import ROOT_DIR


def _payload(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


def call(name, args):
    async def go():
        async with Client(mcp_server.mcp) as c:
            return await c.call_tool(name, args)
    return anyio.run(go)


def test_lists_only_readonly_tools():
    async def go():
        async with Client(mcp_server.mcp) as c:
            return await c.list_tools()
    tools = anyio.run(go).tools
    names = {t.name for t in tools}
    assert names == {"store_analytics", "rca_analysis", "city_comparison", "city_store_summary", "sla_metrics",
                     "knowledge_search"}
    assert all(t.annotations.read_only_hint for t in tools)


def test_rca_tool():
    out = _payload(call("rca_analysis", {"store": "store_101", "day": "2026-04-22"}))
    assert out["rca"]["summary"]["total_orders"] == 397


def test_store_and_city_tools():
    out = _payload(call("store_analytics", {"store": "STORE_101", "day": "2026-04-22", "hour_from": 6, "hour_to": 8}))
    assert [h["hour"] for h in out["hours"]] == [6, 7, 8]
    out = _payload(call("city_comparison", {"day": "2026-04-22"}))
    assert len(out["cities"]) == 9
    out = _payload(call("sla_metrics", {"day": "2026-04-22", "city": "Noida"}))
    assert out["overview"]["total_orders"] > 0
    out = _payload(call("knowledge_search", {"query": "booking gap", "doc_type": "playbook"}))
    assert out["chunks"] and all(c["doc_type"] == "playbook" for c in out["chunks"])


def test_invalid_inputs_rejected():
    res = call("store_analytics", {"store": "1; DROP TABLE stores", "day": "2026-04-22"})
    assert res.is_error
    res = call("store_analytics", {"store": "STORE_101", "day": "2026-04-22", "hour_from": 40})
    assert res.is_error
    out = _payload(call("rca_analysis", {"store": "STORE_9999", "day": "2026-04-22"}))
    assert "Unknown store" in out["error"]


def test_timeout_is_enforced(monkeypatch):
    import time

    monkeypatch.setattr(mcp_server, "TOOL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(mcp_server.service, "store_hours", lambda *a: time.sleep(1))
    out = _payload(call("store_analytics", {"store": "STORE_101", "day": "2026-04-22"}))
    assert "timed out" in out["error"]


def test_stdio_transport():
    params = StdioServerParameters(command=sys.executable, args=["-m", "qc_rca.mcp_server"], cwd=str(ROOT_DIR),
                                   env={**__import__("os").environ})

    async def go():
        async with Client(params) as c:
            tools = await c.list_tools()
            res = await c.call_tool("rca_analysis", {"store": "STORE_102", "day": "2026-04-22"})
            return tools, res
    tools, res = anyio.run(go)
    assert len(tools.tools) == 6
    assert _payload(res)["rca"]["store"] == "STORE_102"
