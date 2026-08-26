"""
Generates the synthetic applicant set for the demo:
  - 4 clean applicants
  - 1 with a document/form DOB mismatch
  - 1 whose name fuzzy-matches the mock sanctions list
  - 2 genuinely ambiguous cases (nothing concretely wrong, nothing clean either)

Also renders a simple templated "ID card" image per applicant with PIL, so
the extraction step has a real image to read. Every name, address, and ID
number here is fictional.

Run from the project root: python scripts/generate_dataset.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from app.db import Base, SessionLocal, engine
from app.models import Applicant

IMAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "id_images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

Base.metadata.create_all(bind=engine)

# (full_name, dob, address, id_number, doc_name_override, doc_dob_override)
# Overrides simulate what's printed on the document; None means it matches the form.
APPLICANTS = [
    ("Ayesha Raza", "1994-03-12", "House 12, Model Town, Lahore", "35202-1234567-1", None, None),
    ("Bilal Ahmed", "1988-07-22", "Flat 4B, DHA Phase 5, Karachi", "42101-9876543-2", None, None),
    ("Sana Tariq", "1996-11-02", "Street 9, F-10, Islamabad", "61101-1122334-5", None, None),
    ("Usman Khalid", "1991-05-18", "Block C, Johar Town, Lahore", "35202-5566778-9", None, None),
    # Red flag: DOB on the document doesn't match the form
    ("Hamza Yousaf", "1985-01-01", "Gulshan-e-Iqbal, Karachi", "42201-1112223-4", None, "1979-06-15"),
    # Red flag: name closely matches an entry on the mock sanctions list
    ("Rao Anwar", "1975-09-09", "Unknown", "00000-0000000-0", None, None),
    # Ambiguous: minor address difference — could be a typo, could matter
    ("Fatima Noor", "1993-02-28", "House 22, Model Town, Islamabad", "35202-3344556-7", None, None),
    # Ambiguous: pep list
    ("Rahimullah Qureshi", "1999-12-01", "Sector G-11, Islamabad", "61101-9988776-5", None, None),
]


def render_id_card(name: str, dob: str, id_number: str, address: str, path: Path) -> None:
    img = Image.new("RGB", (600, 380), color="#f4f1ea")
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 590, 370], outline="#333333", width=3)
    draw.text((30, 30), "NATIONAL IDENTITY CARD (SAMPLE)", fill="#333333")
    draw.text((30, 90), f"Name: {name}", fill="#000000")
    draw.text((30, 130), f"Date of Birth: {dob}", fill="#000000")
    draw.text((30, 170), f"ID Number: {id_number}", fill="#000000")
    draw.text((30, 210), f"Address: {address}", fill="#000000")
    draw.text((30, 330), "SYNTHETIC DOCUMENT — NOT A REAL ID", fill="#b3261e")
    img.save(path)


def main() -> None:
    db = SessionLocal()
    db.query(Applicant).delete()
    db.commit()

    for full_name, dob, address, id_number, doc_name_override, doc_dob_override in APPLICANTS:
        image_path = IMAGE_DIR / f"{full_name.replace(' ', '_').lower()}.png"
        render_id_card(
            name=doc_name_override or full_name,
            dob=doc_dob_override or dob,
            id_number=id_number,
            address=address,
            path=image_path,
        )
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
    print(f"Created {count} synthetic applicants and ID images in {IMAGE_DIR}")
    db.close()


if __name__ == "__main__":
    main()
