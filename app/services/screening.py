"""
Sanctions / PEP screening — deliberately deterministic, no LLM call.

Fuzzy name matching against a watchlist doesn't need a language model — it
needs a similarity metric and a threshold. Reaching for an LLM here would be
slower, more expensive, and harder to audit than rapidfuzz. This is the
"know when not to use AI" example worth pointing at directly in a writeup.

data/ofac_sdn_sample.csv ships with a handful of made-up names for the demo.
The real OFAC SDN list is a free public CSV — swap the path once you're
past the demo stage: https://sanctionslist.ofac.treas.gov/Home/SdnList
"""
import csv
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models import Applicant, ScreeningResult
from app.schemas import ScreeningHit

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SANCTIONS_LIST_PATH = DATA_DIR / "ofac_sdn_sample.csv"
PEP_LIST_PATH = DATA_DIR / "pep_list_mock.csv"

FUZZY_MATCH_THRESHOLD = 85  # 0-100 rapidfuzz similarity score; tune based on false-positive rate


@lru_cache(maxsize=2)
def _load_names(path: Path) -> tuple[str, ...]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return tuple(row["name"].strip() for row in reader if row.get("name"))


def _fuzzy_matches(name: str, candidates: tuple[str, ...], source: str) -> list[ScreeningHit]:
    hits = []
    for candidate in candidates:
        score = fuzz.token_sort_ratio(name.lower(), candidate.lower())
        if score >= FUZZY_MATCH_THRESHOLD:
            hits.append(ScreeningHit(matched_name=candidate, score=round(score, 1), source=source))
    return sorted(hits, key=lambda h: h.score, reverse=True)


def screen_applicant(db: Session, applicant_id: int) -> ScreeningResult:
    applicant = db.query(Applicant).get(applicant_id)
    if applicant is None:
        raise ValueError(f"Applicant {applicant_id} not found")

    sanctions_names = _load_names(SANCTIONS_LIST_PATH)
    pep_names = _load_names(PEP_LIST_PATH)

    sanctions_hits = _fuzzy_matches(applicant.full_name, sanctions_names, "OFAC SDN (sample)")
    pep_hits = _fuzzy_matches(applicant.full_name, pep_names, "Mock PEP list")

    existing = db.query(ScreeningResult).filter_by(applicant_id=applicant.id).first()
    if existing:
        db.delete(existing)
        db.flush()

    result = ScreeningResult(
        applicant_id=applicant.id,
        sanctions_hits=[h.model_dump() for h in sanctions_hits],
        pep_hits=[h.model_dump() for h in pep_hits],
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
