"""Acceptance tests for Differentiator #1 — the Career Evidence Vault.

Covers the H1–H7 criteria from the feature spec:

  H1  A resume bullet can't be exported without approved supporting evidence.
  H2  A user can approve, edit, reject, and delete evidence items.
  H3  A job requirement can be matched to one or more evidence items.
  H4  Unsupported metrics are never added automatically.
  H5  Private/imported content can't appear on a public proof page without opt-in.
  H6  Existing resume generation and PDF export still work.
  H7  Endpoints reject unauthorized access to another user's evidence.

All AI + GitHub calls are mocked, so the suite is deterministic and needs no keys.
"""
import json
import main
from conftest import FakeResp


# ---------------------------------------------------------------------------
# H1 — export is blocked unless every bullet cites approved evidence
# ---------------------------------------------------------------------------
def test_h1_export_blocked_without_approved_evidence(client, signup, auth, add_evidence):
    token, _ = signup()
    ev_id = add_evidence(token, title="Portfolio Site")

    # A bullet backed by an approved item passes; unsupported ones are blocked.
    r = client.post("/resume/export-check", headers=auth(token), json={"claims": [
        {"text": "Built a portfolio site", "evidence_item_ids": [ev_id]},
        {"text": "Led a team of 12 engineers", "evidence_item_ids": []},
        {"text": "Cited a fake id", "evidence_item_ids": [999999]},
    ]})
    body = r.json()
    assert body["success"] is True
    assert body["ok"] is False
    assert "Led a team of 12 engineers" in body["unsupported"]
    assert "Cited a fake id" in body["unsupported"]
    assert "Built a portfolio site" not in body["unsupported"]


def test_h1_export_ok_when_all_supported(client, signup, auth, add_evidence):
    token, _ = signup()
    ev_id = add_evidence(token)
    r = client.post("/resume/export-check", headers=auth(token), json={"claims": [
        {"text": "Backed bullet", "evidence_item_ids": [ev_id]},
    ]})
    body = r.json()
    assert body["success"] is True and body["ok"] is True
    assert body["unsupported"] == []


# ---------------------------------------------------------------------------
# H2 — approve / edit / reject / delete lifecycle
# ---------------------------------------------------------------------------
def _import_one(client, monkeypatch, token, auth):
    """Import a single unapproved, ai_inferred GitHub item (mocked fetch)."""
    monkeypatch.setattr(main, "_fetch_github_evidence", lambda *a, **k: [{
        "name": "cool-repo", "url": "https://github.com/x/cool-repo",
        "deployed": "https://cool.example.com", "description": "A cool tool",
        "language": "Python", "topics": ["cli", "automation"], "stars": 3,
        "readme_excerpt": "This repo does cool things.",
    }])
    r = client.post("/evidence/import-github", headers=auth(token), json={"github": "someuser"})
    assert r.json().get("success") and r.json().get("imported") == 1
    items = client.get("/evidence", headers=auth(token)).json()["evidence"]
    assert len(items) == 1
    return items[0]


def test_h2_lifecycle(client, signup, auth, monkeypatch):
    token, _ = signup()
    item = _import_one(client, monkeypatch, token, auth)
    ev_id = item["id"]
    # Imported items start unapproved + ai_inferred.
    assert item["user_approved"] is False
    assert item["confidence_status"] == "ai_inferred"

    # approve
    assert client.post("/evidence/update", headers=auth(token),
                       json={"id": ev_id, "action": "approve"}).json()["success"]
    got = client.get("/evidence", headers=auth(token)).json()["evidence"][0]
    assert got["user_approved"] is True and got["confidence_status"] == "user_confirmed"

    # edit
    assert client.post("/evidence/update", headers=auth(token),
                       json={"id": ev_id, "action": "edit",
                             "title": "Renamed", "description": "New desc"}).json()["success"]
    got = client.get("/evidence", headers=auth(token)).json()["evidence"][0]
    assert got["title"] == "Renamed" and got["description"] == "New desc"

    # reject
    assert client.post("/evidence/update", headers=auth(token),
                       json={"id": ev_id, "action": "reject"}).json()["success"]
    got = client.get("/evidence", headers=auth(token)).json()["evidence"][0]
    assert got["user_approved"] is False

    # delete
    assert client.post("/evidence/delete", headers=auth(token),
                       json={"id": ev_id}).json()["success"]
    assert client.get("/evidence", headers=auth(token)).json()["evidence"] == []


def test_h2_manual_add_is_approved(client, signup, auth, add_evidence):
    token, _ = signup()
    add_evidence(token, title="Hand entered", description="I built this myself.")
    items = client.get("/evidence", headers=auth(token)).json()["evidence"]
    assert len(items) == 1
    assert items[0]["user_approved"] is True
    assert items[0]["confidence_status"] == "user_confirmed"


def test_h2_review_flow_adds_context_and_metric(client, signup, auth, monkeypatch):
    token, _ = signup()
    item = _import_one(client, monkeypatch, token, auth)
    ev_id = item["id"]
    r = client.post("/evidence/review", headers=auth(token), json={
        "id": ev_id,
        "personal_contribution": "I built the whole backend solo.",
        "solo_or_team": "solo",
        "problem_solved": "Automates a manual report.",
        "metric": "cut report time from 2 hours to 5 minutes",
        "demo_url": "https://cool.example.com",
        "approve": True,
    })
    body = r.json()
    assert body["success"] and body["approved"] is True and body["metric_added"] is True

    items = client.get("/evidence", headers=auth(token)).json()["evidence"]
    # Now two items: the reviewed project (approved) + a user-confirmed metric item.
    cats = sorted(i["category"] for i in items)
    assert cats == ["metric", "project"]
    project = [i for i in items if i["category"] == "project"][0]
    metric = [i for i in items if i["category"] == "metric"][0]
    assert project["user_approved"] is True
    assert "I built the whole backend solo." in project["description"]
    assert metric["user_approved"] is True
    assert metric["confidence_status"] == "user_confirmed"


# ---------------------------------------------------------------------------
# H3 — a job requirement matches one or more evidence items
# ---------------------------------------------------------------------------
def test_h3_evidence_map_matches_requirement(client, signup, auth, add_evidence, mock_gemini):
    token, _ = signup()
    ev_id = add_evidence(token, title="FastAPI service",
                         description="Built a REST API in Python with FastAPI.")
    mock_gemini(json.dumps({
        "overall_fit": "Solid overlap on backend Python.",
        "requirements": [
            {"requirement": "Python backend development", "status": "supported",
             "evidence_item_ids": [ev_id], "explanation": "FastAPI service in Python.",
             "action_if_missing": ""},
            {"requirement": "Kubernetes", "status": "missing",
             "evidence_item_ids": [], "explanation": "No evidence.",
             "action_if_missing": "Build a small deployment."},
        ],
    }))
    r = client.post("/evidence-map", headers=auth(token),
                    json={"job_description": "We need a Python backend dev who knows k8s."})
    body = r.json()
    assert body["success"] is True
    assert body["approved_count"] == 1
    reqs = body["map"]["requirements"]
    supported = [x for x in reqs if x["status"] == "supported"]
    assert supported and ev_id in supported[0]["evidence_item_ids"]


# ---------------------------------------------------------------------------
# H4 — unsupported metrics are never added automatically
# ---------------------------------------------------------------------------
def test_h4_metric_guardrail_unit():
    ok = main._bullet_metric_supported
    # No metric -> always fine.
    assert ok("Built a REST API with FastAPI", ["FastAPI project"]) is True
    # Fabricated metrics with no backing evidence -> rejected.
    assert ok("Scaled to 10,000 users", ["a small side project"]) is False
    assert ok("Improved performance by 40%", ["made it faster"]) is False
    assert ok("Generated $5,000 in revenue", ["a hobby app"]) is False
    # A metric that is present in the evidence -> allowed.
    assert ok("Handled 10000 requests/sec", ["load-tested at 10000 rps"]) is True


def test_h4_generation_drops_fabricated_metric(client, signup, auth, add_evidence, mock_gemini):
    token, _ = signup()
    ev_id = add_evidence(token, title="Todo App",
                         description="A todo app built with React. No metrics recorded.")
    # Model tries to sneak in a fabricated metric on one bullet.
    mock_gemini(json.dumps({"bullets": [
        {"text": "Built a todo app with React", "evidence_item_ids": [ev_id],
         "claim_type": "project", "confidence_status": "user_confirmed"},
        {"text": "Grew the app to 50,000 daily active users", "evidence_item_ids": [ev_id],
         "claim_type": "achievement", "confidence_status": "verified"},
    ]}))
    r = client.post("/evidence-resume", headers=auth(token),
                    json={"role": "Frontend Developer"})
    body = r.json()
    assert body["success"] is True
    texts = [b["text"] for b in body["bullets"]]
    assert "Built a todo app with React" in texts
    assert all("50,000" not in t for t in texts)
    assert body["dropped_unsupported"] >= 1

    # And the fabricated bullet was NOT persisted as a resume_claim.
    conn = main.get_db()
    rows = conn.execute("SELECT text FROM resume_claims").fetchall()
    conn.close()
    assert all("50,000" not in r["text"] for r in rows)


# ---------------------------------------------------------------------------
# H5 — private/imported content can't leak onto a public proof page
# ---------------------------------------------------------------------------
def test_h5_proof_page_requires_optin_and_hides_unselected(client, signup, auth, add_evidence):
    token, _ = signup()
    a = add_evidence(token, title="Public Portfolio", description="A portfolio site.")
    b = add_evidence(token, title="Secret Internal Tool", description="Confidential project.")

    # Nothing is shared by default.
    assert client.post("/proof-page", headers=auth(token),
                       json={"evidence_item_ids": []}).json()["success"] is False

    # Only the selected item appears; the unselected one must not.
    r = client.post("/proof-page", headers=auth(token),
                    json={"evidence_item_ids": [a], "name": "Jane"})
    body = r.json()
    assert body["success"] is True and body["included"] == [a]
    assert "Public Portfolio" in body["html"]
    assert "Secret Internal Tool" not in body["html"]


def test_h5_unapproved_and_readme_never_shared(client, signup, auth, monkeypatch):
    token, _ = signup()
    # Import a repo whose README contains a secret marker; item stays unapproved.
    monkeypatch.setattr(main, "_fetch_github_evidence", lambda *a, **k: [{
        "name": "private-thing", "url": "https://github.com/x/private-thing",
        "deployed": "", "description": "Repo description only",
        "language": "Go", "topics": [], "stars": 0,
        "readme_excerpt": "SECRET_README_TOKEN internal architecture details",
    }])
    client.post("/evidence/import-github", headers=auth(token), json={"github": "x"})
    item = client.get("/evidence", headers=auth(token)).json()["evidence"][0]
    ev_id = item["id"]

    # Selecting an UNAPPROVED item yields no page.
    assert client.post("/proof-page", headers=auth(token),
                       json={"evidence_item_ids": [ev_id]}).json()["success"] is False

    # Approve it, then generate — the README content must NOT be in the page.
    client.post("/evidence/update", headers=auth(token), json={"id": ev_id, "action": "approve"})
    body = client.post("/proof-page", headers=auth(token),
                       json={"evidence_item_ids": [ev_id]}).json()
    assert body["success"] is True
    assert "SECRET_README_TOKEN" not in body["html"]
    assert "private-thing" in body["html"]  # title (user-facing) is fine


# ---------------------------------------------------------------------------
# H6 — existing generation + PDF export still work
# ---------------------------------------------------------------------------
def test_h6_pdf_export_endpoint_still_works(client):
    resume = ("Jane Developer\n\nSUMMARY\nSoftware engineer.\n\n"
              "EXPERIENCE\n- Built things with Python\n\nSKILLS\nPython, FastAPI")
    r = client.post("/download-resume-pdf", json={
        "template": "classic", "resume": resume, "name": "Jane Developer",
        "email": "jane@example.com", "phone": "+1 555 0100", "location": "Remote",
    })
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_h6_build_resume_pdf_helper(tmp_path):
    out = main.build_resume_pdf(str(tmp_path / "r"), "Bob\n- did work",
                                "Bob", "b@x.com", "1", "Earth", template="classic")
    import os
    assert os.path.exists(out)
    with open(out, "rb") as f:
        assert f.read(4) == b"%PDF"


def test_h6_generation_plumbing_still_works(client, mock_gemini):
    mock_gemini("Dear Hiring Manager,\n\nI am a strong fit.\n\nSincerely,\nJane")
    r = client.post("/generate-cover-letter", json={
        "resume": "Jane Developer — Python engineer", "role": "Backend Engineer"})
    body = r.json()
    assert body["success"] is True
    assert "strong fit" in body["cover_letter"]


# ---------------------------------------------------------------------------
# H7 — no cross-user access; unauthenticated requests rejected
# ---------------------------------------------------------------------------
def test_h7_cross_user_isolation(client, signup, auth, add_evidence):
    token_a, _ = signup()
    token_b, _ = signup()
    a_item = add_evidence(token_a, title="A's private evidence")

    # B cannot see A's evidence.
    b_list = client.get("/evidence", headers=auth(token_b)).json()["evidence"]
    assert b_list == []

    # B cannot update A's item (scoped -> "Not found").
    r = client.post("/evidence/update", headers=auth(token_b),
                    json={"id": a_item, "action": "reject"})
    assert r.json()["success"] is False

    # B cannot review A's item.
    r = client.post("/evidence/review", headers=auth(token_b), json={"id": a_item})
    assert r.json()["success"] is False

    # B "deleting" A's item does not remove it.
    client.post("/evidence/delete", headers=auth(token_b), json={"id": a_item})
    a_list = client.get("/evidence", headers=auth(token_a)).json()["evidence"]
    assert len(a_list) == 1 and a_list[0]["id"] == a_item

    # B cannot put A's item on a proof page.
    assert client.post("/proof-page", headers=auth(token_b),
                       json={"evidence_item_ids": [a_item]}).json()["success"] is False


def test_h7_unauthenticated_rejected(client):
    for method, path, payload in [
        ("get", "/evidence", None),
        ("post", "/evidence/add", {"title": "x"}),
        ("post", "/evidence/update", {"id": 1, "action": "approve"}),
        ("post", "/evidence/delete", {"id": 1}),
        ("post", "/evidence/review", {"id": 1}),
        ("post", "/evidence/import-github", {"github": "x"}),
        ("post", "/evidence-map", {"job_description": "x"}),
        ("post", "/evidence-resume", {"role": "x"}),
        ("post", "/resume/export-check", {"claims": []}),
        ("post", "/proof-page", {"evidence_item_ids": [1]}),
    ]:
        resp = client.get(path) if method == "get" else client.post(path, json=payload)
        body = resp.json()
        assert body.get("success") is False, f"{path} should require auth: {body}"
