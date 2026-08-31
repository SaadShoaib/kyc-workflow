"""
Pydantic schemas.

Two different jobs happen here, worth telling apart:
  1. Normal API request/response validation (ApplicantCreate, ApplicantOut, DecisionIn)
  2. Schemas that double as the structured-output contract for Claude
     (ExtractedFields, RiskBriefOut) — these get passed almost directly into
     the tool definitions in services/extraction.py and services/synthesis.py,
     so the model's output is validated the same way a normal request would be.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ApplicantCreate(BaseModel):
    full_name: str
    dob: str
    address: str
    id_number: str
    id_image_path: str


class ApplicantOut(BaseModel):
    id: int
    full_name: str
    dob: str
    address: str
    id_number: str
    status: str

    class Config:
        from_attributes = True


# --- Extraction ---

_WRAPPING_QUOTE_PAIRS = [("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”")]


def _strip_wrapping_quotes(value: str) -> str:
    """Some ID card renders print a field like 'Ayesha Raza' with decorative
    quotes that aren't part of the actual value — left in, they'd cause a
    false mismatch against the un-quoted form value."""
    stripped = value.strip()
    for open_q, close_q in _WRAPPING_QUOTE_PAIRS:
        if len(stripped) >= 2 and stripped[0] == open_q and stripped[-1] == close_q:
            return stripped[1:-1].strip()
    return stripped


class ExtractedFields(BaseModel):
    name: str
    dob: str
    id_number: str
    address: str

    @field_validator("name", "dob", "id_number", "address")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return _strip_wrapping_quotes(v)


class Mismatch(BaseModel):
    field: str
    form_value: str
    doc_value: str


# --- Screening ---

class ScreeningHit(BaseModel):
    matched_name: str
    score: float
    source: str


# --- Risk synthesis ---

class EvidencePoint(BaseModel):
    point: str
    source: Literal["extraction", "screening"]


class RiskBriefOut(BaseModel):
    score: float = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    recommendation: Literal["approve", "manual_review", "reject"]
    evidence: List[EvidencePoint]


# --- Decision ---

class DecisionIn(BaseModel):
    action: Literal["approved", "escalated", "rejected"]
    reason_code: Optional[Literal["mismatch", "sanctions", "pep", "other"]] = None
    reviewer: str = "demo_reviewer"
    notes: Optional[str] = None

    @model_validator(mode="after")
    def reason_required_for_escalate_or_reject(self):
        if self.action in ("escalated", "rejected") and self.reason_code is None:
            raise ValueError("reason_code is required when escalating or rejecting")
        return self
