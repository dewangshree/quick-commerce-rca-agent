"""Typed workflow state and the structured investigation report."""
from enum import Enum
from typing import Any, TypedDict

from pydantic import BaseModel, Field


class Intent(str, Enum):
    store_rca = "store_rca"
    hourly_drilldown = "hourly_drilldown"
    store_comparison = "store_comparison"
    city_summary = "city_summary"
    city_comparison = "city_comparison"
    sla_overview = "sla_overview"
    top_stores = "top_stores"
    documentation = "documentation"
    clarification_needed = "clarification_needed"
    out_of_scope = "out_of_scope"


ANALYTICS_INTENTS = {Intent.store_rca, Intent.hourly_drilldown, Intent.store_comparison, Intent.city_summary,
                     Intent.city_comparison, Intent.sla_overview, Intent.top_stores}
RCA_INTENTS = {Intent.store_rca, Intent.hourly_drilldown}


class Entities(BaseModel):
    stores: list[str] = []
    cities: list[str] = []      # normalized names
    date: str | None = None
    date_assumed: bool = False
    hour_from: int | None = None
    hour_to: int | None = None
    hour_label: str | None = None
    used_context: list[str] = []


class InvestigationState(TypedDict, total=False):
    investigation_id: str
    question: str
    history: list[dict]
    entities: dict
    intent: str
    intent_source: str
    needs_retrieval: bool
    analytics: dict
    analytics_errors: list[dict]
    retrieval: dict
    evidence: list[dict]
    factors: list[dict]
    validation: dict
    report: dict
    llm_usage: list[dict]
    errors: list[str]
    timings: dict
    status: str


class EvidenceItem(BaseModel):
    id: str                     # M1.. metrics, D1.. documents, H1.. historical incidents
    kind: str                   # metric | document | incident | data_quality
    summary: str
    source: str                 # table/operation or document path
    details: dict[str, Any] = {}


class Citation(BaseModel):
    id: str
    source_path: str
    title: str
    heading: str | None
    doc_type: str
    is_sample: bool
    excerpt: str


class ObservedMetric(BaseModel):
    name: str
    value: float | int | str | None
    unit: str | None = None
    evidence_id: str


class ContributingFactor(BaseModel):
    factor: str
    hours: list[int]
    breached_orders_in_flagged_hours: int
    share_of_breached_orders: float | None
    evidence_ids: list[str]
    citation_ids: list[str] = []
    assessment: str


class CorrectiveAction(BaseModel):
    action: str
    addresses: str
    citation_ids: list[str] = []
    source_note: str


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class Confidence(BaseModel):
    level: str                  # high | medium | low | n/a
    limitations: list[str]


class LLMInfo(BaseModel):
    used: bool
    model: str | None = None
    narrative_grounded: bool | None = None
    note: str | None = None
    usage: list[dict] = []


class InvestigationReport(BaseModel):
    investigation_id: str
    question: str
    intent: str
    intent_source: str
    status: str
    scope: dict[str, Any]
    answer_markdown: str
    narrative: str | None = None
    observed_metrics: list[ObservedMetric] = []
    contributing_factors: list[ContributingFactor] = []
    evidence: list[EvidenceItem] = []
    citations: list[Citation] = []
    missing_data: list[str] = []
    confidence: Confidence
    next_steps: list[str] = []
    corrective_actions: list[CorrectiveAction] = []
    validation: list[ValidationCheck] = []
    llm: LLMInfo
    data: dict[str, Any] = Field(default_factory=dict)
    timings_ms: dict[str, float] = {}
    errors: list[str] = []
    disclaimer: str = ("Contributing factors are rule-based co-occurrences in aggregated store-hour data, "
                       "not proven causes. Verify against order-level and operational logs.")
