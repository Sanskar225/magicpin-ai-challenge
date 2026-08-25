"""
Data schemas and models for the Vera Message Engine.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


# =============================================================================
# Core Domain Dataclasses
# =============================================================================

@dataclass
class CategoryContext:
    slug: str
    display_name: str = ""
    voice: Dict[str, Any] = field(default_factory=dict)
    offer_catalog: List[Dict[str, Any]] = field(default_factory=list)
    peer_stats: Dict[str, Any] = field(default_factory=dict)
    digest: List[Dict[str, Any]] = field(default_factory=list)
    patient_content_library: List[Dict[str, Any]] = field(default_factory=list)
    seasonal_beats: List[Dict[str, Any]] = field(default_factory=list)
    trend_signals: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class MerchantContext:
    merchant_id: str
    category_slug: str
    identity: Dict[str, Any] = field(default_factory=dict)
    subscription: Dict[str, Any] = field(default_factory=dict)
    performance: Dict[str, Any] = field(default_factory=dict)
    offers: List[Dict[str, Any]] = field(default_factory=list)
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    customer_aggregate: Dict[str, Any] = field(default_factory=dict)
    signals: List[str] = field(default_factory=list)


@dataclass
class TriggerContext:
    id: str
    scope: Literal["merchant", "customer"]
    kind: str
    source: Literal["external", "internal"]
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    urgency: int = 1
    suppression_key: str = ""
    expires_at: Optional[str] = None


@dataclass
class CustomerContext:
    customer_id: str
    merchant_id: str
    identity: Dict[str, Any] = field(default_factory=dict)
    relationship: Dict[str, Any] = field(default_factory=dict)
    state: str = "active"
    preferences: Dict[str, Any] = field(default_factory=dict)
    consent: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComposedMessage:
    body: str
    cta: Literal["open_ended", "binary", "none"]
    send_as: Literal["vera", "merchant_on_behalf"]
    suppression_key: str
    rationale: str
    template_name: Optional[str] = None
    template_params: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "body": self.body,
            "cta": self.cta,
            "send_as": self.send_as,
            "suppression_key": self.suppression_key,
            "rationale": self.rationale,
        }
        if self.template_name is not None:
            d["template_name"] = self.template_name
        if self.template_params is not None:
            d["template_params"] = self.template_params
        return d


@dataclass
class ReplyAction:
    action: Literal["send", "wait", "end"]
    body: Optional[str] = None
    cta: Optional[str] = None
    rationale: str = ""
    wait_seconds: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"action": self.action, "rationale": self.rationale}
        if self.body is not None:
            d["body"] = self.body
        if self.cta is not None:
            d["cta"] = self.cta
        if self.wait_seconds is not None:
            d["wait_seconds"] = self.wait_seconds
        return d


# =============================================================================
# API Request / Response Pydantic Models
# =============================================================================

class ContextPushRequest(BaseModel):
    scope: Literal["category", "merchant", "customer", "trigger"]
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class ContextPushResponse(BaseModel):
    accepted: bool
    ack_id: str
    stored_at: str


class ContextConflictResponse(BaseModel):
    accepted: bool = False
    reason: str = "stale_version"
    current_version: int


class ActionItem(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    send_as: Literal["vera", "merchant_on_behalf"]
    trigger_id: str
    template_name: Optional[str] = None
    template_params: Optional[List[str]] = None
    body: str
    cta: Literal["open_ended", "binary", "none"]
    suppression_key: str
    rationale: str


class TickRequest(BaseModel):
    now: Optional[str] = None
    available_triggers: List[str] = Field(default_factory=list)


class TickResponse(BaseModel):
    actions: List[ActionItem] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str
    received_at: Optional[str] = None
    turn_number: int = 1


class ReplyResponse(BaseModel):
    action: Literal["send", "wait", "end"]
    body: Optional[str] = None
    cta: Optional[str] = None
    rationale: str
    wait_seconds: Optional[int] = None


class HealthzResponse(BaseModel):
    status: str = "ok"
    uptime_seconds: int
    contexts_loaded: Dict[str, int]


class MetadataResponse(BaseModel):
    team_name: str
    team_members: List[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str
