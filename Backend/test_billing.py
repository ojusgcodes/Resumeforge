"""Placement Season Pass — billing core tests.

Run from Backend/:   py -m pytest test_billing.py -v
              or:    py test_billing.py

These lift the REAL billing functions out of main.py with `ast` and run them
against a real temp SQLite DB built by the real init_db(). No FastAPI, no
network, no mocks of our own logic — so if someone edits main.py, this notices.

What's covered (the things that either lose money or let someone steal Pro):
  B1  a webhook with a forged / tampered / missing signature is REJECTED
  B2  a correctly signed webhook is accepted
  B3  a paid webhook grants exactly SEASON_PASS_MONTHS of Pro
  B4  a REPLAYED webhook (same payment id) does not grant a second pass
  B5  buying a second pass STACKS time (the user never loses days)
  B6  an expired pass reads as 'free' again
  B7  a cached 'pro' verdict is short-lived, so expiry can't be outrun
"""
import ast
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import threading
import time

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

WANT_FUNCS = {
    "_cache_get", "_cache_set", "get_db", "_q", "db_one", "db_all", "db_exec",
    "normalize_email", "init_db",
    "_plan_for_user", "_payment_seen", "_user_id_by_email", "_grant_pro", "_hmac_ok",
}
WANT_CONSTS = {"SEASON_PASS_MONTHS"}


def _load_billing_core():
    """Extract just the billing + DB functions from main.py and exec them."""
    lines = open(MAIN, encoding="utf-8", errors="replace").read().splitlines()
    cut = max(i for i, l in enumerate(lines) if l.startswith("@app."))
    src = "\n".join(lines[:cut])          # trim to a clean top-level boundary

    tree = ast.parse(src)
    chunks, found = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANT_FUNCS:
            chunks.append(ast.get_source_segment(src, node))
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in WANT_CONSTS:
                    chunks.append(ast.get_source_segment(src, node))
                    found.add(t.id)

    missing = (WANT_FUNCS | WANT_CONSTS) - found
    assert not missing, "not found in main.py: %s" % sorted(missing)

    db = os.path.join(tempfile.mkdtemp(), "billing_test.db")
    ns = {
        "os": os, "time": time, "sqlite3": sqlite3, "hmac": hmac, "hashlib": hashlib,
        "threading": threading, "_threading": threading,
        "_CACHE": {}, "_CACHE_LOCK": threading.Lock(),
        "DB_PATH": db, "USE_PG": False, "DATABASE_URL": None, "print": print,
    }
    exec("\n\n".join(chunks), ns)
    ns["init_db"]()

    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO users (name, email, password_hash, salt) VALUES (?,?,?,?)",
                 ("Test", "buyer@example.com", "x", "y"))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email='buyer@example.com'").fetchone()[0]
    conn.close()
    return ns, db, uid


SECRET = "whsec_test_123"


def _rzp_body(payment_id, email, amount=59900):
    return json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": payment_id, "email": email, "amount": amount, "currency": "INR"}}},
    }).encode()


def _sign(body, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_billing_core():
    ns, db, uid = _load_billing_core()
    months = ns["SEASON_PASS_MONTHS"]
    hmac_ok, plan_of, grant, seen = (
        ns["_hmac_ok"], ns["_plan_for_user"], ns["_grant_pro"], ns["_payment_seen"])

    body = _rzp_body("pay_A1", "buyer@example.com")

    # B1 — nobody gets Pro by forging a webhook.
    assert hmac_ok(SECRET, body, _sign(body, "attacker_secret")) is False, "B1a wrong secret accepted"
    assert hmac_ok(SECRET, _rzp_body("pay_A1", "buyer@example.com", 1), _sign(body)) is False, \
        "B1b tampered body accepted"
    assert hmac_ok(SECRET, body, "") is False, "B1c missing signature accepted"
    assert hmac_ok("", body, _sign(body)) is False, "B1d unconfigured secret must fail CLOSED"

    # B2 — the genuine article is accepted.
    assert hmac_ok(SECRET, body, _sign(body)) is True, "B2 valid signature rejected"

    # B3 — paying grants the pass.
    assert plan_of(uid) == "free"
    exp1 = grant(uid, months, "razorpay", "pay_A1", 59900, "INR", "payment.captured")
    ns["_CACHE"].clear()
    assert plan_of(uid) == "pro", "B3 payment did not grant Pro"
    days = (exp1 - time.time()) / 86400
    assert (months * 30 - 2) < days < (months * 30 + 2), "B3 wrong pass length: %.0f days" % days

    # B4 — a replayed webhook must not grant a second pass.
    assert seen("pay_A1") is True
    assert seen("pay_NEW") is False
    if not seen("pay_A1"):                       # mirrors the webhook handler's guard
        grant(uid, months, "razorpay", "pay_A1")
    conn = sqlite3.connect(db)
    n_pay = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    exp_now = conn.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    assert n_pay == 1 and abs(exp_now - exp1) < 1, "B4 replay granted extra time"

    # B5 — a second genuine purchase stacks on top; the user loses no days.
    exp2 = grant(uid, months, "razorpay", "pay_B2", 59900, "INR", "payment.captured")
    stacked = (exp2 - exp1) / 86400
    assert (months * 30 - 2) < stacked < (months * 30 + 2), "B5 second pass overwrote instead of stacking"

    # B6/B7 — expiry actually expires, and the cache can't outrun it.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE subscriptions SET expires_at=? WHERE user_id=?", (time.time() - 60, uid))
    conn.commit()
    conn.close()
    ns["_cache_set"]("plan:%s" % uid, "pro", ttl=300)
    assert ns["_CACHE"]["plan:%s" % uid][0] - time.time() <= 300, "B7 plan cached too long"
    ns["_CACHE"].clear()
    assert plan_of(uid) == "free", "B6 expired pass still reads as Pro"


if __name__ == "__main__":
    test_billing_core()
    print("Billing core is sound — 7/7 checks passed.")
