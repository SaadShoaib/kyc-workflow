"""
Populates the synthetic applicant set for the demo:
  - 4 clean applicants
  - 1 with a document/form DOB mismatch
  - 1 whose name fuzzy-matches the mock sanctions list
  - 1 with a document/form address mismatch
  - 1 whose name fuzzy-matches the mock PEP list

ID card images are no longer generated here — they're the hand-made images
in data/id_images/*.jpeg. This script only populates the database, pointing
each applicant at their existing image. Every name, address, and ID number
here is fictional.

Run from the project root: python scripts/generate_dataset.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine
from app.models import Applicant, Decision, ExtractionResult, RiskBrief, ScreeningResult

IMAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "id_images"

Base.metadata.create_all(bind=engine)

# (full_name, dob, address, id_number, image_filename)
# dob/address here are the FORM-submitted values. Where they're deliberately
# different from what's printed on the applicant's ID card image, that's the
# mismatch scenario for that applicant (see comments below).
APPLICANTS = [
    ("Ayesha Raza", "1994-03-12", "House 12, Model Town, Lahore", "35202-1234567-1", "ayesha_raza.jpeg"),
    ("Bilal Ahmed", "1988-07-22", "Flat 4B, DHA Phase 5, Karachi", "42101-9876543-2", "bilal_ahmed.jpeg"),
    ("Sana Tariq", "1996-11-02", "Street 9, F-10, Islamabad", "61101-1122334-5", "sana_tariq.jpeg"),
    ("Usman Khalid", "1991-05-18", "Block C, Johar Town, Lahore", "35202-5566778-9", "usman_khalid.jpeg"),
    # Red flag: form DOB (1979-06-15) doesn't match the card's DOB (1985-01-01)
    ("Hamza Yousaf", "1979-06-15", "Gulshan-e-Iqbal, Karachi", "42201-1112223-4", "hamza_yousaf.jpeg"),
    # Red flag: name closely matches an entry on the mock sanctions list
    ("Rao Anwar", "1975-09-09", "Unknown", "00000-0000000-0", "rao_anwar.jpeg"),
    # Red flag: form address (Lahore) doesn't match the card's address (Islamabad)
    ("Fatima Noor", "1993-02-28", "House 22, Model Town, Lahore", "35202-3344556-7", "fatima_noor.jpeg"),
    # Red flag: name closely matches an entry on the mock PEP list
    ("Rahimullah Qureshi", "1999-12-01", "Sector G-11, Islamabad", "61101-9988776-5", "rahimullah_qureshi.jpeg"),
]


def main() -> None:
    db = SessionLocal()
    # SQLite reuses primary keys after a delete, so wiping only Applicant
    # would let new applicants silently inherit old ExtractionResult/
    # ScreeningResult/RiskBrief/Decision rows left over from a previous run
    # (same reused applicant_id, stale data). Clear all of them together.
    db.query(Decision).delete()
    db.query(RiskBrief).delete()
    db.query(ScreeningResult).delete()
    db.query(ExtractionResult).delete()
    db.query(Applicant).delete()
    db.commit()

    for full_name, dob, address, id_number, image_filename in APPLICANTS:
        image_path = IMAGE_DIR / image_filename
        if not image_path.is_file():
            raise FileNotFoundError(f"Missing ID card image for {full_name}: {image_path}")
        db.add(
            Applicant(
                full_name=full_name,
                dob=dob,
                address=address,
                id_number=id_number,
                id_image_path=str(image_path),
            )
        )

    db.commit()
    count = db.query(Applicant).count()
    print(f"Created {count} synthetic applicants, pointing at existing images in {IMAGE_DIR}")
    db.close()


if __name__ == "__main__":
    main()
