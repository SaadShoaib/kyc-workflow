"""
FastAPI app.

Five things this app can do:
  GET  /applicants                       list everyone in the pipeline
  POST /applicants/{id}/extract          run document extraction
  POST /applicants/{id}/screen           run sanctions/PEP screening
  POST /applicants/{id}/score            run risk synthesis
  GET  /review/{id}                      the human reviewer screen
  POST /applicants/{id}/decision         the ONLY endpoint that changes status

Run with: uvicorn app.main:app --reload
Then visit http://127.0.0.1:8000/docs for interactive API docs, or
http://127.0.0.1:8000/review/1 for the reviewer screen once an applicant
has been through extract/screen/score.
"""
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.models import Applicant, Decision
from app.schemas import ApplicantOut, DecisionIn
from app.services.extraction import extract_applicant
from app.services.screening import screen_applicant
from app.services.synthesis import synthesize_risk

Base.metadata.create_all(bind=engine)

app = FastAPI(title="KYC Verification Copilot")
templates = Jinja2Templates(directory="app/templates")


@app.get("/applicants", response_model=list[ApplicantOut])
def list_applicants(db: Session = Depends(get_db)):
    return db.query(Applicant).all()


@app.post("/applicants/{applicant_id}/extract")
def run_extraction(applicant_id: int, db: Session = Depends(get_db)):
    try:
        result = extract_applicant(db, applicant_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"applicant_id": applicant_id, "mismatches": result.mismatches}


@app.post("/applicants/{applicant_id}/screen")
def run_screening(applicant_id: int, db: Session = Depends(get_db)):
    try:
        result = screen_applicant(db, applicant_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "applicant_id": applicant_id,
        "sanctions_hits": result.sanctions_hits,
        "pep_hits": result.pep_hits,
    }


@app.post("/applicants/{applicant_id}/score")
def run_synthesis(applicant_id: int, db: Session = Depends(get_db)):
    try:
        result = synthesize_risk(db, applicant_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "applicant_id": applicant_id,
        "score": result.score,
        "confidence": result.confidence,
        "recommendation": result.recommendation,
        "evidence": result.evidence,
    }


@app.get("/applicants/{applicant_id}/id-image")
def id_image(applicant_id: int, db: Session = Depends(get_db)):
    applicant = db.query(Applicant).get(applicant_id)
    if applicant is None:
        raise HTTPException(status_code=404, detail="Applicant not found")
    if not os.path.isfile(applicant.id_image_path):
        raise HTTPException(status_code=404, detail="ID image file not found")
    return FileResponse(applicant.id_image_path)


@app.get("/review/{applicant_id}", response_class=HTMLResponse)
def review_screen(applicant_id: int, request: Request, db: Session = Depends(get_db)):
    applicant = db.query(Applicant).get(applicant_id)
    if applicant is None:
        raise HTTPException(status_code=404, detail="Applicant not found")
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "applicant": applicant,
            "extraction": applicant.extraction,
            "screening": applicant.screening,
            "risk_brief": applicant.risk_brief,
        },
    )


@app.post("/applicants/{applicant_id}/decision")
def submit_decision(
    applicant_id: int,
    action: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    applicant = db.query(Applicant).get(applicant_id)
    if applicant is None:
        raise HTTPException(status_code=404, detail="Applicant not found")

    # Validates that `action` is one of the allowed values before anything touches the DB.
    try:
        decision_input = DecisionIn(action=action, notes=notes or None)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())

    existing = db.query(Decision).filter_by(applicant_id=applicant.id).first()
    if existing:
        db.delete(existing)
        db.flush()

    # This is the ONLY place in the codebase that changes applicant.status.
    decision = Decision(
        applicant_id=applicant.id,
        action=decision_input.action,
        reviewer=decision_input.reviewer,
        notes=decision_input.notes,
    )
    applicant.status = decision_input.action
    db.add(decision)
    db.commit()

    return RedirectResponse(url=f"/review/{applicant_id}", status_code=303)
