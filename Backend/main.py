import os
import io
import html
import requests
import re
import sqlite3
import hashlib
import hmac
import secrets
import time

from typing import List, Optional
from PIL import Image

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


# LinkedIn PDF upload safety limits
MAX_PDF_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_PDF_PAGES = 15                  # LinkedIn exports are usually 2-6 pages

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import google.generativeai as genai

from reportlab.pdfgen import canvas
from reportlab.lib.utils import simpleSplit

from playwright.sync_api import sync_playwright


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResumeRequest(BaseModel):
    github: str
    company: str
    role: str
    linkedin_data: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""


@app.get("/")
def home():
    return {
        "message": "ResumeForge Backend Running"
    }


# ============================================================
# AUTHENTICATION (SQLite + PBKDF2 password hashing + tokens)
# ============================================================

from fastapi import Header

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000
    ).hex()


def normalize_email(email):
    return (email or "").strip().lower()


class SignupRequest(BaseModel):
    name: str = ""
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


def user_to_public(row):
    return {"name": row["name"], "email": row["email"]}


def get_user_from_token(authorization):
    """Return the user row for a 'Bearer <token>' header, or None."""
    if not authorization:
        return None

    parts = authorization.split(" ", 1)
    token = parts[1].strip() if len(parts) == 2 else authorization.strip()

    if not token:
        return None

    conn = get_db()
    sess = conn.execute(
        "SELECT email FROM sessions WHERE token = ?",
        (token,)
    ).fetchone()

    if not sess:
        conn.close()
        return None

    user = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (sess["email"],)
    ).fetchone()
    conn.close()
    return user


@app.post("/signup")
def signup(data: SignupRequest):
    email = normalize_email(data.email)
    name = (data.name or "").strip() or email.split("@")[0]
    password = data.password or ""

    if "@" not in email or "." not in email:
        return {"success": False, "error": "Please enter a valid email address."}

    if len(password) < 6:
        return {"success": False, "error": "Password must be at least 6 characters."}

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        (email,)
    ).fetchone()

    if existing:
        conn.close()
        return {"success": False, "error": "An account with this email already exists."}

    salt = secrets.token_hex(16)
    pwd_hash = hash_password(password, salt)

    conn.execute(
        "INSERT INTO users (name, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, email, pwd_hash, salt, time.time())
    )

    token = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO sessions (token, email, created_at) VALUES (?, ?, ?)",
        (token, email, time.time())
    )
    conn.commit()
    conn.close()

    return {"success": True, "token": token, "name": name, "email": email}


@app.post("/login")
def login(data: LoginRequest):
    email = normalize_email(data.email)
    password = data.password or ""

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (email,)
    ).fetchone()

    if not user:
        conn.close()
        return {"success": False, "error": "Invalid email or password."}

    expected = user["password_hash"]
    actual = hash_password(password, user["salt"])

    if not hmac.compare_digest(expected, actual):
        conn.close()
        return {"success": False, "error": "Invalid email or password."}

    token = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO sessions (token, email, created_at) VALUES (?, ?, ?)",
        (token, email, time.time())
    )
    conn.commit()
    conn.close()

    return {"success": True, "token": token, "name": user["name"], "email": email}


@app.get("/me")
def me(authorization: Optional[str] = Header(None)):
    user = get_user_from_token(authorization)
    if not user:
        return {"success": False, "error": "Not authenticated."}
    return {"success": True, **user_to_public(user)}


@app.post("/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization:
        parts = authorization.split(" ", 1)
        token = parts[1].strip() if len(parts) == 2 else authorization.strip()
        if token:
            conn = get_db()
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            conn.close()
    return {"success": True}


# ============================================================
# CHATBOT RESUME EDITING
# ============================================================

class ChatEditRequest(BaseModel):
    resume: str
    instruction: str
    company: str = ""
    role: str = ""


class SaveResumeRequest(BaseModel):
    resume: str


@app.post("/chat-edit")
def chat_edit(data: ChatEditRequest):
    current = (data.resume or "").strip()
    instruction = (data.instruction or "").strip()

    if not current:
        return {"success": False, "error": "No resume to edit. Generate a resume first."}

    if not instruction:
        return {"success": False, "error": "Please describe the change you want."}

    prompt = f"""
You are an expert resume editor. Edit the resume below based ONLY on the user's request.

CURRENT RESUME:
{current}

USER REQUEST:
{instruction}

Rules:
- Apply ONLY the change the user asked for. Leave everything else exactly as it is.
- Keep the SAME section headings exactly as written (for example: PROFESSIONAL SUMMARY,
  TECHNICAL SKILLS, EXPERIENCE, PROJECTS, EDUCATION) so the resume formatting stays intact.
- Stay truthful. Do not invent fake jobs, employers, dates, degrees, or metrics.
- Keep it concise and ATS-friendly.
- Target company: {data.company or "N/A"}. Target role: {data.role or "N/A"}.
- Return ONLY the full, updated resume text. No commentary, no explanations, no code fences.
"""

    try:
        response = model.generate_content(prompt)
        updated = clean_resume_markdown(response.text or "")
    except Exception as e:
        print("Chat edit error:", str(e))
        return {"success": False, "error": "The editor is unavailable right now. Please try again."}

    if not updated:
        return {"success": False, "error": "The edit came back empty. Please rephrase your request."}

    with open("latest_resume.txt", "w", encoding="utf-8") as f:
        f.write(updated)

    return {
        "success": True,
        "resume": updated,
        "message": "Done — I've updated your resume."
    }


@app.post("/save-resume")
def save_resume(data: SaveResumeRequest):
    """Persist a given resume text (used by the Undo button so the
    downloadable PDF matches what's shown on screen)."""
    text = (data.resume or "").strip()
    if not text:
        return {"success": False, "error": "Nothing to save."}

    with open("latest_resume.txt", "w", encoding="utf-8") as f:
        f.write(text)

    return {"success": True}


# ============================================================
# RESUME TEMPLATE GALLERY (multiple downloadable PDF designs)
# ============================================================

def render_html_to_pdf(html_content, basename):
    html_file = f"{basename}.html"
    pdf_file = f"{basename}.pdf"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("file://" + os.path.abspath(html_file), wait_until="networkidle")
        page.pdf(
            path=pdf_file,
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"}
        )
        browser.close()
    return pdf_file


def _resume_context():
    if not os.path.exists("latest_resume.txt"):
        return None
    with open("latest_resume.txt", "r", encoding="utf-8") as f:
        resume_text = f.read()

    name = "Candidate Name"
    email = "your.email@example.com"
    phone = "+91 XXXXX XXXXX"
    location = "Your City, India"

    if os.path.exists("latest_contact.txt"):
        with open("latest_contact.txt", "r", encoding="utf-8") as f:
            cl = f.read().splitlines()
        if len(cl) > 0 and cl[0]:
            name = cl[0]
        if len(cl) > 1 and cl[1]:
            email = cl[1]
        if len(cl) > 2 and cl[2]:
            phone = cl[2]
        if len(cl) > 3 and cl[3]:
            location = cl[3]

    s = split_resume_sections(resume_text)
    initials = "".join([w[0] for w in name.split()[:2]]).upper() or "CV"

    return {
        "name": html.escape(name),
        "email": html.escape(email),
        "phone": html.escape(phone),
        "location": html.escape(location),
        "initials": html.escape(initials),
        "summary": clean_text(s["summary"]),
        "skills": format_bullets(s["skills"]),
        "experience": format_bullets(s["experience"]),
        "education": clean_text(s["education"]),
    }


def _fill(tpl, ctx):
    out = tpl
    for k in ["name", "email", "phone", "location", "initials",
              "summary", "skills", "experience", "education"]:
        out = out.replace("__" + k.upper() + "__", ctx[k] or "")
    return out


TEMPLATE_SLATE = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
@page{size:A4;margin:0}
*{box-sizing:border-box;font-family:Arial,Helvetica,sans-serif}
body{margin:0;color:#2b2b2b;font-size:11px}
.head{background:#3f4a56;color:#fff;padding:22px 30px;display:flex;align-items:center;gap:18px}
.mono{width:62px;height:62px;background:#55606c;color:#fff;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;letter-spacing:1px}
.head h1{margin:0;font-size:26px;letter-spacing:1px}
.head .c{font-size:10px;color:#d4d8dd;margin-top:5px;line-height:1.6}
.body{padding:18px 30px}
h2{font-size:12px;letter-spacing:1px;color:#3f4a56;margin:16px 0 6px;border-bottom:1px solid #c9ced3;padding-bottom:3px}
p{margin:0 0 6px;line-height:1.5}
ul.two{column-count:2;margin:0;padding-left:16px}
ul.two li{margin-bottom:3px;line-height:1.4}
ul.exp{list-style:none;margin:0;padding:0}
ul.exp p{font-weight:700;margin:8px 0 2px}
ul.exp li{margin:0 0 3px 16px;list-style:disc}
.lr{display:flex;justify-content:space-between;margin-top:7px;font-size:10px}
.bar{height:5px;background:#e0e3e6;border-radius:3px;margin-top:3px;overflow:hidden}
.bar i{display:block;height:100%;background:#3f4a56}
</style></head><body>
<div class="head"><div class="mono">__INITIALS__</div><div><h1>__NAME__</h1><div class="c">__LOCATION__<br>__EMAIL__ &nbsp;|&nbsp; __PHONE__</div></div></div>
<div class="body">
<h2>SUMMARY</h2><p>__SUMMARY__</p>
<h2>SKILLS</h2><ul class="two">__SKILLS__</ul>
<h2>EXPERIENCE</h2><ul class="exp">__EXPERIENCE__</ul>
<h2>EDUCATION AND TRAINING</h2><p>__EDUCATION__</p>
<h2>LANGUAGES</h2>
<div class="lr"><span>English</span><span>C2</span></div><div class="bar"><i style="width:92%"></i></div>
<div class="lr"><span>Hindi</span><span>Native</span></div><div class="bar"><i style="width:100%"></i></div>
</div></body></html>"""


TEMPLATE_PHOTO = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
@page{size:A4;margin:0}
*{box-sizing:border-box;font-family:Arial,Helvetica,sans-serif}
body{margin:0;color:#33373c;font-size:11px}
.tab{height:14px;width:42%;background:#3f4a56}
.top{display:flex;gap:18px;padding:16px 30px 10px}
.avatar{width:84px;height:96px;background:#55606c;color:#fff;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:700;flex-shrink:0}
.name{font-size:26px;color:#3f4a56;letter-spacing:1px;font-weight:700}
.contact{font-size:10px;color:#444;margin-top:8px;line-height:1.8}
.row{display:flex;padding:0 30px;border-top:1px solid #dcdfe3}
.label{width:120px;flex-shrink:0;padding:13px 12px 13px 0;font-weight:700;color:#3f4a56;font-size:11px;letter-spacing:.5px}
.content{flex:1;padding:13px 0}
p{margin:0 0 6px;line-height:1.5}
ul.two{column-count:2;margin:0;padding-left:16px}
ul.two li{margin-bottom:3px;line-height:1.4}
ul.exp{list-style:none;margin:0;padding:0}
ul.exp p{font-weight:700;margin:6px 0 2px}
ul.exp li{margin:0 0 3px 16px;list-style:disc}
.lr{display:flex;justify-content:space-between;margin-top:6px;font-size:10px}
.bar{height:5px;background:#e3e5e8;border-radius:3px;margin-top:3px;overflow:hidden}
.bar i{display:block;height:100%;background:#3f4a56}
</style></head><body>
<div class="tab"></div>
<div class="top"><div class="avatar">__INITIALS__</div><div><div class="name">__NAME__</div><div class="contact">&#9679; __LOCATION__<br>&#9742; __PHONE__<br>&#9993; __EMAIL__</div></div></div>
<div class="row"><div class="label">SUMMARY</div><div class="content"><p>__SUMMARY__</p></div></div>
<div class="row"><div class="label">SKILLS</div><div class="content"><ul class="two">__SKILLS__</ul></div></div>
<div class="row"><div class="label">EXPERIENCE</div><div class="content"><ul class="exp">__EXPERIENCE__</ul></div></div>
<div class="row"><div class="label">EDUCATION AND TRAINING</div><div class="content"><p>__EDUCATION__</p></div></div>
<div class="row"><div class="label">LANGUAGES</div><div class="content">
<div class="lr"><span>English</span><span>C2</span></div><div class="bar"><i style="width:92%"></i></div>
<div class="lr"><span>Hindi</span><span>Native</span></div><div class="bar"><i style="width:100%"></i></div>
</div></div>
</body></html>"""


TEMPLATE_GREEN = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
@page{size:A4;margin:0}
*{box-sizing:border-box;font-family:Arial,Helvetica,sans-serif}
body{margin:0;color:#2c2f33;font-size:11px}
.wrap{display:flex;min-height:297mm}
.side{width:30%;padding:26px 16px;border-right:1px solid #eee}
.ava{width:60px;height:60px;background:#111;color:#19c37d;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:700;margin-bottom:14px}
.sname{color:#19c37d;font-weight:700;font-size:19px;line-height:1.2}
.scontact{font-size:10px;color:#444;margin-top:12px;line-height:1.9}
.main{flex:1;padding:26px 22px}
h2{color:#111;font-size:13px;font-weight:700;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.5px}
h2:first-child{margin-top:0}
p{margin:0 0 6px;line-height:1.5}
ul.two{column-count:2;margin:0;padding-left:16px}
ul.two li{margin-bottom:3px;line-height:1.4}
ul.exp{list-style:none;margin:0;padding:0}
ul.exp p{font-weight:700;margin:8px 0 2px}
ul.exp li{margin:0 0 3px 16px;list-style:disc}
.lr{display:flex;justify-content:space-between;margin-top:6px;font-size:10px}
.bar{height:5px;background:#e6e8ea;border-radius:3px;margin-top:3px;overflow:hidden}
.bar i{display:block;height:100%;background:#19c37d}
</style></head><body>
<div class="wrap">
<div class="side">
<div class="ava">__INITIALS__</div>
<div class="sname">__NAME__</div>
<div class="scontact">__PHONE__<br>__EMAIL__<br>__LOCATION__</div>
</div>
<div class="main">
<h2>Summary</h2><p>__SUMMARY__</p>
<h2>Skills</h2><ul class="two">__SKILLS__</ul>
<h2>Experience</h2><ul class="exp">__EXPERIENCE__</ul>
<h2>Education and Training</h2><p>__EDUCATION__</p>
<h2>Languages</h2>
<div class="lr"><span>English</span><span>C2</span></div><div class="bar"><i style="width:92%"></i></div>
<div class="lr"><span>Hindi</span><span>Native</span></div><div class="bar"><i style="width:100%"></i></div>
</div>
</div></body></html>"""


TEMPLATE_CLASSIC = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
@page{size:A4;margin:0}
*{box-sizing:border-box;font-family:'Times New Roman',Georgia,serif}
body{margin:0;color:#1a1a1a;font-size:11.5px}
.name{text-align:center;font-size:25px;letter-spacing:3px;margin:26px 0 0;font-weight:700}
.contact{text-align:center;font-size:11px;margin:6px 30px 0;line-height:1.5}
h2{text-align:center;font-size:13px;letter-spacing:2px;border-top:1px solid #000;border-bottom:1px solid #000;padding:3px 0;margin:16px 30px 8px;text-transform:uppercase}
.sec{padding:0 30px}
p{margin:0 0 6px;line-height:1.5}
ul.two{column-count:2;margin:0;padding-left:18px}
ul.two li{margin-bottom:3px;line-height:1.4}
ul.exp{list-style:none;margin:0;padding:0}
ul.exp p{font-weight:700;margin:8px 0 2px;text-align:center}
ul.exp li{margin:0 0 3px 18px;list-style:disc}
.lr{display:flex;justify-content:space-between;margin-top:6px;font-size:10.5px}
.bar{height:5px;background:#ddd;margin-top:3px;overflow:hidden}
.bar i{display:block;height:100%;background:#1a1a1a}
</style></head><body>
<div class="name">__NAME__</div>
<div class="contact">__LOCATION__<br>__PHONE__<br>__EMAIL__</div>
<h2>Summary</h2><div class="sec"><p>__SUMMARY__</p></div>
<h2>Skills</h2><div class="sec"><ul class="two">__SKILLS__</ul></div>
<h2>Experience</h2><div class="sec"><ul class="exp">__EXPERIENCE__</ul></div>
<h2>Education and Training</h2><div class="sec"><p>__EDUCATION__</p></div>
<h2>Languages</h2><div class="sec">
<div class="lr"><span>English</span><span>C2</span></div><div class="bar"><i style="width:92%"></i></div>
<div class="lr"><span>Hindi</span><span>Native</span></div><div class="bar"><i style="width:100%"></i></div>
</div></body></html>"""


TEMPLATE_MAUVE = """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
@page{size:A4;margin:0}
*{box-sizing:border-box;font-family:Georgia,'Times New Roman',serif}
body{margin:0;color:#3a3530;font-size:11px}
.band{background:#a8978d;color:#fff;padding:26px 30px;display:flex;justify-content:space-between;align-items:flex-start}
.bname{font-size:30px;line-height:1.05;letter-spacing:1px;font-weight:700}
.bcontact{text-align:right;font-size:10px;line-height:1.8}
.row{display:flex;padding:0 30px;margin-top:4px}
.label{width:128px;flex-shrink:0;padding:14px 12px 14px 0;font-weight:700;color:#9a8478;font-size:12px;letter-spacing:.5px}
.content{flex:1;padding:14px 0;border-left:0}
p{margin:0 0 6px;line-height:1.55}
ul.two{column-count:2;margin:0;padding-left:16px}
ul.two li{margin-bottom:3px;line-height:1.4}
ul.exp{list-style:none;margin:0;padding:0}
ul.exp p{font-weight:700;margin:6px 0 2px}
ul.exp li{margin:0 0 3px 16px;list-style:disc}
.lr{display:flex;justify-content:space-between;margin-top:6px;font-size:10px}
.bar{height:5px;background:#e7e1db;border-radius:3px;margin-top:3px;overflow:hidden}
.bar i{display:block;height:100%;background:#a8978d}
</style></head><body>
<div class="band"><div class="bname">__NAME__</div><div class="bcontact">__EMAIL__<br>__PHONE__<br>__LOCATION__</div></div>
<div class="row"><div class="label">SUMMARY</div><div class="content"><p>__SUMMARY__</p></div></div>
<div class="row"><div class="label">SKILLS</div><div class="content"><ul class="two">__SKILLS__</ul></div></div>
<div class="row"><div class="label">EXPERIENCE</div><div class="content"><ul class="exp">__EXPERIENCE__</ul></div></div>
<div class="row"><div class="label">EDUCATION AND TRAINING</div><div class="content"><p>__EDUCATION__</p></div></div>
<div class="row"><div class="label">LANGUAGES</div><div class="content">
<div class="lr"><span>English</span><span>C2</span></div><div class="bar"><i style="width:92%"></i></div>
<div class="lr"><span>Hindi</span><span>Native</span></div><div class="bar"><i style="width:100%"></i></div>
</div></div>
</body></html>"""


TEMPLATES = {
    "slate": TEMPLATE_SLATE,
    "photo": TEMPLATE_PHOTO,
    "green": TEMPLATE_GREEN,
    "classic": TEMPLATE_CLASSIC,
    "mauve": TEMPLATE_MAUVE,
}


@app.get("/download-template/{template}")
def download_template(template: str):
    ctx = _resume_context()
    if ctx is None:
        return {"success": False, "error": "No resume generated yet."}

    tpl = TEMPLATES.get(template)
    if not tpl:
        return {"success": False, "error": "Unknown template."}

    html_content = _fill(tpl, ctx)
    pdf_file = render_html_to_pdf(html_content, f"resume_{template}")

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename=f"ResumeForge_{template}.pdf"
    )


# ============================================================
# STEP 1 — AI ROLE MATCHING
# (decides which roles/companies the candidate can apply to,
#  plus stretch roles and what's needed to reach them)
# ============================================================

import json as _json


class RoleMatchRequest(BaseModel):
    resume: str = ""
    linkedin_data: str = ""
    github_skills: str = ""
    github_bio: str = ""


def _extract_json(text):
    """Pull the first JSON object out of an LLM response, tolerant of
    code fences or surrounding prose."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return _json.loads(t[start:end + 1])
    except Exception:
        return None


@app.post("/match-roles")
def match_roles(data: RoleMatchRequest):
    resume = (data.resume or "").strip()
    profile = (data.linkedin_data or "").strip()
    gh_skills = (data.github_skills or "").strip()
    gh_bio = (data.github_bio or "").strip()

    if not resume and not profile and not gh_skills:
        return {"success": False, "error": "No candidate information available. Generate a resume first."}

    prompt = f"""
You are a career advisor and technical recruiter. Analyze the candidate below and
decide which job roles and companies they can realistically apply to RIGHT NOW,
plus "stretch" roles they are close to.

CANDIDATE RESUME:
{resume}

LINKEDIN PROFILE INFO:
{profile or "N/A"}

GITHUB SKILLS: {gh_skills or "N/A"}
GITHUB BIO: {gh_bio or "N/A"}

Return ONLY valid JSON (no markdown, no commentary) in EXACTLY this shape:
{{
  "summary": "one-line read of the candidate's current level",
  "qualified_roles": [
    {{"role": "Job title", "level": "Intern/Junior/Mid/Senior", "reason": "short why they qualify"}}
  ],
  "stretch_roles": [
    {{"role": "Job title", "gap": ["missing skill 1", "missing skill 2"], "advice": "what to do to reach it"}}
  ],
  "target_companies": [
    {{"name": "Company or company-type", "fit": "why it fits this candidate"}}
  ]
}}

Rules:
- Base everything strictly on the candidate's actual skills, projects, and experience. Do not invent skills.
- 3 to 6 qualified_roles, 2 to 4 stretch_roles, 3 to 6 target_companies.
- Keep each text field short (one sentence).
"""

    try:
        response = model.generate_content(prompt)
        parsed = _extract_json(response.text or "")
    except Exception as e:
        print("Role match error:", str(e))
        return {"success": False, "error": "The role analyzer is unavailable right now. Please try again."}

    if not parsed:
        return {"success": False, "error": "Could not analyze roles. Please try again."}

    parsed["success"] = True
    return parsed


# ============================================================
# STEP 2 — JOB OPENING SEARCH (multi-source)
# Runs every CONFIGURED provider, merges + de-duplicates results,
# and tags each job with its source. Providers:
#   1. Aggregators: Adzuna, JSearch (Google Jobs), SerpApi (Google Jobs)
#   2. ATS boards:  Greenhouse, Lever  (free, no key)
#   3. Official LinkedIn/Indeed read-APIs: do NOT exist publicly -> only
#      reachable as one-click platform search links (see _platform_links)
#   4. Scraper: optional JobSpy, DISABLED by default (see warning below)
# Plus one-click search links to the platforms users browse manually.
# ============================================================

import urllib.parse as _urlparse

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")          # for JSearch (Google for Jobs)
SERPAPI_KEY = os.getenv("SERPAPI_KEY")            # for SerpApi Google Jobs

# Companies whose ATS boards we query (free APIs). Override via env
# ATS_GREENHOUSE_COMPANIES / ATS_LEVER_COMPANIES (comma separated tokens).
DEFAULT_GREENHOUSE = ["stripe", "databricks", "figma", "gitlab", "robinhood"]
DEFAULT_LEVER = ["netflix", "plaid", "ramp"]

# WARNING: scraping LinkedIn/Indeed/Naukri violates their Terms of Service,
# gets IPs/accounts blocked, and carries legal risk. This provider is OFF
# unless the product owner explicitly sets ENABLE_SCRAPER=true AND installs
# the optional `python-jobspy` package. Use at your own discretion.
ENABLE_SCRAPER = os.getenv("ENABLE_SCRAPER", "").lower() in ("1", "true", "yes")


class JobSearchRequest(BaseModel):
    role: str
    location: str = ""
    country: str = "in"


def _strip_html(t):
    return re.sub(r"<[^>]+>", "", t or "").strip()


def _platform_links(role, location):
    q = _urlparse.quote_plus(role or "")
    loc = _urlparse.quote_plus(location or "")
    slug = _urlparse.quote((role or "").strip().lower().replace(" ", "-"))
    return [
        {"name": "LinkedIn Jobs", "url": f"https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}"},
        {"name": "Indeed", "url": f"https://www.indeed.com/jobs?q={q}&l={loc}"},
        {"name": "Internshala", "url": f"https://internshala.com/internships/keywords-{q}"},
        {"name": "Naukri", "url": f"https://www.naukri.com/{slug}-jobs"},
        {"name": "Wellfound", "url": f"https://wellfound.com/jobs?q={q}"},
        {"name": "Y Combinator", "url": f"https://www.workatastartup.com/companies?query={q}"},
    ]


# ---- Provider 1a: Adzuna ----
def _provider_adzuna(role, location, country):
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        return None
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY,
        "what": role, "where": location, "results_per_page": 20,
        "content-type": "application/json",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    out = []
    for j in r.json().get("results", []):
        out.append({
            "title": j.get("title", ""),
            "company": (j.get("company") or {}).get("display_name", ""),
            "location": (j.get("location") or {}).get("display_name", ""),
            "url": j.get("redirect_url", ""),
            "snippet": _strip_html(j.get("description", ""))[:240],
            "source": "Adzuna",
        })
    return out


# ---- Provider 1b: JSearch (Google for Jobs -> LinkedIn/Indeed/etc.) ----
def _provider_jsearch(role, location, country):
    if not RAPIDAPI_KEY:
        return None
    query = role + (f" in {location}" if location else "")
    r = requests.get(
        "https://jsearch.p.rapidapi.com/search",
        headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        params={"query": query, "page": "1", "num_pages": "1"},
        timeout=15,
    )
    r.raise_for_status()
    out = []
    for j in (r.json().get("data") or []):
        loc = ", ".join([x for x in [j.get("job_city"), j.get("job_country")] if x])
        out.append({
            "title": j.get("job_title", ""),
            "company": j.get("employer_name", ""),
            "location": loc,
            "url": j.get("job_apply_link", "") or j.get("job_google_link", ""),
            "snippet": _strip_html(j.get("job_description", ""))[:240],
            "source": "Google Jobs",
        })
    return out


# ---- Provider 1c: SerpApi Google Jobs ----
def _provider_serpapi(role, location, country):
    if not SERPAPI_KEY:
        return None
    r = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google_jobs", "q": role, "location": location or "India", "api_key": SERPAPI_KEY},
        timeout=20,
    )
    r.raise_for_status()
    out = []
    for j in (r.json().get("jobs_results") or []):
        opts = j.get("apply_options") or []
        link = opts[0].get("link", "") if opts else j.get("share_link", "")
        out.append({
            "title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": j.get("location", ""),
            "url": link,
            "snippet": _strip_html(j.get("description", ""))[:240],
            "source": "Google Jobs",
        })
    return out


def _ats_companies(env_name, default):
    v = os.getenv(env_name)
    if v:
        return [c.strip() for c in v.split(",") if c.strip()]
    return default


# ---- Provider 2: ATS boards (Greenhouse + Lever), free, no key ----
def _provider_ats(role, location, country):
    role_l = (role or "").lower()
    out = []
    for comp in _ats_companies("ATS_GREENHOUSE_COMPANIES", DEFAULT_GREENHOUSE):
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{comp}/jobs", timeout=8)
            if r.status_code != 200:
                continue
            for j in (r.json().get("jobs") or []):
                title = j.get("title", "")
                if role_l and role_l not in title.lower():
                    continue
                out.append({
                    "title": title, "company": comp.title(),
                    "location": (j.get("location") or {}).get("name", ""),
                    "url": j.get("absolute_url", ""), "snippet": "", "source": "Greenhouse",
                })
        except Exception:
            continue
    for comp in _ats_companies("ATS_LEVER_COMPANIES", DEFAULT_LEVER):
        try:
            r = requests.get(f"https://api.lever.co/v0/postings/{comp}?mode=json", timeout=8)
            if r.status_code != 200:
                continue
            for j in (r.json() or []):
                title = j.get("text", "")
                if role_l and role_l not in title.lower():
                    continue
                out.append({
                    "title": title, "company": comp.title(),
                    "location": (j.get("categories") or {}).get("location", ""),
                    "url": j.get("hostedUrl", ""), "snippet": "", "source": "Lever",
                })
        except Exception:
            continue
    return out


# ---- Provider 4: optional JobSpy scraper (OFF unless owner opts in) ----
def _provider_scraper(role, location, country):
    if not ENABLE_SCRAPER:
        return None
    try:
        from jobspy import scrape_jobs
    except Exception:
        print("Scraper enabled but 'python-jobspy' is not installed.")
        return None
    try:
        df = scrape_jobs(
            site_name=["indeed", "linkedin"],
            search_term=role,
            location=location or "India",
            results_wanted=15,
        )
        out = []
        for _, row in df.iterrows():
            out.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", "")),
                "url": str(row.get("job_url", "")),
                "snippet": _strip_html(str(row.get("description", "")))[:240],
                "source": "Scraper",
            })
        return out
    except Exception as e:
        print("Scraper error:", str(e))
        return None


JOB_PROVIDERS = [
    ("Adzuna", _provider_adzuna),
    ("Google Jobs (JSearch)", _provider_jsearch),
    ("Google Jobs (SerpApi)", _provider_serpapi),
    ("Company ATS", _provider_ats),
    ("Scraper", _provider_scraper),
]


@app.post("/search-jobs")
def search_jobs(data: JobSearchRequest):
    role = (data.role or "").strip()
    if not role:
        return {"success": False, "error": "Please choose a role to search for."}

    location = (data.location or "").strip()
    country = (data.country or "in").strip().lower() or "in"

    all_jobs = []
    sources_active = []
    for name, fn in JOB_PROVIDERS:
        try:
            res = fn(role, location, country)
        except Exception as e:
            print(f"Provider {name} error:", str(e))
            res = None
        if res is None:
            continue
        sources_active.append(name)
        all_jobs.extend(res)

    # De-duplicate by (title, company)
    seen = set()
    deduped = []
    for j in all_jobs:
        key = (j.get("title", "").strip().lower(), j.get("company", "").strip().lower())
        if not key[0] or key in seen:
            continue
        seen.add(key)
        deduped.append(j)

    return {
        "success": True,
        "jobs": deduped[:40],
        "sources_active": sources_active,
        "platform_links": _platform_links(role, location),
    }


# ============================================================
# STEPS 3-5 — READ JD, TAILOR RESUME, SAVE AS NAMED PDF
# ============================================================

class TailorRequest(BaseModel):
    resume: str = ""
    job_description: str = ""
    role: str = ""
    name: str = ""


def _ctx_from_text(resume_text, name, email, phone, location):
    s = split_resume_sections(resume_text)
    initials = "".join([w[0] for w in name.split()[:2]]).upper() or "CV"
    return {
        "name": html.escape(name),
        "email": html.escape(email),
        "phone": html.escape(phone),
        "location": html.escape(location),
        "initials": html.escape(initials),
        "summary": clean_text(s["summary"]),
        "skills": format_bullets(s["skills"]),
        "experience": format_bullets(s["experience"]),
        "education": clean_text(s["education"]),
    }


@app.post("/tailor-resume")
def tailor_resume(data: TailorRequest):
    resume = (data.resume or "").strip()
    jd = (data.job_description or "").strip()
    role = (data.role or "").strip()

    if not resume:
        return {"success": False, "error": "Generate a resume first."}
    if not jd:
        return {"success": False, "error": "Paste the job description first."}

    prompt = f"""
You are an expert resume writer and ATS specialist. Read the job description, then
tailor the candidate's resume to it.

CANDIDATE RESUME:
{resume}

JOB DESCRIPTION:
{jd}

TARGET ROLE: {role or "N/A"}

Do TWO things:
1. Extract what the job wants.
2. Rewrite the resume to match it — change the headline/summary to fit the role,
   move the most relevant projects/experience to the top, add matching skills and
   keywords from the job description (only if the candidate plausibly has them — do
   NOT invent fake jobs, employers, dates or degrees), and trim clearly irrelevant content.

Keep these EXACT section headings so formatting stays intact:
PROFESSIONAL SUMMARY:
TECHNICAL SKILLS:
EXPERIENCE:
PROJECTS:
EDUCATION:

Return ONLY valid JSON (no markdown fences, no commentary) in EXACTLY this shape:
{{
  "requirements": {{
    "skills": ["..."],
    "experience_level": "e.g. 0-2 years / Intern / Mid",
    "tools": ["..."],
    "location": "...",
    "duration": "e.g. 6 months / Full-time / N/A",
    "responsibilities": ["..."],
    "keywords": ["ATS keywords from the JD"]
  }},
  "headline": "new one-line headline for the candidate",
  "changes": ["short bullet describing each change you made"],
  "tailored_resume": "the full rewritten resume text, using the exact headings above"
}}
"""

    try:
        response = model.generate_content(prompt)
        parsed = _extract_json(response.text or "")
    except Exception as e:
        print("Tailor error:", str(e))
        return {"success": False, "error": "The tailoring service is unavailable right now. Please try again."}

    if not parsed or not parsed.get("tailored_resume"):
        return {"success": False, "error": "Could not tailor the resume. Please try again."}

    with open("tailored_resume.txt", "w", encoding="utf-8") as f:
        f.write(clean_resume_markdown(parsed["tailored_resume"]))

    parsed["success"] = True
    return parsed


def _professional_filename(name, role):
    base = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_") or "Resume"
    rolepart = re.sub(r"[^A-Za-z0-9]+", "_", (role or "").strip()).strip("_")
    return base + ("_" + rolepart if rolepart else "") + "_Resume.pdf"


@app.get("/download-tailored/{template}")
def download_tailored(template: str, role: str = ""):
    if not os.path.exists("tailored_resume.txt"):
        return {"success": False, "error": "No tailored resume yet. Tailor one first."}

    with open("tailored_resume.txt", "r", encoding="utf-8") as f:
        resume_text = f.read()

    name = "Candidate Name"
    email = "your.email@example.com"
    phone = "+91 XXXXX XXXXX"
    location = "Your City, India"
    if os.path.exists("latest_contact.txt"):
        with open("latest_contact.txt", "r", encoding="utf-8") as f:
            cl = f.read().splitlines()
        if len(cl) > 0 and cl[0]:
            name = cl[0]
        if len(cl) > 1 and cl[1]:
            email = cl[1]
        if len(cl) > 2 and cl[2]:
            phone = cl[2]
        if len(cl) > 3 and cl[3]:
            location = cl[3]

    tpl = TEMPLATES.get(template)
    if not tpl:
        return {"success": False, "error": "Unknown template."}

    ctx = _ctx_from_text(resume_text, name, email, phone, location)
    html_content = _fill(tpl, ctx)
    pdf_file = render_html_to_pdf(html_content, f"tailored_{template}")

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename=_professional_filename(name, role)
    )


class RenderRequest(BaseModel):
    resume: str = ""
    name: str = ""
    role: str = ""
    template: str = "classic"


@app.post("/render-resume")
def render_resume(data: RenderRequest):
    """Render ANY given resume text to a PDF (used to download a unique,
    per-job tailored resume). Returns a professionally-named PDF."""
    resume = (data.resume or "").strip()
    if not resume:
        return {"success": False, "error": "No resume to render."}

    tpl = TEMPLATES.get(data.template or "classic") or TEMPLATES.get("classic")

    name = (data.name or "Candidate Name").strip()
    email = "your.email@example.com"
    phone = "+91 XXXXX XXXXX"
    location = "Your City, India"
    if os.path.exists("latest_contact.txt"):
        with open("latest_contact.txt", "r", encoding="utf-8") as f:
            cl = f.read().splitlines()
        if len(cl) > 0 and cl[0]:
            name = cl[0]
        if len(cl) > 1 and cl[1]:
            email = cl[1]
        if len(cl) > 2 and cl[2]:
            phone = cl[2]
        if len(cl) > 3 and cl[3]:
            location = cl[3]

    ctx = _ctx_from_text(resume, name, email, phone, location)
    html_content = _fill(tpl, ctx)
    pdf_file = render_html_to_pdf(html_content, "render_app")

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename=_professional_filename(name, data.role)
    )


@app.post("/generate")
def generate_resume(data: ResumeRequest):

    try:
        github_url = data.github.strip()

        username = github_url.rstrip("/").split("/")[-1]

        print("GitHub Username:", username)

        github_warning = ""

        github_data = {}
        repos = []

        if username:
            url = f"https://api.github.com/users/{username}"
            github_response = requests.get(url, timeout=10)

            if github_response.status_code == 200:
                github_data = github_response.json()
            else:
                github_warning = "GitHub profile could not be analyzed properly."

            repos_url = f"https://api.github.com/users/{username}/repos"
            repos_response = requests.get(repos_url, timeout=10)

            if repos_response.status_code == 200:
                repos = repos_response.json()

                if not isinstance(repos, list):
                    repos = []
                    github_warning = "GitHub repositories could not be analyzed properly."

                elif len(repos) == 0:
                    github_warning = "No repositories found. Resume will be generated using provided information."

            else:
                github_warning = "GitHub repositories could not be fetched."

        else:
            github_warning = "No GitHub URL provided. Resume will be generated using provided information."

        linkedin_name = extract_name_from_linkedin(data.linkedin_data)

        github_name = github_data.get("name") or ""

        candidate_name = (
        linkedin_name
        or github_name
        or username
        or "Candidate Name"
    )

        print("\nRepositories:")

        skills = set()
        github_summary = ""

        for repo in repos:

            repo_name = repo.get("name")
            repo_description = repo.get("description")
            repo_language = repo.get("language")

            print("\nRepository")
            print("Name:", repo_name)
            print("Description:", repo_description)
            print("Language:", repo_language)
            print("----------------")

            if repo_language:
                skills.add(repo_language)

            github_summary += (
                f"Repository: {repo_name}\n"
                f"Description: {repo_description}\n"
                f"Language: {repo_language}\n\n"
            )

        print("\nSkills Found:")

        for skill in skills:
            print("-", skill)

        skills_text = ", ".join(skills)

        print("Skills:", skills_text)

        prompt = f"""
You are an expert resume writer and ATS optimization specialist.

Create a detailed, professional, ATS-friendly resume for the following target:

Target Company:
{data.company}

Target Role:
{data.role}

GitHub Profile Analysis:
{github_summary}

Detected GitHub Skills:
{skills_text}

LinkedIn Profile Information:
{data.linkedin_data}

Instructions:

1. Write a strong professional resume, not a short summary.

2. Use this exact structure:

PROFESSIONAL SUMMARY
Write 4-5 detailed lines.
Mention years of experience if available.
Mention target role alignment.
Mention key domains, tools, leadership, and measurable impact.

TECHNICAL SKILLS
Group skills into categories.
Example:
- Programming: Python, JavaScript, TypeScript
- Product/Tools: Product Management, Analytics, GitHub
- Platforms: Cloud, APIs, Databases

EXPERIENCE
For each role, write:
Job Title, Company, Dates, Location if available.
Then write 2-4 strong bullet points.
Each bullet should include action + responsibility + impact.
Use metrics if available.
Do not make bullets too short.

PROJECTS
Create a detailed Projects section using both LinkedIn extracted projects and GitHub repositories.

Rules:
- If LinkedIn Profile Information contains projects, include them.
- If GitHub repositories are available, include the strongest repositories as projects.
- If both LinkedIn projects and GitHub repositories exist, combine them intelligently.
- For each project, write:
  Project Name
  - What the project does
  - Technologies used
  - Impact, purpose, or result
- Do not leave the Projects section empty.
- If no projects are available from either LinkedIn or GitHub, completely omit the Projects section.

EDUCATION
Include education details from LinkedIn if available.

Rules:
- Make the resume detailed but still professional.
- Do not invent fake companies, fake degrees, or fake metrics.
- If exact metrics are missing, describe impact honestly without numbers.
- Use clear bullet points.
- Avoid long paragraphs except in Professional Summary.
- Optimize for ATS keywords related to the target role.
- Do not include markdown tables.
- Do not include extra explanations outside the resume.

If the user uploaded a LinkedIn profile PDF or LinkedIn screenshots containing projects, those projects must be included in the final resume.
Formatting rules:
- Do not use markdown bold symbols like **.
- Do not use separator lines like ---.
- Use plain section headings only.
- Use simple dash bullets.
- Keep the resume detailed but compact.
- For professional PDF, avoid making one tiny leftover section spill onto a second page.
- Use maximum 12-15 core skills.
- Use maximum 5 most relevant experience roles.
- Use maximum 2-3 bullets per role.
- Use maximum 2 strongest projects.

Resume length and detail rules:
- The resume should feel like a complete one-page professional resume.
- Do not make the resume too short.
- Professional Summary should be 4-5 strong lines.
- Technical Skills should include 12-18 relevant skills grouped into categories.
- Experience should include 3-4 bullet points for major roles.
- Each bullet should explain action, responsibility, and impact.
- Include achievements from education if available.
- Include Projects if available from LinkedIn or GitHub.
- If projects are not available, expand Experience and Education enough to make the resume feel complete.
- Do not leave large empty space on a one-page resume.
- Keep the resume detailed but not fake.
- Do not invent fake companies, fake degrees, or fake metrics.

Truthfulness rules:
- Use only facts clearly present in the GitHub data, LinkedIn extraction, or user-provided details.
- Do not invent metrics, revenue impact, customer counts, team sizes, degrees, certifications, acquisitions, company rankings, or technical ownership.
- Do not infer that a project was deployed, used by customers, or successful unless the source explicitly says so.
- If a metric is not available, use neutral wording instead of inventing impact.
- Do not write phrases such as “significantly improved,” “industry-leading,” “India’s leading,” “successfully exited,” or “reduced churn” unless the source data explicitly proves them.
- Do not infer education degree names or grades.

Target company rule:
- Use the target company and role only to choose relevant keywords and prioritize relevant skills.
- Never mention the target company name in the final resume.
- Never write “seeking to join [company name]” in the summary.
- Keep the final resume reusable for multiple job applications.

Skills rules:
- Include only 10 to 15 high-signal skills.
- Group skills into 3 or 4 categories only.
- Prefer concrete tools, languages, frameworks, cloud platforms, and domains.
- Do not include generic skills such as “Software Development,” “Problem Solving,” “Leadership,” or “Communication” unless they are directly relevant and supported by experience.
- Do not repeat the same skill in multiple categories.

Project selection rule:
- For candidates with 8 or more years of experience, include projects only if they are highly relevant, recent, and stronger than older academic projects.
- For senior professionals, prefer work achievements over college projects.
- For students and freshers, include 1 to 2 strong projects.
- Omit weak or irrelevant projects instead of filling space.

Resume length rule:
- For students and candidates with under 5 years of experience, create a one-page resume.
- For candidates with 5 to 10 years of experience, use one page only if content remains readable.
- For candidates with more than 10 years of experience, create a detailed two-page resume.
- Never shrink fonts too much just to force content into one page.
- Never create a second page containing only one small leftover project.

One-page completeness rule:
- For one-page resumes, fill most of the page with meaningful verified content.
- Expand education, certifications, coursework, projects, GitHub work, and internship details when they are available.
- Do not invent content to fill space.
- If the candidate has limited experience, prioritize projects and technical work over generic interests.
"""

        response = model.generate_content(prompt)

        resume_text = response.text

        cleaned_resume = clean_resume_markdown(response.text)

        with open("latest_resume.txt", "w", encoding="utf-8") as f:
            f.write(cleaned_resume)

        with open("latest_contact.txt", "w", encoding="utf-8") as f:
            f.write(f"{candidate_name}\n")
            f.write(f"{data.email}\n")
            f.write(f"{data.phone}\n")
            f.write(f"{data.location}\n")

        return {
            "success": True,
            "resume": cleaned_resume,
            "github_analysis": {
                "username": username,
                "repo_count": len(repos),
                "skills": list(skills),
                "bio": github_data.get("bio") or "Not Available",
                "warning": github_warning
            }
        }

    except Exception as e:
        print("Generate Error:", str(e))

        return {
            "success": False,
            "error": str(e)
        }

def clean_resume_markdown(text):
    if not text:
        return ""

    text = text.replace("\u00ad", "")

    # Remove bold markdown
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)

    # Remove markdown headings
    text = text.replace("#", "")

    # Convert star bullets to dash bullets
    text = re.sub(r"^\s*\*\s+", "- ", text, flags=re.MULTILINE)

    # Remove separator lines like --- or --
    text = re.sub(r"^\s*[-–—_]{2,}\s*$", "", text, flags=re.MULTILINE)

    # Remove extra blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
def extract_name_from_linkedin(linkedin_data):
    if not linkedin_data:
        return ""

    patterns = [
        r"Full Name\s*:\s*(.+)",
        r"Name\s*:\s*(.+)",
        r"- Full Name\s*:\s*(.+)",
        r"- Name\s*:\s*(.+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, linkedin_data, re.IGNORECASE)

        if match:
            name = match.group(1).strip()

            name = name.replace("*", "").replace("#", "").strip()

            if name and len(name) < 80:
                return name

    return ""

def extract_pdf_text(pdf_bytes):
    """
    Locally extract text + page count from a PDF using pypdf.
    Returns (text, page_count). Never raises - returns ("", 0) on failure
    so the route can still fall back to Gemini's visual reading.
    """
    if PdfReader is None:
        return "", 0

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)

        parts = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue

        text = "\n".join(p.strip() for p in parts if p.strip())
        return text.strip(), page_count

    except Exception as e:
        print("Local PDF extraction failed:", str(e))
        return "", 0


@app.post("/upload-linkedin")
async def upload_linkedin(
    pdf: Optional[UploadFile] = File(None),
    images: Optional[List[UploadFile]] = File(None)
):

    temp_files = []

    try:
        images_for_gemini = []
        pdf_for_gemini = None
        local_pdf_text = ""

        if pdf and pdf.filename:

            is_pdf = (
                pdf.content_type == "application/pdf"
                or pdf.filename.lower().endswith(".pdf")
            )

            if not is_pdf:
                return {
                    "success": False,
                    "error": "Please upload a valid LinkedIn profile PDF."
                }

            pdf_contents = await pdf.read()

            # Size guard
            if len(pdf_contents) > MAX_PDF_BYTES:
                return {
                    "success": False,
                    "error": (
                        f"PDF is too large "
                        f"({len(pdf_contents) // (1024 * 1024)} MB). "
                        f"Please upload a file under "
                        f"{MAX_PDF_BYTES // (1024 * 1024)} MB."
                    )
                }

            # Local text extraction + page-count guard
            local_pdf_text, page_count = extract_pdf_text(pdf_contents)

            if page_count > MAX_PDF_PAGES:
                return {
                    "success": False,
                    "error": (
                        f"PDF has {page_count} pages. Please upload your "
                        f"LinkedIn profile export (under {MAX_PDF_PAGES} pages)."
                    )
                }

            pdf_for_gemini = {
                "mime_type": "application/pdf",
                "data": pdf_contents
            }

        images = images or []

        for image in images:

            if not image.filename:
                continue

            contents = await image.read()

            temp_filename = f"linkedin_{image.filename}"
            temp_files.append(temp_filename)

            with open(temp_filename, "wb") as f:
                f.write(contents)

            print("Saved:", temp_filename)

            with Image.open(temp_filename) as img:
                images_for_gemini.append(
                    img.convert("RGB").copy()
                )

        gemini_inputs = []

        if pdf_for_gemini:
            gemini_inputs.append(pdf_for_gemini)

        gemini_inputs.extend(images_for_gemini)

        if not gemini_inputs:
            return {
                "success": True,
                "linkedin_data": ""
            }

        prompt = """
Analyze the uploaded LinkedIn profile PDF and/or LinkedIn profile screenshots.

If a PDF is provided, read the full PDF carefully because it is the recommended source.
If screenshots are provided, treat them as fallback material and use them to fill any missing details.
Combine information from all provided LinkedIn files.

Extract the following information carefully:

- Full Name
- Professional Headline
- Contact information if visible
- Education
- Experience
- Projects
- Skills
- Certifications if visible

For Projects, extract:
- Project name
- Project description
- Technologies used
- Results, impact, or purpose if visible

Important:
Do not ignore the Projects section if it appears in any screenshot.
Do not ignore the Projects section if it appears in the PDF.
If a PDF or screenshot contains projects, include them clearly.
If projects are not available, write: Projects: Not found.

Return clean structured text in this format:

FULL NAME:
...

HEADLINE:
...

EDUCATION:
...

EXPERIENCE:
...

PROJECTS:
- Project Name:
  Description:
  Technologies:
  Impact:

SKILLS:
...
"""

        linkedin_data = ""

        try:
            response = model.generate_content(
                [prompt] + gemini_inputs
            )
            linkedin_data = (response.text or "").strip()
            print("LinkedIn extraction completed successfully.")

        except Exception as gemini_error:
            # Gemini unavailable / quota / network: don't fail the whole
            # request if we already have locally extracted PDF text.
            print("Gemini extraction failed:", str(gemini_error))

            if local_pdf_text:
                print("Falling back to local PDF text extraction.")
                linkedin_data = (
                    "FULL NAME:\n(See details below - extracted locally)\n\n"
                    "RAW LINKEDIN PDF TEXT:\n" + local_pdf_text
                )
            else:
                return {
                    "success": False,
                    "error": (
                        "Could not read the LinkedIn file. "
                        "Please try again or upload screenshots instead."
                    )
                }

        # If Gemini returned nothing usable but we have local text, use it.
        if not linkedin_data and local_pdf_text:
            linkedin_data = (
                "RAW LINKEDIN PDF TEXT:\n" + local_pdf_text
            )

        return {
            "success": True,
            "linkedin_data": linkedin_data
        }

    except Exception as e:
        print("LinkedIn Upload Error:", str(e))

        return {
            "success": False,
            "error": str(e)
        }

    finally:
        for file in temp_files:
            if os.path.exists(file):
                os.remove(file)


@app.get("/download-pdf")
def download_pdf():

    pdf_file = "resume.pdf"

    c = canvas.Canvas(pdf_file)

    c.setFont("Helvetica-Bold", 24)

    c.drawString(
        50,
        800,
        "AI GENERATED RESUME"
    )

    c.setFont("Helvetica", 11)

    c.drawString(
        50,
        780,
        "Created with ResumeForge AI"
    )

    c.line(
        50,
        770,
        550,
        770
    )

    c.setFont("Helvetica", 11)

    y = 740

    try:
        with open("latest_resume.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:

            text = line.strip()

            wrapped_lines = simpleSplit(
                text,
                "Helvetica",
                11,
                470
            )

            for wrapped_line in wrapped_lines:
                c.drawString(60, y, wrapped_line)
                y -= 18

                if y < 50:
                    c.showPage()
                    c.setFont("Helvetica", 11)
                    y = 800

    except Exception as e:
        c.drawString(
            50,
            740,
            f"Error: {str(e)}"
        )

    c.save()

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename="ResumeForge_Resume.pdf"
    )


def clean_text(text):
    text = clean_resume_markdown(text or "")
    return html.escape(text).replace("\n", "<br>")



def split_resume_sections(resume_text):
    sections = {
        "summary": "",
        "skills": "",
        "experience": "",
        "projects": "",
        "education": ""
    }

    current_section = None

    headings = {
        "PROFESSIONAL SUMMARY": "summary",
        "SUMMARY": "summary",
        "TECHNICAL SKILLS": "skills",
        "SKILLS": "skills",
        "EXPERIENCE": "experience",
        "PROJECTS": "projects",
        "EDUCATION": "education"
    }

    for line in resume_text.splitlines():

        clean_line = line.strip().replace(":", "").replace("*", "").replace("#", "")

        upper_line = clean_line.upper()

        if upper_line in headings:
            current_section = headings[upper_line]
            continue

        if current_section:
            sections[current_section] += line + "\n"

    return sections


def format_bullets(text):
    output = ""

    text = clean_resume_markdown(text or "")

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if re.fullmatch(r"[-–—_]{2,}", line):
            continue

        if line.startswith("-"):
            output += f"<li>{html.escape(line[1:].strip())}</li>"

        elif line.startswith("•"):
            output += f"<li>{html.escape(line[1:].strip())}</li>"

        elif line.startswith("*"):
            output += f"<li>{html.escape(line[1:].strip())}</li>"

        else:
            output += f"<p>{html.escape(line)}</p>"

    return output

@app.get("/download-professional-pdf")
def download_professional_pdf():

    if not os.path.exists("latest_resume.txt"):
        return {
            "success": False,
            "error": "No resume generated yet."
        }

    with open("latest_resume.txt", "r", encoding="utf-8") as f:
        resume_text = f.read()

    candidate_name = "Candidate Name"
    email = "your.email@example.com"
    phone = "+91 XXXXX XXXXX"
    location = "Your City, India"

    if os.path.exists("latest_contact.txt"):

        with open("latest_contact.txt", "r", encoding="utf-8") as f:
            contact_lines = f.read().splitlines()

    if len(contact_lines) > 0 and contact_lines[0]:
        candidate_name = contact_lines[0]

    if len(contact_lines) > 1 and contact_lines[1]:
        email = contact_lines[1]

    if len(contact_lines) > 2 and contact_lines[2]:
        phone = contact_lines[2]

    if len(contact_lines) > 3 and contact_lines[3]:
        location = contact_lines[3]

    sections = split_resume_sections(resume_text)

    summary = clean_text(sections["summary"])
    skills = format_bullets(sections["skills"])
    experience = format_bullets(sections["experience"])
    projects = format_bullets(sections["projects"])
    
    projects_section_html = ""

    if projects.strip():
        projects_section_html = f"""
    <div class="section-title">Projects</div>
    <ul>
        {projects}
    </ul>
    """
    education = clean_text(sections["education"])
    candidate_name_display = html.escape(candidate_name)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">

        <style>
@page {{
    size: A4;
    margin: 0;
}}

* {{
    box-sizing: border-box;
}}

html,
body {{
    margin: 0;
    padding: 0;
    font-family: Arial, sans-serif;
    background: white;
    color: #222;
}}

.page-border {{
    position: fixed;
    top: 0;
    left: 0;

    width: 210mm;
    height: 297mm;

    border: 10mm solid #9b918c;

    z-index: 0;

    pointer-events: none;
}}

.corner-top {{
    position: fixed;
    top: 0;
    right: 0;

    width: 0;
    height: 0;

    border-top: 16mm solid #7f7773;
    border-left: 16mm solid transparent;

    z-index: 1;
}}

.corner-bottom {{
    position: fixed;
    bottom: 0;
    left: 0;

    width: 0;
    height: 0;

    border-bottom: 16mm solid #7f7773;
    border-right: 16mm solid transparent;

    z-index: 1;
}}

.resume-page {{
    width: 210mm;
    min-height: 297mm;

    display: flex;
    align-items: stretch;

    background: white;

    padding: 12mm 11mm;

    position: relative;
    z-index: 2;

    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
}}

.sidebar {{
    width: 32%;
    padding-right: 12px;
    border-right: 2px solid #b7aaa5;
    font-size: 10px;
}}
.main {{
    width: 68%;
    padding-left: 18px;
}}

.contact {{
    font-size: 10px;
    line-height: 1.35;
    margin-bottom: 14px;
    color: #222;
}}


.name {{
    font-size: 30px;
    font-weight: 300;
    letter-spacing: 1px;
    color: #555;
    margin-bottom: 16px;
}}

.section-title {{
    font-size: 14px;
    color: #555;
    margin-top: 14px;
    margin-bottom: 5px;
    font-weight: 700;
    break-after: avoid;
}}

.sidebar-title {{
    font-size: 12.5px;
    color: #555;
    margin-top: 14px;
    margin-bottom: 5px;
    font-weight: 700;
    break-after: avoid;
}}
p {{
    font-size: 10px;
    line-height: 1.28;
    margin: 0 0 5px 0;
}}

ul {{
    margin: 0;
    padding-left: 12px;
}}

li {{
    font-size: 10px;
    line-height: 1.28;
    margin-bottom: 3px;
}}


.experience-item {{
    margin-bottom: 6px;
    break-inside: avoid;
}}


.skill-list li {{
    font-size: 9.5px;
    margin-bottom: 2px;
}}
.section-block {{
    break-inside: avoid;
}}
        </style>
    </head>

    <body>
<div class="page-border"></div>
<div class="corner-top"></div>
<div class="corner-bottom"></div>
        <div class="resume-page">

            <div class="sidebar">

                <div class="contact">
                    <strong>{email}</strong><br>
                    {phone}<br>
                    {location}
                </div>

                <div class="sidebar-title">Skills</div>
                <ul class="skill-list">
                    {skills}
                </ul>

                <div class="sidebar-title">Education And Training</div>
                <p>{education}</p>

                <div class="sidebar-title">Languages</div>
                <p>English: Professional</p>
                <p>Hindi: Native</p>

                <div class="sidebar-title">Interests And Hobbies</div>
                <ul>
                    <li>Technology</li>
                    <li>Software Development</li>
                    <li>Problem Solving</li>
                </ul>

            </div>

            <div class="main">

                <div class="name">{candidate_name_display}</div>

                <div class="section-title">Summary</div>
                <p>{summary}</p>

                <div class="section-title">Experience</div>
                <ul>
                    {experience}
                </ul>

                {projects_section_html}

            </div>

        </div>

    </body>
    </html>
    """

    html_file = "professional_resume.html"
    pdf_file = "professional_resume.pdf"

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    with sync_playwright() as p:

        browser = p.chromium.launch()

        page = browser.new_page()

        page.goto(
            "file://" + os.path.abspath(html_file),
            wait_until="networkidle"
        )

        page.pdf(
    path=pdf_file,
    format="A4",
    print_background=True,
    prefer_css_page_size=True,
    margin={
        "top": "0",
        "right": "0",
        "bottom": "0",
        "left": "0"
    }
)

        browser.close()

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename="ResumeForge_Professional_Template.pdf"
    )
