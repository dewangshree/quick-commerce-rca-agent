# Quick-Commerce RCA Agent

AI-powered multi-turn conversational agent for OR2A SLA analysis and root-cause investigation in quick-commerce delivery operations.

---

# Overview

This project implements a conversational RCA (Root Cause Analysis) agent for delivery operations.

The agent enables operations users to:

* Analyze city-level performance
* Investigate store-level underperformance
* Drill down into hourly metrics
* Perform RCA using predefined business rules
* Ask follow-up questions in natural language
* Access operational documentation through MCP

The solution supports multi-turn conversations and provides explanations based on operational metrics, RCA playbooks, and reference documentation.

---

# Features

## Multi-Turn Conversational Agent

Supports natural language conversations across multiple turns.

Example:

1. How did Bangalore do on 2026-04-22?
2. Why did STORE_101 underperform?
3. Walk me through the morning hours.
4. What about STORE_102?
5. Compare them.

The agent maintains context during the session to support drill-downs and follow-up questions.

---

## RCA Analysis

Implements deterministic RCA logic based on the provided playbook.

The RCA engine identifies:

* Demand Spike
* Pileup
* Sustained Pileup
* Booking Gap
* Utilization Gap

This logic is implemented in Python code rather than prompts to ensure consistency and avoid hallucinations.

---

## SQL Analytics Tool

The agent can query operational data stored in SQLite.

Supported analysis includes:

* City-level performance
* Store-level performance
* Hourly performance
* Breach analysis
* OR2A metrics

---

## Documentation Tool

The agent can retrieve:

* OR2A definitions
* RCA playbook information
* Dataset schema documentation

through a Filesystem MCP Server.

---

# Architecture

User
↓
Streamlit Frontend
↓
FastAPI Backend
↓
LangGraph Agent
↓
LangChain Tools
├── sql_tool
├── rca_tool
├── city_summary_tool
└── docs_tool
↓
SQLite Database / MCP Server
↓
Groq LLM

---

# Technology Stack

## Agent Framework

* LangGraph
* LangChain

## LLM

* Groq
* Llama 3.3 70B Versatile

## Backend

* FastAPI
* Uvicorn

## Frontend

* Streamlit

## Database

* SQLite

## MCP

* Filesystem MCP Server

---

# MCP Integration

The project includes a Filesystem MCP Server integration.

Documentation is not embedded directly into prompts.

Instead, the agent retrieves documentation dynamically through MCP.

Flow:

User Question
→ LangGraph Agent
→ docs_tool
→ Filesystem MCP Server
→ Documentation Files
→ Response

Documentation accessed through MCP:

* order_ready_to_assignment.md
* quick_commerce_rca_logic.md
* quick_commerce_orders_gold.md

This keeps knowledge separate from model weights and demonstrates external tool integration through MCP.

---

# Tool Design

## sql_tool

Purpose:

Execute analytical queries against operational data stored in SQLite.

Responsibilities:

* Store metrics
* City metrics
* Hourly analysis
* Breach calculations

---

## rca_tool

Purpose:

Perform deterministic root-cause analysis.

Responsibilities:

* Demand spike detection
* Pileup detection
* Booking gap detection
* Utilization gap detection

---

## city_summary_tool

Purpose:

Generate city-level operational summaries.

Responsibilities:

* Store performance aggregation
* Breach rate analysis
* OR2A summaries

---

## docs_tool

Purpose:

Retrieve operational documentation through MCP.

Responsibilities:

* OR2A definitions
* RCA logic
* Dataset schema

---

# Dataset

Input data:

quick_commerce_orders_gold_20260422.csv

The dataset is automatically loaded into SQLite during application startup.

No manual database setup is required.

---

# Setup

## Install Dependencies

```bash
pip install -r requirements.txt
```

## Configure Environment Variables

Create:

```bash
.env
```

Add:

```env
GROQ_API_KEY=your_api_key
```

---

# Run

## Terminal 1

```bash
python main.py
```

Starts FastAPI backend on port 8000.

## Terminal 2

```bash
streamlit run app.py
```

Starts Streamlit frontend on port 8501.

Open:

http://localhost:8501

---

# Sample Questions

## City Analysis

How did Bangalore do on 2026-04-22?

---

## Store RCA

Why did STORE_101 underperform that day?

---

## Hourly Investigation

Walk me through the morning hours at STORE_101.

---

## Comparison

Compare STORE_101 and STORE_102.

---

## Documentation

What is OR2A?

Explain RCA logic.

---

# Design Decisions and Tradeoffs

## SQLite vs PostgreSQL

Choice:
SQLite

Reason:

* Zero setup
* Faster development
* Suitable for assignment dataset size

Tradeoff:

* Not ideal for large-scale production workloads

---

## Deterministic RCA Logic vs LLM Reasoning

Choice:
Deterministic Python implementation

Reason:

* Consistent outputs
* Exact threshold handling
* No hallucinations

Tradeoff:

* Less flexible when business rules change

---

## Filesystem MCP vs Complex Enterprise MCPs

Choice:
Filesystem MCP

Reason:

* Simple and reliable
* Fits documentation retrieval use case
* Demonstrates MCP integration clearly

Tradeoff:

* Less representative of enterprise integrations such as GitHub, Slack, or Notion

---

## Streamlit vs Custom React Frontend

Choice:
Streamlit

Reason:

* Faster delivery
* Minimal setup
* Ideal for demonstrating agent behavior

Tradeoff:

* Limited UI customization

---

# AI Tools Used

The following AI tools were used during development:

* Claude
* ChatGPT

Used for:

* Architecture brainstorming
* LangGraph setup
* FastAPI scaffolding
* MCP integration guidance
* Code generation assistance

All generated code was reviewed, tested, modified, and integrated manually.

---

# Future Improvements

* Session persistence
* Authentication
* PostgreSQL support
* Advanced analytics dashboards
* Additional MCP integrations
* Production deployment
* Historical trend analysis

---

# Assignment Requirement Mapping

✅ Multi-turn Chat Agent

✅ LangGraph

✅ LangChain

✅ MCP Integration

✅ SQL Tool

✅ RCA Tool

✅ Documentation Tool

✅ FastAPI Backend

✅ Streamlit Frontend

✅ CSV Data Source

✅ Conversational RCA

✅ Sample Question Support

✅ Additional Follow-up Questions

✅ Tradeoff Documentation

✅ AI Tool Disclosure
