# KYC Verification Copilot — Build Plan

A synthetic-data KYC intake pipeline with a hard human-in-the-loop gate. Built to double as a portfolio piece for both an "applied AI engineer, build a prototype" job application and a role focused on agent evals/observability.

## Architecture

```
Applicant submission (form + ID doc + selfie)
        |
Document extraction (OCR / consistency checks)
        |
Sanctions / PEP screening (name match against watchlists)
        |
Risk synthesizer (score, evidence, confidence)
        |
Human reviewer (approve / escalate / reject)
        |
Decision + audit log (who decided, based on what)
```

The AI does real work at extraction and synthesis. Only a human can change an applicant's status — that boundary is the whole point and should be enforced in code, not just the UI.

## Tech stack decisions

- **FastAPI + Python** — backend and all reasoning logic. Chosen deliberately over the usual JS stack to build resume-relevant Python depth.
- **SQLite** — zero-config, won't break the demo. Swap to Postgres/Supabase later only if a "production-ready" story is needed.
- **Claude vision** — for document extraction. A genuine multimodal tool-use call, more impressive and less brittle than classic OCR (Tesseract) against templated synthetic ID images.
- **rapidfuzz + OFAC SDN list (public CSV)** — sanctions/PEP screening. Deterministic, no LLM call — a deliberate "know when not to use AI" example worth calling out explicitly in the writeup/video.
- **LangGraph** — added *after* the plain-function pipeline works, to handle the pause/resume around the human review step (`interrupt` + `SqliteSaver` checkpointer). Not used for anything that doesn't need a graph — LangChain is deliberately skipped here since there's no retrieval/knowledge-base component in this project.
- **n8n** — orchestration and notification only: fires on a LangGraph interrupt, checks the risk score, and pings a reviewer (Slack/email) with a link into the review UI. Kept separate from the reasoning logic on purpose, to show judgment about when a workflow tool is the right call vs. writing code.
- **Jinja2 + HTMX** — reviewer UI. No separate frontend framework needed for a demo this size.

## Build order

### 1. Set up the project scaffold
```
mkdir kyc-copilot && cd kyc-copilot
python -m venv venv && source venv/bin/activate
pip install fastapi uvicorn sqlalchemy anthropic rapidfuzz python-multipart jinja2 pillow python-dotenv
```
Folder layout:
- `app/main.py`
- `app/models.py` — SQLAlchemy: `Applicant`, `ExtractionResult`, `ScreeningResult`, `RiskBrief`, `Decision`
- `app/db.py` — SQLite setup

Get `uvicorn app.main:app --reload` returning a hello-world before writing any logic.

### 2. Generate the synthetic dataset
Write a script that creates 8–10 applicant records as JSON (name, DOB, address, ID number). Use PIL to draw those details onto a simple ID-card template for 3–4 of them, so there are real images to feed into extraction.

Bake in the test cases now:
- A name that fuzzy-matches a sanctions entry
- A DOB mismatch between form and doc
- One genuinely ambiguous case that shouldn't get an easy answer either way

Never use real personal data or real documents — synthetic only.

### 3. Build extraction as a plain function
`extract_applicant(applicant_id)`:
- Sends the ID image to Claude as a vision message with a structured output schema (name, DOB, ID number, address)
- Diffs the result against the form data field by field
- Returns the mismatches, logging which field came from which source (this is the provenance trail)

Test it directly in a script before wiring it to any endpoint.

### 4. Build sanctions screening as a plain function
`screen_applicant(applicant_id)`:
- Load the OFAC SDN CSV into memory once at startup
- Fuzzy-match the applicant's name against it with rapidfuzz, using a distance threshold
- Also check against a small mock PEP list written by hand

No LLM call — deterministic and fast, on purpose.

### 5. Build the risk synthesizer as a plain function
`synthesize_risk(applicant_id)`:
- One Claude call combining the extraction mismatches and screening hits
- Returns a structured brief: score, an evidence list citing which check produced each point, and a confidence level
- Recommendation only — this function must never write a final decision to the database

### 6. Wire it into FastAPI and build the reviewer UI
- Wrap the three functions in endpoints: `/extract`, `/screen`, `/score`
- Build the review screen with Jinja2 templates + a little HTMX
- Show the applicant, the risk brief, and the raw evidence together
- Approve / escalate / reject buttons post to `/applicants/{id}/decision`
- That endpoint is the **only** place in the codebase allowed to change status — enforce this in code

### 7. Refactor orchestration into LangGraph, add n8n for notification
Once steps 3–5 work correctly on the synthetic set:
- Refactor the extract → screen → score sequence into a LangGraph graph with a `SqliteSaver` checkpointer
- Add an interrupt node before the review step that pauses and persists state until a human decides
- Build an n8n workflow: webhook fires on the interrupt → HTTP Request node checks the risk score → Slack/email node notifies a reviewer with a link into the UI

### 8. Build the eval harness, deploy, and record
- Script that runs the full synthetic set through the pipeline and checks output against expected labels, saved as a pass/fail table
- Optional: wire the eval script into a GitHub Action so it visibly gates changes
- Deploy FastAPI + SQLite on Railway or Render
- Spin up n8n on their free cloud trial (avoid self-hosting for the demo)
- Script the 5-minute video before recording: problem framing → live run on one clean and one flagged applicant → explicit statement of the human/AI boundary → tradeoffs and failure handling → what you'd do next at scale
- Write a one-page PDF stating assumptions, linking the deployed demo and video

## Things to keep front-of-mind while building

- **Uncertain cases route to a human by default** — never auto-approve or auto-reject on low confidence.
- **"Background check" = sanctions/PEP screening**, not a real criminal background check — call it that explicitly, it's more accurate and more credible.
- **Every claim in the risk brief should trace back to a specific check** — this is the provenance/lineage piece that matters for both applications.
- **The eval table is the single artifact that does the most work** — it proves judgment for a build-a-prototype application and proves eval discipline for an evals/observability-focused one.
