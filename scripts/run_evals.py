"""
Eval harness: runs the full pipeline over every synthetic applicant and
checks EACH STAGE independently against hand-verified ground truth (see
eval_expected.py) — not just the final recommendation. A failure tells you
which stage broke: "extraction misread the DOB" is a different bug than
"synthesis under-weighted a correct DOB mismatch," and this output tells you
which one happened.

Five independent checks per applicant:
  1. extraction  — exact string match against what's actually on the ID card
  2. screening   — sanctions/PEP hits match exactly
  3. recommendation — matches the expected label
  4. confidence  — matches the expected tier (the two ambiguous applicants
                    must land on low/medium, never high — false certainty is
                    worse than an honest "I'm not sure")
  5. evidence    — every evidence point is grounded in real extraction/
                    screening data, not invented. Pure keyword heuristic
                    (see _mentions_positive_finding) since this needs to stay
                    a plain string check, not a second model call.

Deliberately simple — a pass/fail table, not a scoring framework. The point
is a non-zero exit code on failure, so it can gate a CI build.

Requires generate_dataset.py to have been run first, and your LLM_PROVIDER's
API key to be set.

Run from the project root: python scripts/run_evals.py
Run a single applicant (useful on a rate-limited free tier, or to confirm
one case works before running the whole set):
  python scripts/run_evals.py --applicant "Hamza Yousaf"
Add a delay between applicants (default 0) to stay under a requests-per-minute cap:
  python scripts/run_evals.py --delay 15
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.db import SessionLocal
from app.models import Applicant
from app.services.extraction import extract_applicant
from app.services.screening import screen_applicant
from app.services.synthesis import synthesize_risk
from scripts.eval_expected import EXPECTED


def _mentions_positive_finding(text: str, keywords: list[str]) -> bool:
    """Heuristic: does this evidence text claim one of `keywords` actually
    happened, as opposed to explicitly saying it didn't? E.g. "Address
    mismatch detected..." -> True, "No sanctions hits found..." -> False."""
    lowered = text.lower()
    if not any(k in lowered for k in keywords):
        return False
    negations = ["no ", "none", "not found", "no matches", "no hits", "doesn't", "does not"]
    return not any(neg in lowered for neg in negations)


def check_extraction(extraction, expected) -> tuple[bool, str]:
    actual = {
        "name": extraction.extracted_name.strip(),
        "dob": extraction.extracted_dob.strip(),
        "id_number": extraction.extracted_id_number.strip(),
        "address": extraction.extracted_address.strip(),
    }
    exp = {
        "name": expected.expected_name,
        "dob": expected.expected_dob,
        "id_number": expected.expected_id_number,
        "address": expected.expected_address,
    }
    field_errors = [f for f in exp if actual[f].lower() != exp[f].lower()]

    actual_mismatch_fields = sorted(m["field"] for m in extraction.mismatches)
    expected_mismatch_fields = sorted(expected.expected_mismatch_fields)
    mismatch_error = actual_mismatch_fields != expected_mismatch_fields

    if not field_errors and not mismatch_error:
        return True, "ok"
    detail = []
    if field_errors:
        detail.append(f"fields wrong: {field_errors}")
    if mismatch_error:
        detail.append(f"mismatches: expected {expected_mismatch_fields}, got {actual_mismatch_fields}")
    return False, "; ".join(detail)


def check_screening(screening, expected) -> tuple[bool, str]:
    actual_sanctions = sorted(h["matched_name"] for h in screening.sanctions_hits)
    actual_pep = sorted(h["matched_name"] for h in screening.pep_hits)
    expected_sanctions = sorted(expected.expected_sanctions_names)
    expected_pep = sorted(expected.expected_pep_names)

    if actual_sanctions == expected_sanctions and actual_pep == expected_pep:
        return True, "ok"
    return False, f"sanctions: expected {expected_sanctions} got {actual_sanctions}; pep: expected {expected_pep} got {actual_pep}"


def _check_field(actual, expected_value) -> tuple[bool, str]:
    if actual == expected_value:
        return True, "ok"
    return False, f"expected {expected_value}, got {actual}"


def check_recommendation(brief, expected) -> tuple[bool, str]:
    return _check_field(brief.recommendation, expected.expected_recommendation)


def check_confidence(brief, expected) -> tuple[bool, str]:
    return _check_field(brief.confidence, expected.expected_confidence)


def check_evidence_grounding(extraction, screening, brief) -> tuple[bool, str]:
    extraction_points = [e["point"] for e in brief.evidence if e["source"] == "extraction"]
    screening_points = [e["point"] for e in brief.evidence if e["source"] == "screening"]

    has_mismatch = bool(extraction.mismatches)
    claims_mismatch = any(
        _mentions_positive_finding(p, ["mismatch", "discrepancy", "does not match", "doesn't match"])
        for p in extraction_points
    )
    if has_mismatch and not claims_mismatch:
        return False, "real extraction mismatch not reflected in any evidence point"
    if not has_mismatch and claims_mismatch:
        return False, "evidence claims an extraction mismatch that doesn't exist"

    has_hit = bool(screening.sanctions_hits) or bool(screening.pep_hits)
    claims_hit = any(
        _mentions_positive_finding(p, ["sanctions", "pep", "match"])
        for p in screening_points
    )
    if has_hit and not claims_hit:
        return False, "real screening hit not reflected in any evidence point"
    if not has_hit and claims_hit:
        return False, "evidence claims a screening hit that doesn't exist"

    return True, "ok"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--applicant", help="Run only this applicant (exact full name)")
    parser.add_argument("--delay", type=float, default=0, help="Seconds to wait between applicants")
    args = parser.parse_args()

    db = SessionLocal()
    applicants = db.query(Applicant).all()

    if args.applicant:
        applicants = [a for a in applicants if a.full_name == args.applicant]
        if not applicants:
            print(f"No applicant named {args.applicant!r} found.")
            sys.exit(1)

    if not applicants:
        print("No applicants found — run scripts/generate_dataset.py first.")
        sys.exit(1)

    rows = []
    for i, applicant in enumerate(applicants, start=1):
        if i > 1 and args.delay:
            print(f"(waiting {args.delay}s before next applicant...)", flush=True)
            time.sleep(args.delay)

        expected = EXPECTED.get(applicant.full_name)
        if expected is None:
            print(f"WARNING: no expected data for {applicant.full_name} — skipping")
            continue

        print(f"[{i}/{len(applicants)}] {applicant.full_name}: extracting...", flush=True)
        extraction = extract_applicant(db, applicant.id)
        print(f"[{i}/{len(applicants)}] {applicant.full_name}: screening...", flush=True)
        screening = screen_applicant(db, applicant.id)
        print(f"[{i}/{len(applicants)}] {applicant.full_name}: synthesizing...", flush=True)
        brief = synthesize_risk(db, applicant.id)

        checks = {
            "extraction": check_extraction(extraction, expected),
            "screening": check_screening(screening, expected),
            "recommendation": check_recommendation(brief, expected),
            "confidence": check_confidence(brief, expected),
            "evidence": check_evidence_grounding(extraction, screening, brief),
        }
        rows.append((applicant.full_name, checks))

    columns = ["extraction", "screening", "recommendation", "confidence", "evidence"]
    header = f"{'Applicant':<22}" + "".join(f"{c:<16}" for c in columns)
    print(header)
    print("-" * len(header))

    failures = 0
    failure_details = []
    for name, checks in rows:
        row_passed = all(passed for passed, _ in checks.values())
        if not row_passed:
            failures += 1
        cells = "".join(f"{'PASS' if checks[c][0] else 'FAIL':<16}" for c in columns)
        print(f"{name:<22}{cells}")
        for c in columns:
            passed, detail = checks[c]
            if not passed:
                failure_details.append(f"  {name} / {c}: {detail}")

    print("-" * len(header))
    print(f"{len(rows) - failures}/{len(rows)} applicants fully passed")

    if failure_details:
        print("\nFailure details:")
        for line in failure_details:
            print(line)

    db.close()
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
