"""
Document extraction — plain function, no framework.

Supports three providers, switched with the LLM_PROVIDER env var:
  - "anthropic" (default): Claude vision, forced tool-call output.
  - "gemini": Google's hosted Gemini vision, forced JSON-schema output.
    Free tier (rate-limited), needs a GEMINI_API_KEY.
  - "ollama": a local vision model via Ollama, forced JSON-schema output.
    Free, no API key, runs entirely on your machine. Needs a vision-capable
    model pulled locally first, e.g.:
        ollama pull llama3.2-vision      # heavier, more accurate
        ollama pull moondream            # lighter, faster, good for iterating

All three paths produce the same ExtractedFields object and go through the same
diff/save logic below — the provider only affects how that object gets filled in.
"""
import base64
import json
import mimetypes
import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Applicant, ExtractionResult
from app.schemas import ExtractedFields, Mismatch

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic", "gemini", or "ollama"

ANTHROPIC_MODEL = "claude-sonnet-5"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llama3.2-vision")

EXTRACTION_PROMPT = (
    "Read the identity document in this image and record the name, date of "
    "birth, ID number, and address exactly as printed. Focus getting the information correct instead of guessing"
    "\n"
    "The fields are laid out vertically in this fixed order, each under its "
    "own label. Don't read any urdu text, only focus on the english text:\n"
    "  1. \"Name\" — the full name, at the top.\n"
    "  2. \"DOB\" — the date of birth, directly below the name, printed in "
    "YYYY-MM-DD format. Read each digit carefully — do not guess.\n"
    "  3. \"Identity Number\" — below the DOB, it is in the CNIC format: 00000-0000000-0.\n"
    "  4. \"Address\" — the last field, at the bottom. Ignore any text near "
    "the photo box (e.g. \"no image available\") — that is not the address."
)


EXTRACTION_TOOL = {
    "name": "record_extracted_fields",
    "description": "Record the fields read off an identity document image.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "dob": {"type": "string", "description": "Format YYYY-MM-DD"},
            "id_number": {"type": "string"},
            "address": {"type": "string"},
        },
        "required": ["name", "dob", "id_number", "address"],
    },
}


def _image_to_base64(path: str) -> str:
    return base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")


def _extract_with_anthropic(image_path: str) -> tuple[ExtractedFields, str]:
    from anthropic import Anthropic

    client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_extracted_fields"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _image_to_base64(image_path),
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )
    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return ExtractedFields(**tool_use_block.input), json.dumps(tool_use_block.input)


def _extract_with_gemini(image_path: str) -> tuple[ExtractedFields, str]:
    from google import genai
    from google.genai import types

    # Without an explicit timeout, a stalled connection can hang this call
    # forever — the SDK has no default. 30s is generous for a single image.
    client = genai.Client(http_options=types.HttpOptions(timeout=30_000))  # reads GEMINI_API_KEY from the environment
    mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=Path(image_path).read_bytes(), mime_type=mime_type),
            EXTRACTION_PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedFields,
        ),
    )
    return ExtractedFields.model_validate_json(response.text), response.text


def _extract_with_ollama(image_path: str) -> tuple[ExtractedFields, str]:
    import ollama

    try:
        response = ollama.chat(
            model=OLLAMA_VISION_MODEL,
            format=ExtractedFields.model_json_schema(),
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT,
                    "images": [image_path],
                }
            ],
        )
    except Exception as e:
        raise RuntimeError(
            f"Couldn't reach Ollama or model '{OLLAMA_VISION_MODEL}' isn't pulled. "
            f"Run `ollama serve` and `ollama pull {OLLAMA_VISION_MODEL}` first. "
            f"Original error: {e}"
        ) from e

    raw = response.message.content
    return ExtractedFields.model_validate_json(raw), raw


def _diff_fields(form: dict, doc: ExtractedFields) -> list[Mismatch]:
    """Simple case-insensitive field diff. Good enough for a demo — a real
    system would want fuzzier date parsing and address normalization."""
    mismatches = []
    pairs = [
        ("name", form["full_name"], doc.name),
        ("dob", form["dob"], doc.dob),
        ("id_number", form["id_number"], doc.id_number),
        ("address", form["address"], doc.address),
    ]
    for field, form_value, doc_value in pairs:
        if form_value.strip().lower() != doc_value.strip().lower():
            mismatches.append(Mismatch(field=field, form_value=form_value, doc_value=doc_value))
    return mismatches


def extract_applicant(db: Session, applicant_id: int) -> ExtractionResult:
    applicant = db.query(Applicant).get(applicant_id)
    if applicant is None:
        raise ValueError(f"Applicant {applicant_id} not found")

    if LLM_PROVIDER == "ollama":
        extracted, raw_output = _extract_with_ollama(applicant.id_image_path)
    elif LLM_PROVIDER == "gemini":
        extracted, raw_output = _extract_with_gemini(applicant.id_image_path)
    else:
        extracted, raw_output = _extract_with_anthropic(applicant.id_image_path)

    form_data = {
        "full_name": applicant.full_name,
        "dob": applicant.dob,
        "id_number": applicant.id_number,
        "address": applicant.address,
    }
    mismatches = _diff_fields(form_data, extracted)

    # Overwrite any previous extraction for this applicant rather than stacking rows.
    existing = db.query(ExtractionResult).filter_by(applicant_id=applicant.id).first()
    if existing:
        db.delete(existing)
        db.flush()

    result = ExtractionResult(
        applicant_id=applicant.id,
        extracted_name=extracted.name,
        extracted_dob=extracted.dob,
        extracted_id_number=extracted.id_number,
        extracted_address=extracted.address,
        mismatches=[m.model_dump() for m in mismatches],
        raw_model_output=raw_output,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
