"""
Risk synthesis — the one place extraction and screening results get combined
into a brief a human can act on.

Three things are enforced deliberately here, not just by convention:
  1. The prompt explicitly tells the model every evidence point must trace
     back to the extraction or screening data — it should not invent findings.
  2. RiskBriefOut (schemas.py) has no field for a final decision, only a
     `recommendation`. There is no code path from this function to
     Applicant.status. That only happens in the /decision endpoint.
  3. score/confidence/recommendation are computed deterministically in
     _compute_risk, not left to the model — an LLM asked to freely pick a
     0-100 number alongside a categorical recommendation can produce
     self-contradictory output (e.g. score=0 with recommendation="reject").
     The model still writes the evidence narrative; it just doesn't get a
     vote on the number.

Supports four providers, switched with the LLM_PROVIDER env var — see
extraction.py for the same pattern. Ollama and Gemini here are text-only, so
any decent local instruct model works for Ollama, e.g.:
    ollama pull llama3.1
    ollama pull qwen2.5
"""
import json
import os

from sqlalchemy.orm import Session

from app.models import Applicant, ExtractionResult, RiskBrief, ScreeningResult
from app.schemas import RiskBriefOut

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic", "gemini", "mistral", or "ollama"

ANTHROPIC_MODEL = "claude-sonnet-5"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
OLLAMA_TEXT_MODEL = os.getenv("OLLAMA_TEXT_MODEL", "llama3.1")

SYSTEM_PROMPT = (
    "You are assisting a human KYC reviewer. You never make the final call — "
    "you only produce a recommendation for them to act on. Every point in "
    "your evidence list must be traceable to either the extraction or "
    "screening data you were given; do not invent findings that aren't in "
    "that data. If the evidence is genuinely mixed or thin, say so with low "
    "confidence and recommend manual_review rather than forcing approve or "
    "reject."
)

RISK_BRIEF_TOOL = {
    "name": "record_risk_brief",
    "description": (
        "Record a structured risk assessment for a KYC applicant. "
        "This is a recommendation only — it never finalizes a decision."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "number", "description": "0-100, higher means riskier"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "recommendation": {"type": "string", "enum": ["approve", "manual_review", "reject"]},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "point": {"type": "string"},
                        "source": {"type": "string", "enum": ["extraction", "screening"]},
                    },
                    "required": ["point", "source"],
                },
            },
        },
        "required": ["score", "confidence", "recommendation", "evidence"],
    },
}


SANCTIONS_SCORE = 100.0
PEP_SCORE = 40.0
MISMATCH_SCORE = 10.0
MAX_NON_SANCTIONS_SCORE = 95.0  # only a sanctions hit can reach the full 100


def _compute_risk(extraction, screening) -> tuple[float, str, str]:
    """Deterministic score/confidence/recommendation from screening + extraction
    signals. A sanctions hit is a hard stop — automatic max score and reject.
    Everything else (PEP hits, document mismatches) is a softer signal that
    combines additively, since a PEP hit or a form/document mismatch alone
    could be a mistake rather than wrongdoing and shouldn't be treated the
    same as a confirmed sanctions match."""
    if screening.sanctions_hits:
        return SANCTIONS_SCORE, "high", "reject"

    score = 0.0
    if screening.pep_hits:
        score += PEP_SCORE
    score += MISMATCH_SCORE * len(extraction.mismatches)
    score = min(score, MAX_NON_SANCTIONS_SCORE)

    if score >= 70:
        return score, "high", "reject"
    if score >= 30:
        return score, "medium", "manual_review"
    return score, ("high" if score == 0 else "medium"), "approve"


def _build_context(applicant, extraction, screening) -> str:
    return json.dumps(
        {
            "applicant_name": applicant.full_name,
            "extraction_mismatches": extraction.mismatches,
            "sanctions_hits": screening.sanctions_hits,
            "pep_hits": screening.pep_hits,
        },
        indent=2,
    )


def _synthesize_with_anthropic(applicant, extraction, screening) -> tuple[RiskBriefOut, str]:
    from anthropic import Anthropic

    client = Anthropic()
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[RISK_BRIEF_TOOL],
        tool_choice={"type": "tool", "name": "record_risk_brief"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Here are the extraction and screening results for this "
                    f"applicant:\n\n{_build_context(applicant, extraction, screening)}"
                ),
            }
        ],
    )
    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return RiskBriefOut(**tool_use_block.input), json.dumps(tool_use_block.input)


def _synthesize_with_gemini(applicant, extraction, screening) -> tuple[RiskBriefOut, str]:
    from google import genai
    from google.genai import types

    # Without an explicit timeout, a stalled connection can hang this call
    # forever — the SDK has no default. 30s is generous for a text-only call.
    client = genai.Client(http_options=types.HttpOptions(timeout=30_000))  # reads GEMINI_API_KEY from the environment
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            "Here are the extraction and screening results for this "
            f"applicant:\n\n{_build_context(applicant, extraction, screening)}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=RiskBriefOut,
        ),
    )
    return RiskBriefOut.model_validate_json(response.text), response.text


def _synthesize_with_mistral(applicant, extraction, screening) -> tuple[RiskBriefOut, str]:
    from mistralai.client import Mistral

    client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
    response = client.chat.complete(
        model=MISTRAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Here are the extraction and screening results for this "
                    f"applicant:\n\n{_build_context(applicant, extraction, screening)}"
                ),
            },
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": RISK_BRIEF_TOOL["name"],
                "description": RISK_BRIEF_TOOL["description"],
                "parameters": RISK_BRIEF_TOOL["input_schema"],
            },
        }],
        tool_choice="any",
    )
    arguments = response.choices[0].message.tool_calls[0].function.arguments
    return RiskBriefOut(**json.loads(arguments)), arguments


def _synthesize_with_ollama(applicant, extraction, screening) -> tuple[RiskBriefOut, str]:
    import ollama

    try:
        response = ollama.chat(
            model=OLLAMA_TEXT_MODEL,
            format=RiskBriefOut.model_json_schema(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Here are the extraction and screening results for this "
                        f"applicant:\n\n{_build_context(applicant, extraction, screening)}"
                    ),
                },
            ],
        )
    except Exception as e:
        raise RuntimeError(
            f"Couldn't reach Ollama or model '{OLLAMA_TEXT_MODEL}' isn't pulled. "
            f"Run `ollama serve` and `ollama pull {OLLAMA_TEXT_MODEL}` first. "
            f"Original error: {e}"
        ) from e

    raw = response.message.content
    return RiskBriefOut.model_validate_json(raw), raw


def synthesize_risk(db: Session, applicant_id: int) -> RiskBrief:
    applicant = db.query(Applicant).get(applicant_id)
    extraction = db.query(ExtractionResult).filter_by(applicant_id=applicant_id).first()
    screening = db.query(ScreeningResult).filter_by(applicant_id=applicant_id).first()

    if applicant is None:
        raise ValueError(f"Applicant {applicant_id} not found")
    if extraction is None or screening is None:
        raise ValueError("Run extraction and screening before synthesis")

    if LLM_PROVIDER == "ollama":
        brief, raw_output = _synthesize_with_ollama(applicant, extraction, screening)
    elif LLM_PROVIDER == "gemini":
        brief, raw_output = _synthesize_with_gemini(applicant, extraction, screening)
    elif LLM_PROVIDER == "mistral":
        brief, raw_output = _synthesize_with_mistral(applicant, extraction, screening)
    else:
        brief, raw_output = _synthesize_with_anthropic(applicant, extraction, screening)

    brief.score, brief.confidence, brief.recommendation = _compute_risk(extraction, screening)

    existing = db.query(RiskBrief).filter_by(applicant_id=applicant.id).first()
    if existing:
        db.delete(existing)
        db.flush()

    result = RiskBrief(
        applicant_id=applicant.id,
        score=brief.score,
        confidence=brief.confidence,
        recommendation=brief.recommendation,
        evidence=[e.model_dump() for e in brief.evidence],
        raw_model_output=raw_output,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
