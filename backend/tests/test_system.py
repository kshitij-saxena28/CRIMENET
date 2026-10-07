from ai_engine.entity_resolution.resolver import similarity
from ai_engine.nlp.extractor import extract_entities
from graph_engine.engine import GraphEngine


def test_health_is_minimal_and_public(client):
    r = client.get("/health")
    assert r.status_code == 200 and set(r.json()) == {"status", "api_version"}


def test_login(login):
    assert login("investigator")


def test_nlp():
    e = extract_entities("FIR-101 vehicle DL01AB1234 phone 9876543210")
    assert {x["type"] for x in e} >= {"FIR", "VEHICLE", "PHONE"}


def test_resolution():
    assert similarity("Rahul Kumar", "rahul kumar") == 1


def test_graph():
    g = GraphEngine(); g.add_entity("A"); g.add_entity("B"); g.add_entity("C")
    g.add_relationship("A", "B", "X"); g.add_relationship("B", "C", "Y")
    assert g.path("A", "C") == ["A", "B", "C"]


def test_hidden_connections_unknown_node_is_empty_not_500():
    g = GraphEngine(); g.add_entity("A")
    assert g.hidden_connections("A", "ZZZ") == []


def test_model_registry_is_honest(client, login):
    data = client.get("/models/status", headers=login("investigator")).json()
    names = {m["name"]: m for m in data["models"]}
    assert "NOT neural" in names["Translation"]["type"]
    assert names["Anomaly detector"]["calibration"].startswith("uncalibrated")
