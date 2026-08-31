"""
Hand-verified ground truth for the synthetic dataset, one entry per
applicant — checked by hand against the actual ID card images in
data/id_images/, not derived from the generator script. This is what
run_evals.py compares real pipeline output against, stage by stage.
"""
from dataclasses import dataclass, field


@dataclass
class ExpectedApplicant:
    full_name: str

    # --- extraction ---
    # What the model should read off the ID card image itself (the document
    # values), not the form data. Mismatches are (field, form_value, doc_value).
    expected_name: str
    expected_dob: str
    expected_id_number: str
    expected_address: str
    expected_mismatch_fields: list[str] = field(default_factory=list)

    # --- screening ---
    expected_sanctions_names: list[str] = field(default_factory=list)
    expected_pep_names: list[str] = field(default_factory=list)

    # --- synthesis ---
    expected_recommendation: str = "approve"
    expected_confidence: str = "high"


EXPECTED = {
    "Ayesha Raza": ExpectedApplicant(
        full_name="Ayesha Raza",
        expected_name="Ayesha Raza",
        expected_dob="1994-03-12",
        expected_id_number="35202-1234567-1",
        expected_address="House 12, Model Town, Lahore",
        expected_recommendation="approve",
        expected_confidence="high",
    ),
    "Bilal Ahmed": ExpectedApplicant(
        full_name="Bilal Ahmed",
        expected_name="Bilal Ahmed",
        expected_dob="1988-07-22",
        expected_id_number="42101-9876543-2",
        expected_address="Flat 4B, DHA Phase 5, Karachi",
        expected_recommendation="approve",
        expected_confidence="high",
    ),
    "Sana Tariq": ExpectedApplicant(
        full_name="Sana Tariq",
        expected_name="Sana Tariq",
        expected_dob="1996-11-02",
        expected_id_number="61101-1122334-5",
        expected_address="Street 9, F-10, Islamabad",
        expected_recommendation="approve",
        expected_confidence="high",
    ),
    "Usman Khalid": ExpectedApplicant(
        full_name="Usman Khalid",
        expected_name="Usman Khalid",
        expected_dob="1991-05-18",
        expected_id_number="35202-5566778-9",
        expected_address="Block C, Johar Town, Lahore",
        expected_recommendation="approve",
        expected_confidence="high",
    ),
    "Hamza Yousaf": ExpectedApplicant(
        full_name="Hamza Yousaf",
        # Document DOB — deliberately differs from the form DOB (1979-06-15).
        expected_name="Hamza Yousaf",
        expected_dob="1985-01-01",
        expected_id_number="42201-1112223-4",
        expected_address="Gulshan-e-Iqbal, Karachi",
        expected_mismatch_fields=["dob"],
        expected_recommendation="approve",
        expected_confidence="medium",
    ),
    "Rao Anwar": ExpectedApplicant(
        full_name="Rao Anwar",
        expected_name="Rao Anwar",
        expected_dob="1975-09-09",
        expected_id_number="00000-0000000-0",
        expected_address="Unknown",
        expected_sanctions_names=["Rao Anwar"],
        expected_recommendation="reject",
        expected_confidence="high",
    ),
    "Fatima Noor": ExpectedApplicant(
        full_name="Fatima Noor",
        # Document address — deliberately differs from the form address (...Lahore).
        expected_name="Fatima Noor",
        expected_dob="1993-02-28",
        expected_id_number="35202-3344556-7",
        expected_address="House 22, Model Town, Islamabad",
        expected_mismatch_fields=["address"],
        expected_recommendation="approve",
        expected_confidence="medium",
    ),
    "Rahimullah Qureshi": ExpectedApplicant(
        full_name="Rahimullah Qureshi",
        expected_name="Rahimullah Qureshi",
        expected_dob="1999-12-01",
        expected_id_number="61101-9988776-5",
        expected_address="Sector G-11, Islamabad",
        expected_pep_names=["Rahimullah Qureshi"],
        expected_recommendation="manual_review",
        expected_confidence="medium",
    ),
}
