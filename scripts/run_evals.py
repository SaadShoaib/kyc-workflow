"""
Eval harness: runs the full pipeline over every synthetic applicant and
checks the risk synthesizer's recommendation against an expected label.

Deliberately simple — a pass/fail table, not a scoring framework. The point
is to have something with a non-zero exit code on failure, so it can gate a
CI build rather than a client report.

Requires ANTHROPIC_API_KEY to be set, and requires generate_dataset.py to
have been run first.

Run from the project root: python scripts/run_evals.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Applicant
from app.services.extraction import extract_applicant
from app.services.screening import screen_applicant
from app.services.synthesis import synthesize_risk

# Expected recommendation per applicant, set by hand when the dataset was built.
# "manual_review" covers both real red flags and genuinely ambiguous cases —
# the system is meant to triage, never to auto-decide.
EXPECTED = {
    "Ayesha Raza": "approve",
    "Bilal Ahmed": "approve",
    "Sana Tariq": "approve",
    "Usman Khalid": "approve",
    "Hamza Yousaf": "manual_review",       # DOB mismatch
    "Vladimir Petrescu": "manual_review",  # sanctions list fuzzy match
    "Fatima Noor": "manual_review",        # ambiguous address difference
    "Ali Raza Shah": "manual_review",      # ambiguous, thin history
}


def main() -> None:
    db = SessionLocal()
    applicants = db.query(Applicant).all()

    if not applicants:
        print("No applicants found — run scripts/generate_dataset.py first.")
        sys.exit(1)

    results = []
    for applicant in applicants:
        extract_applicant(db, applicant.id)
        screen_applicant(db, applicant.id)
        brief = synthesize_risk(db, applicant.id)

        expected = EXPECTED.get(applicant.full_name, "manual_review")
        passed = brief.recommendation == expected
        results.append((applicant.full_name, expected, brief.recommendation, passed))

    print(f"{'Applicant':<22}{'Expected':<16}{'Actual':<16}{'Result'}")
    print("-" * 70)
    failures = 0
    for name, expected, actual, passed in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            failures += 1
        print(f"{name:<22}{expected:<16}{actual:<16}{status}")

    print("-" * 70)
    print(f"{len(results) - failures}/{len(results)} passed")

    db.close()
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
