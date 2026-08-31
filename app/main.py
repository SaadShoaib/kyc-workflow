"""
FastAPI app.

Eight things this app can do:
  GET  /applicants                       list everyone in the pipeline
  POST /applicants/{id}/run              run extract->screen->score via LangGraph, pause at review
  POST /applicants/{id}/extract          run document extraction directly
  POST /applicants/{id}/screen           run sanctions/PEP screening directly
  POST /applicants/{id}/score            run risk synthesis directly
  GET  /applicants/{id}/risk-brief       read the existing risk brief (no LLM call, no write)
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

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from langgraph.types import Command

from app.db import Base, SessionLocal, engine, get_db
from app.graph import graph, has_crashed, is_paused_at, shutdown_graph, thread_config
from app.models import Applicant, Decision, RiskBrief
from app.schemas import ApplicantOut, DecisionIn
from app.services.extraction import extract_applicant
from app.services.notifications import send_reason_notification
from app.services.review_notifications import send_review_needed_notification
from app.services.screening import screen_applicant
from app.services.synthesis import synthesize_risk

Base.metadata.create_all(bind=engine)

app = FastAPI(title="KYC Verification Copilot")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("shutdown")
def _close_graph():
    shutdown_graph()


def _notify_if_newly_paused_at_review(applicant_id: int, config: dict) -> None:
    """Fires the review-needed webhook (workflow #1) if this invoke() call
    just brought the graph to a fresh stop at the review interrupt.
    /run's own idempotency logic guarantees this only ever happens once per
    applicant — a second /run call on an already-paused thread returns
    early and never reaches here, so this can't double-fire.

    Runs as a background task: nothing in the HTTP response depends on this
    webhook's outcome, so it shouldn't add its own latency (up to the 5s
    timeout) to the request the caller is waiting on. Opens its own DB
    session since it runs after the request-scoped session may be gone."""
    if not is_paused_at(graph.get_state(config), "review"):
        return

    db = SessionLocal()
    try:
        applicant = db.query(Applicant).get(applicant_id)
        risk_brief = db.query(RiskBrief).filter_by(applicant_id=applicant_id).first()
        if applicant is None or risk_brief is None:
            return

        base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")
        send_review_needed_notification(
            applicant_id=applicant_id,
            full_name=applicant.full_name,
            risk_score=risk_brief.score,
            confidence=risk_brief.confidence,
            recommendation=risk_brief.recommendation,
            review_url=f"{base_url}/review/{applicant_id}",
        )
    finally:
        db.close()


@app.post("/applicants/{applicant_id}/run")
def run_pipeline(applicant_id: int, background_tasks: BackgroundTasks):
    """Runs extract -> screen -> score automatically, then pauses at review.
    Safe to call repeatedly:
      - not started yet -> starts a fresh run from the top
      - genuinely paused at the review interrupt -> reports that, no-op
      - a previous attempt crashed mid-node (e.g. a transient LLM API error)
        -> resumes from the checkpoint, retrying the failed node, instead of
           either restarting from the top or getting silently stuck
    """
    config = thread_config(applicant_id)
    existing_state = graph.get_state(config)

    if not existing_state.next:
        graph.invoke({"applicant_id": applicant_id}, config=config)
        background_tasks.add_task(_notify_if_newly_paused_at_review, applicant_id, config)
        return {"applicant_id": applicant_id, "status": "started"}

    if has_crashed(existing_state):
        graph.invoke(None, config=config)
        background_tasks.add_task(_notify_if_newly_paused_at_review, applicant_id, config)
        return {"applicant_id": applicant_id, "status": "retried_after_error"}

    # Already paused at the interrupt, or a run is currently in flight
    # (e.g. a concurrent request) — either way, don't invoke again.
    return {
        "applicant_id": applicant_id,
        "status": "already_running_or_paused",
        "paused_at": existing_state.next,
    }


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


@app.get("/applicants/{applicant_id}/risk-brief")
def get_risk_brief(applicant_id: int, db: Session = Depends(get_db)):
    """Pure read of the existing risk brief — no LLM call, no write. Unlike
    POST /score, safe to call as often as you like (e.g. from an n8n
    HTTP Request node) without re-running synthesis or overwriting data."""
    result = db.query(RiskBrief).filter_by(applicant_id=applicant_id).first()
    if result is None:
        raise HTTPException(status_code=404, detail="No risk brief yet for this applicant")
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
    background_tasks: BackgroundTasks,
    action: str = Form(...),
    reason_code: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    applicant = db.query(Applicant).get(applicant_id)
    if applicant is None:
        raise HTTPException(status_code=404, detail="Applicant not found")

    # Validates that `action` is one of the allowed values, and that
    # reason_code is present when escalating or rejecting, before anything
    # touches the DB.
    try:
        decision_input = DecisionIn(action=action, reason_code=reason_code or None, notes=notes or None)
    except ValidationError as e:
        # include_context=False: e.errors() otherwise embeds the raw ValueError
        # object from our custom validator in ctx.error, which isn't JSON
        # serializable and would turn this into an unhandled 500.
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

    existing = db.query(Decision).filter_by(applicant_id=applicant.id).first()
    if existing:
        db.delete(existing)
        db.flush()

    # This is the ONLY place in the codebase that changes applicant.status.
    decision = Decision(
        applicant_id=applicant.id,
        action=decision_input.action,
        reason_code=decision_input.reason_code,
        reviewer=decision_input.reviewer,
        notes=decision_input.notes,
    )
    applicant.status = decision_input.action
    db.add(decision)
    db.commit()

    # Fires the reason-routing webhook (workflow #2) for escalate/reject only.
    # Best-effort — see notifications.py; runs after the response is sent so
    # a slow/unresponsive n8n webhook can't add latency to the reviewer's
    # click, and can't block the decision that was already committed above.
    if decision_input.action in ("escalated", "rejected"):
        background_tasks.add_task(
            send_reason_notification,
            applicant_id=applicant.id,
            full_name=applicant.full_name,
            reason_code=decision_input.reason_code,
            recipient_email=os.getenv("NOTIFICATION_RECIPIENT_EMAIL", "dev@example.com"),
        )

    # Resumes the paused graph so the checkpoint shows the thread completed.
    # This is bookkeeping only — the status write above already happened;
    # the graph gains no new authority here.
    config = thread_config(applicant_id)
    if graph.get_state(config).next:
        graph.invoke(
            Command(resume={"action": decision_input.action, "notes": decision_input.notes}),
            config=config,
        )

    return RedirectResponse(url=f"/review/{applicant_id}", status_code=303)
