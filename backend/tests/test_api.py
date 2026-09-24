"""HTTP layer: validation, response shape, end-to-end urgency ordering."""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    # latency so tickets pile up behind the first one and ordering is observable
    app = create_app(process_seconds=0.05, aging_seconds=0)
    with TestClient(app) as c:  # context manager runs lifespan -> worker starts
        yield c


def wait_all_done(client, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        tickets = client.get("/api/tickets").json()["tickets"]
        if tickets and all(t["status"] == "done" for t in tickets):
            return tickets
        time.sleep(0.02)
    raise AssertionError("tickets did not finish")


def test_submit_returns_202_queued(client):
    r = client.post("/api/tickets", json={"text": "Please reset my password"})
    assert r.status_code == 202
    body = r.json()
    assert body["id"] == "T0001"
    assert body["urgency"] == "normal"
    assert body["status"] in ("queued", "processing")


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "   "}, {"text": 5},
                                     {"text": "x" * 5001}])
def test_validation(client, payload):
    assert client.post("/api/tickets", json=payload).status_code == 422


def test_unknown_ticket_404(client):
    assert client.get("/api/tickets/T9999").status_code == 404


def test_action_and_escalation_results(client):
    a = client.post("/api/tickets", json={"text": "I forgot my password"}).json()["id"]
    e = client.post("/api/tickets", json={"text": "My account was hacked, reset my password"}).json()["id"]
    wait_all_done(client)

    done_a = client.get(f"/api/tickets/{a}").json()
    assert done_a["decision"] == "action"
    assert done_a["category"] == "password_reset"
    assert done_a["action"] == "send_password_reset"
    assert "PWD-" in done_a["action_result"]

    done_e = client.get(f"/api/tickets/{e}").json()
    assert done_e["decision"] == "escalate"
    assert done_e["category"] is None
    assert done_e["action"] is None
    assert "security" in done_e["reason"].lower()


def test_processed_by_urgency_not_arrival(client):
    texts = [
        "please reset my password",                    # normal (may start first: worker idle)
        "how do I change my email? no rush",           # low
        "check my billing status",                     # normal
        "production is down, restart the server",      # high
        "my account was compromised",                  # critical
    ]
    ids = [client.post("/api/tickets", json={"text": t}).json()["id"] for t in texts]
    tickets = {t["id"]: t for t in wait_all_done(client)}
    order = sorted(ids, key=lambda i: tickets[i]["processed_order"])

    # the first ticket may already be running before the rest arrive
    rest = [i for i in order if i != ids[0]]
    urg = [tickets[i]["urgency"] for i in rest]
    rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    assert urg == sorted(urg, key=rank.get), urg
    assert tickets[rest[0]]["urgency"] == "critical"
    assert tickets[rest[-1]]["urgency"] == "low"
