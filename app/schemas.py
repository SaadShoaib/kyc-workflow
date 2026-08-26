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

from pydantic import BaseModel, Field


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

class ExtractedFields(BaseModel):
    name: str
    dob: str
    id_number: str
    address: str


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
    reviewer: str = "demo_reviewer"
    notes: Optional[str] = None
