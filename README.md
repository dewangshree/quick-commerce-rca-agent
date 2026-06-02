# Quick-Commerce RCA Agent

> AI-powered multi-turn conversational agent for OR2A SLA analysis and root-cause investigation in quick-commerce delivery operations.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Usage](#usage)
- [Design Decisions](#design-decisions)
- [Assignment Checklist](#assignment-checklist)
- [AI Tools Disclosure](#ai-tools-disclosure)
- [Future Improvements](#future-improvements)

---

## Overview

The Quick-Commerce RCA Agent enables operations teams to investigate delivery performance through natural language. It supports multi-turn conversations, deterministic root-cause analysis, SQL-powered analytics, and dynamic documentation retrieval via an MCP server — all through a clean chat interface.

---

## Features

### Multi-Turn Conversational Agent

The agent maintains session context, enabling natural drill-down investigations across multiple turns.

**Example conversation flow:**

```
1. "How did Bangalore do on 2026-04-22?"
2. "Why did STORE_101 underperform?"
3. "Walk me through the morning hours."
4. "What about STORE_102?"
5. "Compare them."
```

---

### Deterministic RCA Engine

Root-cause analysis is implemented in Python — not prompts — to ensure consistent, hallucination-free results.

Detected root causes:

| Root Cause | Description |
|---|---|
| Demand Spike | Sudden surge in order volume |
| Pileup | Order accumulation exceeding capacity |
| Sustained Pileup | Prolonged pileup beyond recovery threshold |
| Booking Gap | Insufficient rider bookings relative to demand |
| Utilization Gap | Low rider utilization during available hours |

---

### SQL Analytics Tool

Queries operational data stored in SQLite.

Supported analyses:

- City-level performance summaries
- Store-level performance breakdowns
- Hourly metric trends
- Breach rate calculations
- OR2A metric aggregation

---

### Documentation Tool (MCP)

Retrieves operational documentation dynamically through a Filesystem MCP Server.

Documents accessed:

- `order_ready_to_assignment.md` — OR2A metric definitions
- `quick_commerce_rca_logic.md` — RCA playbook
- `quick_commerce_orders_gold.md` — Dataset schema reference

---

## Architecture

```
User
 │
 ▼
Streamlit Frontend
 │
 ▼
FastAPI Backend
 │
 ▼
LangGraph Agent
 │
 ▼
LangChain Tools
 │
 ├── sql_tool           → SQLite Database
 ├── rca_tool           → Deterministic RCA Engine
 ├── city_summary_tool  → Aggregated City Metrics
 └── docs_tool          → Filesystem MCP Server → Documentation Files
 │
 ▼
Groq LLM (Llama 3.3 70B Versatile)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Framework | LangGraph, LangChain |
| LLM | Groq — Llama 3.3 70B Versatile |
| Backend | FastAPI, Uvicorn |
| Frontend | Streamlit |
| Database | SQLite |
| MCP | Filesystem MCP Server |

---

## Project Structure

```
quick-commerce-rca-agent/
├── main.py                        # FastAPI backend entry point
├── app.py                         # Streamlit frontend
├── requirements.txt
├── .env                           # GROQ_API_KEY (not committed)
├── data/
│   └── quick_commerce_orders_gold_20260422.csv
├── agent/
│   ├── graph.py                   # LangGraph agent definition
│   ├── chains.py                  # LangChain chains and prompt templates
│   └── tools/
│       ├── sql_tool.py
│       ├── rca_tool.py
│       ├── city_summary_tool.py
│       └── docs_tool.py
└── docs/                          # Documentation served via MCP
    ├── order_ready_to_assignment.md
    ├── quick_commerce_rca_logic.md
    └── quick_commerce_orders_gold.md
```

---

## Getting Started

### Prerequisites

- Python 3.9+
- A [Groq API key](https://console.groq.com)

### Installation

```bash
git clone https://github.com/your-username/quick-commerce-rca-agent.git
cd quick-commerce-rca-agent
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_api_key_here
```

### Running the Application

**Terminal 1 — Start the backend:**

```bash
python main.py
```

The FastAPI server starts on `http://localhost:8000`.

**Terminal 2 — Start the frontend:**

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

> The dataset is automatically loaded into SQLite on startup. No manual database setup is required.

---

## Usage

### Sample Queries

**City-level analysis:**
```
How did Bangalore do on 2026-04-22?
```

**Store RCA:**
```
Why did STORE_101 underperform that day?
```

**Hourly drill-down:**
```
Walk me through the morning hours at STORE_101.
```

**Comparison:**
```
Compare STORE_101 and STORE_102.
```

**Documentation lookup:**
```
What is OR2A?
Explain the RCA logic.
```

---

## Design Decisions

### SQLite over PostgreSQL

SQLite was chosen for zero-setup, fast iteration, and suitability for the assignment dataset size. A production deployment would migrate to PostgreSQL for concurrent writes and larger workloads.

---

### Deterministic RCA over LLM Reasoning

RCA logic is implemented in Python code rather than prompts. This guarantees consistent threshold evaluation and eliminates hallucinations. The tradeoff is reduced flexibility when business rules change — a config-driven rules engine could address this in future iterations.

---

### Filesystem MCP over Embedded Prompts

Documentation is retrieved dynamically through MCP rather than injected into the system prompt. This keeps knowledge decoupled from model weights, demonstrates real MCP integration, and makes documentation updates instant without redeployment.

---

### Streamlit over Custom React Frontend

Streamlit was chosen for rapid delivery and minimal setup, which is appropriate for demonstrating agent behavior in an assignment context. A production-grade UI would use React for richer interactivity and component control.

---

## Assignment Checklist

| Requirement | Status |
|---|---|
| Multi-turn Chat Agent | ✅ |
| LangGraph | ✅ |
| LangChain | ✅ |
| MCP Integration | ✅ |
| SQL Tool | ✅ |
| RCA Tool | ✅ |
| Documentation Tool | ✅ |
| FastAPI Backend | ✅ |
| Streamlit Frontend | ✅ |
| CSV Data Source | ✅ |
| Conversational RCA | ✅ |
| Sample Question Support | ✅ |
| Follow-up Question Support | ✅ |
| Tradeoff Documentation | ✅ |
| AI Tool Disclosure | ✅ |

---

## AI Tools Disclosure

The following AI tools were used during development:

- **Claude** — Architecture brainstorming, LangGraph setup, LangChain setup, FastAPI scaffolding, code generation
- **ChatGPT** — MCP integration guidance, code generation assistance

All generated code was reviewed, tested, modified, and integrated manually.

---

## Future Improvements

- Session persistence across browser refreshes
- User authentication and role-based access
- PostgreSQL support for production workloads
- Advanced analytics dashboards with time-series visualizations
- Additional MCP integrations (GitHub, Notion, Slack)
- Historical trend analysis across multiple dates
- Production deployment with Docker and CI/CD
