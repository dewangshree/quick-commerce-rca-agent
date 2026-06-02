import os
import json
from typing import Annotated, TypedDict
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

import tools as t

from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def sql_tool(query: str) -> str:
    """Execute a SQL query against the quick_commerce orders SQLite database.
    Table name: orders. Columns include: charge_date, store, city, city_lower, hour,
    total_orders, breached_count, breached_rate, is_problem_hour, pileup_count,
    pileup_flag, avg_or2a, order_projection, current_capacity_booked, man_hour,
    noshow_count, current_size, booked_size, completed_count.
    Use city_lower for case-insensitive city filtering.
    """
    return t.sql_query(query)


@tool
def rca_tool(store: str, date: str) -> str:
    """Run a full RCA (Root Cause Analysis) for a specific store on a given date.
    Checks demand spike, pileup, booking gap, and utilization gap.
    Args:
        store: Store code e.g. STORE_101
        date: Date in YYYY-MM-DD format e.g. 2026-04-22
    """
    return t.run_rca(store, date)


@tool
def city_summary_tool(city: str, date: str) -> str:
    """Get a performance summary for all stores in a city on a given date.
    Args:
        city: City name e.g. Bangalore, Chennai, Mumbai north
        date: Date in YYYY-MM-DD format e.g. 2026-04-22
    """
    return t.get_city_summary(city, date)


@tool
def docs_tool(topic: str) -> str:
    """Read documentation about the RCA playbook, OR2A metric, or table schema.
    Args:
        topic: One of 'rca_logic', 'or2a', 'gold_schema', or 'all'
    """
    if topic == "all":
        return t.get_all_docs()
    return t.read_docs(topic)


TOOLS = [sql_tool, rca_tool, city_summary_tool, docs_tool]
TOOL_MAP = {tool.name: tool for tool in TOOLS}

# ── LLM ───────────────────────────────────────────────────────────────────────

def get_llm():
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise ValueError("GROQ_API_KEY not set in environment")

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=api_key,
        temperature=0,
    )

    return llm.bind_tools(TOOLS)

# ── Graph State ───────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


SYSTEM_PROMPT = """You are an expert delivery operations analyst for Loadshare's quick-commerce business.
You help ops teams understand store performance and diagnose OR2A (Order Ready to Assignment) SLA breaches.

Available data: quick_commerce orders for 2026-04-22 across multiple cities and stores.

Key capabilities:
- City-level performance summaries
- Store-level performance drill-down
- Full RCA (root cause analysis) per store per day
- Raw SQL queries for custom analysis
- Documentation lookup

Guidelines:
- For city performance questions, use city_summary_tool
- For "why did store X underperform", use rca_tool
- For morning hours or specific hour analysis, use sql_tool to fetch those hours then explain
- For follow-up questions about a store already discussed, reuse context from conversation
- Always interpret numbers in human-friendly terms
- OR2A threshold: ≤ 0 is the stated client-specific healthy threshold, but flag any positive avg OR2A
- When a store name isn't found, check if the user might mean a different store code

Today's data date: 2026-04-22
"""


# ── Graph Nodes ───────────────────────────────────────────────────────────────

def call_model(state: AgentState):
    llm = get_llm()
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}


def call_tools(state: AgentState):
    last = state["messages"][-1]
    results = []
    for call in last.tool_calls:
        tool_fn = TOOL_MAP.get(call["name"])
        if tool_fn is None:
            result = f"Unknown tool: {call['name']}"
        else:
            try:
                result = tool_fn.invoke(call["args"])
            except Exception as e:
                result = f"Tool error: {str(e)}"
        results.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
    return {"messages": results}


def should_continue(state: AgentState):
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ── Build Graph ───────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("model", call_model)
    g.add_node("tools", call_tools)
    g.set_entry_point("model")
    g.add_conditional_edges("model", should_continue, {"tools": "tools", END: END})
    g.add_edge("tools", "model")
    return g.compile()


GRAPH = None


def get_graph():
    global GRAPH
    if GRAPH is None:
        GRAPH = build_graph()
    return GRAPH


def chat(messages: list) -> str:
    """
    messages: list of {"role": "user"|"assistant", "content": str}
    Returns the assistant reply string.
    """
    graph = get_graph()
    lc_messages = []
    for m in messages:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))

    result = graph.invoke({"messages": lc_messages})
    last = result["messages"][-1]
    return last.content
