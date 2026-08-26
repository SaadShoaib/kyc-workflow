# KYC Verification Copilot

A synthetic-data KYC intake pipeline with a hard human-in-the-loop gate.
See `build-plan.md` for the full design rationale.

## Current status

**Built and tested:** the core pipeline — document extraction, sanctions/PEP
screening, risk synthesis, the reviewer UI, and the decision gate. All of it
runs as plain FastAPI endpoints you call **manually and in order**.

**Not built yet:**
- **LangGraph orchestration** — right now nothing chains `/extract` →
  `/screen` → `/score` automatically, and nothing pauses execution for a
  human decision. That's the next step: wrapping these three functions in a
  graph with an interrupt node before review.
- **n8n workflow** — no notification step exists yet. This will sit outside
  the app entirely and fire when the LangGraph interrupt happens.
- **Eval run against a real model** — `scripts/run_evals.py` is written and
  tested for its structure, but hasn't been run against real Claude or
  Ollama output yet. That's the first thing to do once you're set up.

## Setup

```
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Then choose a provider in `.env`:

### Option A — Anthropic (paid, best quality for your final demo)
Set `LLM_PROVIDER=anthropic` and add your `ANTHROPIC_API_KEY`.

### Option B — Ollama (free, runs on your machine, good for iterating)
Set `LLM_PROVIDER=ollama`, then pull the two local models used by the pipeline:
```
ollama pull llama3.2-vision   # used for document extraction — needs vision
ollama pull llama3.1          # used for risk synthesis — text only
```
If your machine is lower-powered, swap `llama3.2-vision` for `moondream`
(faster, less accurate) by setting `OLLAMA_VISION_MODEL=moondream` in `.env`.

Make sure Ollama itself is running (`ollama serve`, or just open the Ollama
app) before you hit `/extract` or `/score` — if it's not, you'll get a clear
error telling you what to do rather than a raw stack trace.

## Run it

```
python scripts/generate_dataset.py    # creates 8 synthetic applicants + ID card images
uvicorn app.main:app --reload
```

Then:
- `http://127.0.0.1:8000/docs` — interactive API docs
- `http://127.0.0.1:8000/applicants` — list all applicants and their IDs
- `http://127.0.0.1:8000/review/{id}` — the reviewer screen

## Try the pipeline on one applicant

```
curl -X POST http://127.0.0.1:8000/applicants/6/extract
curl -X POST http://127.0.0.1:8000/applicants/6/screen
curl -X POST http://127.0.0.1:8000/applicants/6/score
```
Then open `http://127.0.0.1:8000/review/6` to see the risk brief and approve/escalate/reject.

Applicant 6 (Vladimir Petrescu) is built to trip the sanctions screen — a
good first test since `/screen` alone catches it, no LLM call needed:
```
curl -X POST http://127.0.0.1:8000/applicants/6/screen
```

## Run the evals

```
python scripts/run_evals.py
```
Runs the full pipeline over every synthetic applicant and checks the risk
synthesizer's recommendation against an expected label. Exits non-zero on
any failure, so it can gate a CI build. This is the first real end-to-end
test of extraction and synthesis together, against whichever provider you've
set in `.env`.

## What's verified vs. what's on you

Already tested (in a sandboxed environment, not on your machine — see below):
- Dataset generation (PIL-rendered ID cards)
- Sanctions/PEP screening end to end, both providers don't affect this path
- The reviewer UI and decision endpoint, including validation and error handling
- The Ollama and Anthropic code paths import cleanly either way
- Ollama's parsing logic, verified against a mocked model response — the
  JSON-schema-to-Pydantic path works correctly
- The "Ollama not running" error message is clear rather than a stack trace

Not yet run against a real model, since that requires either your API key or
your local Ollama server, neither of which exist outside your machine:
- `/extract` and `/score` against real Claude or real Ollama output
- `scripts/run_evals.py` against real output

Run `scripts/run_evals.py` once you're set up — that's your first real test
of the two AI-backed endpoints together.

## Next steps (see build-plan.md for detail)

1. Refactor the extract → screen → score sequence into a LangGraph graph
   with an interrupt node before human review.
2. Wire an n8n workflow to notify a reviewer when the graph interrupts.
3. Deploy (Railway/Render for the app, n8n cloud trial for the workflow)
   and record the walkthrough video.

