"""
Reason-coded decision notification — fires a webhook to n8n after a reviewer
escalates or rejects an applicant, so n8n can route to the right template:

  reason_code   recipient (conceptually)   message
  sanctions     applicant                  generic rejection, no reason stated
  pep           senior reviewer, internal  enhanced review needed; applicant never messaged
  mismatch      applicant                  generic "please resubmit clearer documentation"

This app only sends the payload below — the routing/template logic lives in
the n8n workflow, built separately. N8N_REASON_WEBHOOK_URL is expected to be
unset until that workflow exists; until then this is a silent no-op so it
never blocks a real decision write.
"""
from app.services.webhook import post_webhook


def send_reason_notification(applicant_id: int, full_name: str, reason_code: str, recipient_email: str) -> None:
    post_webhook("N8N_REASON_WEBHOOK_URL", {
        "applicant_id": applicant_id,
        "full_name": full_name,
        "reason_code": reason_code,
        "recipient_email": recipient_email,
    })
