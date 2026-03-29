"""Tests for the demo Flask app.

One test (test_hello_returns_200) contains an intentional bug — it asserts
status_code == 404 instead of 200.  bernstein demo will fix it.
"""

import pytest
from app import app as flask_app


@pytest.fixture
def client():
    """Return a test client for the Flask app."""
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_hello_returns_200(client):
    """GET / should return HTTP 200.

    BUG 4: asserts 404 instead of 200.
    """
    resp = client.get("/")
    assert resp.status_code == 404  # wrong — should be 200


def test_hello_json_structure(client):
    """GET / should return JSON with a 'message' field."""
    resp = client.get("/")
    data = resp.get_json()
    assert data is not None
    assert "message" in data
    assert data["status"] == "ok"


def test_get_item_first(client):
    """GET /items/1 should return the first item (apple)."""
    resp = client.get("/items/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["item"] == "apple"


def test_health_returns_200(client):
    """GET /health should return HTTP 200."""
    resp = client.get("/health")
    assert resp.status_code == 200
