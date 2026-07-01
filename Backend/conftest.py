"""Pytest fixtures for the ResumeForge backend.

Points the app at a throwaway SQLite database and disables real AI/network BEFORE
`main` is imported, so the test suite never touches the developer's real users.db
or the Gemini/GitHub APIs. Each test starts from clean user-scoped tables.
"""
import os
import sys
import uuid
import tempfile

# --- must run before `import main` so init_db() targets the temp DB ---
_TEST_DB = os.path.join(tempfile.gettempdir(), f"rf_test_{uuid.uuid4().hex}.db")
os.environ["RF_DB_PATH"] = _TEST_DB
os.environ.setdefault("GEMINI_API_KEY", "")     # no real key -> no accidental calls
os.environ.pop("DATABASE_URL", None)            # force SQLite, never a real Postgres

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from fastapi.testclient import TestClient

import main  # noqa: E402  (import after env setup, by design)


class FakeResp:
    """Stand-in for a Gemini response object (only `.text` is used)."""
    def __init__(self, text):
        self.text = text


@pytest.fixture(scope="session")
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def clean_db():
    """Wipe user-scoped tables before every test for isolation."""
    conn = main.get_db()
    for tbl in ("resume_claims", "evidence_items", "evidence_sources",
                "job_evidence_matches", "sessions", "resumes", "applications",
                "profile_vault", "users"):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def mock_gemini(monkeypatch):
    """Return a helper that makes model.generate_content yield canned text."""
    def _set(text):
        monkeypatch.setattr(main.model, "generate_content",
                            lambda *a, **k: FakeResp(text))
    return _set


# ---- small auth/setup helpers exposed as fixtures ----
def _signup(client, email=None, password="Passw0rd!"):
    email = email or f"u_{uuid.uuid4().hex[:12]}@example.com"
    r = client.post("/signup", json={"name": "Test User", "email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("success") and body.get("token"), body
    return body["token"], email


@pytest.fixture
def signup(client):
    def _make(email=None):
        return _signup(client, email)
    return _make


def _auth(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def auth():
    return _auth


@pytest.fixture
def add_evidence(client):
    """Create an APPROVED evidence item for a user; returns its id."""
    def _add(token, title="My Project", description="A web app built with Python and FastAPI.",
             category="project", tags=None):
        r = client.post("/evidence/add",
                        headers=_auth(token),
                        json={"category": category, "title": title,
                              "description": description, "tags": tags or []})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success") and body.get("id"), body
        return body["id"]
    return _add
