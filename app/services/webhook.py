"""
Shared best-effort webhook delivery, used by both n8n notification workflows
(notifications.py = workflow #2, review_notifications.py = workflow #1).
"""
import os

import httpx


def post_webhook(url_env_var: str, payload: dict) -> None:
    """No-ops if the env var isn't set (workflow not built yet). A delivery
    failure must never block whatever real work already happened — callers
    fire this after their own DB write/commit, not before."""
    webhook_url = os.getenv(url_env_var)
    if not webhook_url:
        return

    try:
        httpx.post(webhook_url, json=payload, timeout=5)
    except httpx.HTTPError:
        pass
