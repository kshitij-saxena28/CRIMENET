from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.db.database import Base
from backend.app.services.app_service import AppService


def make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


FIR = """FIRST INFORMATION REPORT
District: Gautam Budh Nagar P.S.: City Center Year: 2026 FIR No: 0452/2026
Date of FIR: 05/09/2026 Time: 14:30 hrs
Acts & Sections: Section 379
Place of Occurrence: Sector 4 Market, Greater Noida
Complainant / Informant:
Name: Rahul Sharma
Father's Name: Alok Sharma
Address: Flat 402, Greater Noida
Accused: Unknown person riding scooter DL01AB1234
Details of Properties Stolen / Involved:
1x Laptop Serial No: SN-987654321-X
Short Description of Incident: Theft observed on CCTV; suspect fled.
Investigating Officer: ASI Vikram Singh
Phone: 9876543210
Account: ACCT-998877
"""


def verify(service, db, doc):
    return service.review_document(db, doc["document_id"], {"status": "Verified"}, "investigator")


def test_two_fir_case_is_scoped_and_interlinked():
    db = make_db()
    svc = AppService()
    a = svc.extract_document(db, FIR.encode(), "fir_a.txt", case_number="CASE-FLOW")
    b = svc.extract_document(db, FIR.replace("0452/2026", "0453/2026").encode(), "fir_b.txt", case_number="CASE-FLOW")
    verify(svc, db, a)
    verify(svc, db, b)

    summary = svc.analytics_overview(db, "CASE-FLOW")
    assert summary["graph"]["nodes"] > 0
    links = svc.fir_relationships(db, "CASE-FLOW")["relationships"]
    assert links and links[0]["match_score"] >= 0.8
    assert any(r["relation"] == "INTERLINKED_VIA" for r in svc.relationships(db, "CASE-FLOW"))

    # A separate unassigned seeded-style entity must not leak into the workspace.
    from backend.app.models.models import Case
    db.add(Case(case_number="CASE-OTHER", title="other")); db.commit()
    svc.add_entity(db, {"external_id": "P-OUTSIDE", "name": "Outside", "entity_type": "PERSON"}, "investigator", case_number="CASE-OTHER")
    assert all(e["external_id"] != "P-OUTSIDE" for e in svc.entities(db, "CASE-FLOW"))


def test_timeline_and_evidence_are_source_linked():
    db = make_db()
    svc = AppService()
    doc = svc.extract_document(db, FIR.encode(), "fir_timeline.txt", case_number="CASE-TIMELINE")
    verify(svc, db, doc)
    timeline = svc.timeline_intelligence(db, "CASE-TIMELINE")
    assert timeline["event_count"] >= 2
    assert all(e["source_ref"] for e in timeline["events"])

    ev = svc.evidence_upload(db, b"evidence-flow", "statement.txt", "text/plain", "Witness statement", "investigator", case_number="CASE-TIMELINE", document_id=doc["document_id"])
    assert ev[0]["case_number"] == "CASE-TIMELINE"
    assert ev[0]["sha256"]
    checked = svc.verify_evidence(db, ev[0]["evidence_id"], "investigator")
    assert checked["status"] == "verified"


def test_copilot_is_case_grounded():
    db = make_db()
    svc = AppService()
    doc = svc.extract_document(db, FIR.encode(), "fir_copilot.txt", case_number="CASE-COPILOT")
    verify(svc, db, doc)
    result = svc.copilot("What is in this case?", db, "CASE-COPILOT")
    assert "active investigation workspace" in result["answer"].lower()
    assert result["sources"]


def test_new_evidence_is_readback_verified_and_copilot_suggestions_execute_intents():
    db = make_db()
    svc = AppService()
    doc = svc.extract_document(db, FIR.encode(), "fir_suggestions.txt", case_number="CASE-SUGGEST")
    verify(svc, db, doc)
    ev = svc.evidence_upload(db, b"\xff\xd8\xff\xe0pending-flow", "photo.jpg", "image/jpeg", "Scene photo", "investigator", case_number="CASE-SUGGEST", document_id=doc["document_id"])
    assert ev[0]["integrity_status"] == "verified"  # hash re-computed from the stored file at upload
    suggestions = svc.copilot_suggestions(db, "CASE-SUGGEST")
    assert suggestions and all(x.get("question") and x.get("reason") for x in suggestions)
    assert "Show FIR relationships" not in [x["question"] for x in suggestions]  # adaptive, not hard-coded filler
    result = svc.copilot("Show communication activity", db, "CASE-SUGGEST")
    assert "communication event" in result["answer"].lower()


def test_evidence_requires_case_scope_and_isolated_by_case():
    db = make_db()
    svc = AppService()
    try:
        svc.evidence_upload(db, b"global", "global.txt", "text/plain", "test", "investigator")
        assert False, "evidence without a case should be rejected"
    except ValueError as exc:
        assert "case-scoped" in str(exc)
    a = svc.evidence_upload(db, b"same-content", "a.txt", "text/plain", "test", "investigator", case_number="CASE-A")
    b = svc.evidence_upload(db, b"same-content", "b.txt", "text/plain", "test", "investigator", case_number="CASE-B")
    assert svc.evidence(db, case_number="CASE-A")[0]["evidence_id"] == a[0]["evidence_id"]
    assert svc.evidence(db, case_number="CASE-B")[0]["evidence_id"] == b[0]["evidence_id"]
    assert not svc.evidence(db, case_number="CASE-C")


def test_fir_link_intelligence_exposes_shared_key_explanations():
    db = make_db()
    svc = AppService()
    a_text = FIR.replace("0452/2026", "0142/2026").replace("City Center", "Indirapuram")
    b_text = FIR.replace("0452/2026", "0198/2026").replace("City Center", "Kaushambi")
    a = svc.extract_document(db, a_text.encode(), "link_a.txt", case_number="CASE-LINK-CENTER")
    b = svc.extract_document(db, b_text.encode(), "link_b.txt", case_number="CASE-LINK-CENTER")
    verify(svc, db, a); verify(svc, db, b)
    data = svc.fir_relationships(db, "CASE-LINK-CENTER")
    assert data["summary"]["verified_firs"] == 2
    assert data["summary"]["candidate_links"] == 1
    row = data["relationships"][0]
    assert row["interlink_status"] == "verified graph link"
    assert {x["type"] for x in row["reasons"]} >= {"phone", "vehicle", "serial", "account"}
    assert row["bridge_entities"]
