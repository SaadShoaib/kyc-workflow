"""
SQLAlchemy models.

One table per pipeline stage (Applicant, ExtractionResult, ScreeningResult,
RiskBrief) plus a Decision table. Keeping each stage's output in its own
table — rather than one wide "applicant" row that gets mutated — means every
stage's output is preserved as a record, which is what makes the provenance
trail possible later: you can always point at exactly which row produced
which claim in the risk brief.

Decision is the only table that ever changes an applicant's status, and it's
only ever written by the /decision endpoint in main.py.
"""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship

from app.db import Base


class Applicant(Base):
    __tablename__ = "applicants"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    dob = Column(String, nullable=False)          # form-submitted DOB, YYYY-MM-DD
    address = Column(String, nullable=False)
    id_number = Column(String, nullable=False)
    id_image_path = Column(String, nullable=False)
    status = Column(String, default="pending")     # pending | approved | escalated | rejected
    created_at = Column(DateTime, default=datetime.utcnow)

    extraction = relationship("ExtractionResult", back_populates="applicant", uselist=False)
    screening = relationship("ScreeningResult", back_populates="applicant", uselist=False)
    risk_brief = relationship("RiskBrief", back_populates="applicant", uselist=False)
    decision = relationship("Decision", back_populates="applicant", uselist=False)


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), unique=True)

    extracted_name = Column(String)
    extracted_dob = Column(String)
    extracted_id_number = Column(String)
    extracted_address = Column(String)

    mismatches = Column(JSON)          # [{"field", "form_value", "doc_value"}, ...]
    raw_model_output = Column(Text)    # full JSON Claude returned — the provenance record
    created_at = Column(DateTime, default=datetime.utcnow)

    applicant = relationship("Applicant", back_populates="extraction")


class ScreeningResult(Base):
    __tablename__ = "screening_results"

    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), unique=True)

    sanctions_hits = Column(JSON)      # [{"matched_name", "score", "source"}, ...]
    pep_hits = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    applicant = relationship("Applicant", back_populates="screening")


class RiskBrief(Base):
    __tablename__ = "risk_briefs"

    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), unique=True)

    score = Column(Float)              # 0-100, higher = riskier
    confidence = Column(String)        # low | medium | high
    recommendation = Column(String)    # approve | manual_review | reject — a SUGGESTION only
    evidence = Column(JSON)            # [{"point", "source": "extraction"|"screening"}, ...]
    raw_model_output = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    applicant = relationship("Applicant", back_populates="risk_brief")


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), unique=True)

    action = Column(String, nullable=False)   # approved | escalated | rejected
    reason_code = Column(String, nullable=True)  # mismatch | sanctions | pep | other — required for escalated/rejected
    reviewer = Column(String, default="demo_reviewer")
    notes = Column(String, nullable=True)
    decided_at = Column(DateTime, default=datetime.utcnow)

    applicant = relationship("Applicant", back_populates="decision")
