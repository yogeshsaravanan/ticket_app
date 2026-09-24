"""Pydantic request/response models for the API.

frontend/src/api.ts mirrors `Ticket`; keep the two in sync.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

MAX_CHARS = 5000

Urgency = Literal["critical", "high", "normal", "low"]
Status = Literal["queued", "processing", "done"]
Decision = Literal["action", "escalate"]
Category = Literal["billing_status", "password_reset", "service_restart"]


class TicketIn(BaseModel):
    """Body of POST /api/tickets."""

    text: str = Field(..., max_length=MAX_CHARS)

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("ticket text is required")
        return v


class Ticket(BaseModel):
    """A ticket as returned by the API, at any stage of processing."""

    id: str
    text: str
    urgency: Urgency
    urgency_reason: str
    effective_urgency: Urgency
    status: Status
    queue_position: Optional[int]
    processed_order: Optional[int]
    submitted_at: float
    started_at: Optional[float]
    completed_at: Optional[float]
    # filled in once status == "done"
    decision: Optional[Decision]
    category: Optional[Category]
    reason: Optional[str]
    action: Optional[str]
    action_result: Optional[str]
    scores: Optional[dict[str, int]]
    matched: Optional[dict[str, list[str]]]


class TicketList(BaseModel):
    """Response of GET /api/tickets."""

    tickets: list[Ticket]
