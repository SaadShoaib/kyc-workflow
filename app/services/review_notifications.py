"""
Review-needed notification — fires a webhook to n8n the moment the
LangGraph pipeline pauses at the review interrupt, so a reviewer finds out
there's something waiting on them instead of having to poll /review/{id}.

This is workflow #1, separate from the reason-routing webhook in
notifications.py (workflow #2, which fires later, after a decision is made).
The payload is self-contained (includes the risk score/confidence/
recommendation directly) rather than expecting n8n to call back into this
app for them — while running locally, an n8n cloud webhook can't reach a
localhost dev server anyway. GET /applicants/{id}/risk-brief exists if you
want n8n to re-fetch live once this app is deployed somewhere reachable.
"""
from app.services.webhook import post_webhook


def send_review_needed_notification(
    applicant_id: int,
    full_name: str,
    risk_score: float,
    confidence: str,
    recommendation: str,
    review_url: str,
) -> None:
    post_webhook("N8N_REVIEW_WEBHOOK_URL", {
        "applicant_id": applicant_id,
        "full_name": full_name,
        "risk_score": risk_score,
        "confidence": confidence,
        "recommendation": recommendation,
        "review_url": review_url,
    })
