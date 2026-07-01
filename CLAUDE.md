# ResumeForge — Project Guide (for Claude Code)

AI-powered resume builder + job-application platform. Turns a user's GitHub +
LinkedIn into a tailored resume, matches roles, searches jobs, tailors a resume
per job, and (via a Chrome extension) auto-fills applications. Includes accounts,
an application tracker, and resume history.

## Stack
- **Backend:** FastAPI (Python), single file `Backend/main.py` (~2500+ lines). AI via Google Gemini (`google-generativeai`, model `gemini-2.5-flash`).
- **Frontend:** static site — `Frontend/index.html` (one large file, inline CSS/JS), plus `about.html`, `privacy.html`, `terms.html`, `extension.html`, `editor.html`.
- **Extension:** `Extension/` — Manifest V3 Chrome extension (auto-fill).
- **DB:** Supabase Postgres in prod, SQLite locally. Schema in `Backend/schema.sql`.
- **Hosting:** backend on Render, frontend on Vercel (`resumeforge-opal.vercel.app`), DB on Supabase. Backend URL: `https://resumeforge-backend-1bu3.onrender.com`.

## Run locally
```bash
# Backend
cd Backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# Frontend: just open Frontend/index.html (it auto-targets localhost:8000 when on localhost/file://)
```
Local DB is a SQLite file `Backend/users.db` (auto-created by init_db). Delete it to reset schema.

## Deploy
- `git add . && git commit -m "..." && git push` → Render redeploys backend, Vercel redeploys frontend.
- After schema changes, re-run `Backend/schema.sql` in the Supabase SQL Editor.

## Environment variables (Render)
- `GEMINI_API_KEY` — required, all AI.
- `DATABASE_URL` — Supabase **Session pooler** connection string (IPv4; Render is IPv4-only). If unset, backend falls back to local SQLite.
- `ALLOWED_ORIGINS` — comma-separated allowed CORS origins (set to the Vercel URL in prod). Falls back to `*` if unset.
- `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW` — optional (defaults 30 / 60s).
- `SENTRY_DSN` — optional error monitoring (no-op if unset).
- Job search (all optional): `RAPIDAPI_KEY`, `SERPAPI_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `ENABLE_SCRAPER`.
- Render build command must install Chromium only if you re-enable browser PDF (currently NOT needed — see below).

## Architecture notes
- **DB layer:** `get_db()/db_one/db_all/db_exec` abstract SQLite vs Postgres; `_q()` swaps `?`→`%s` for PG. `USE_PG = bool(DATABASE_URL)`. Inserts omit `created_at` (DB defaults handle it).
- **Auth:** PBKDF2-HMAC-SHA256 (200k iters) + per-user salt; opaque session tokens; 30-day expiry enforced in `get_user_from_token`. Never store plaintext passwords.
- **PDF generation:** pure-Python **reportlab** (`build_resume_pdf`, `build_cover_pdf`) — NOT Chromium. Chromium/Playwright was removed because it OOM-crashed Render's 512MB tier (an OOM kill can't be caught in code). `render_html_to_pdf` still exists but is unused/dead. 6 templates map to accent colors.
- **Auto-apply engine:** `POST /autofill-plan` — layered: ATS hints (Greenhouse/Lever/Workday/Ashby) → generic label rules → `form_maps` cache (field→role, no PII) → Gemini fallback. Sensitive fields (work auth, salary, demographics) always flagged for review.
- **Analytics:** `POST /track` → `events` table (event name + path only, no PII). Query the funnel in Supabase.
- **Per-user data:** `/resumes`, `/applications`, `/vault` endpoints, all gated by `get_user_from_token`.
- **Extension:** connects by reading `rf_token`/`rf_api_base` from the site's localStorage; auto-fills forms, human submits; reports to `/applications/add`. Packaged as `Frontend/resumeforge-extension.zip`.

## Differentiation / "Proof engine" (NEW — backend done, frontend UI pending)
Strategy pivot: not "more AI features" but **proof-backed, defensible applications for early-career devs**. Backend endpoints are built + logic-tested (Gemini output can't be tested without a key); **the frontend UI for these is the next phase**.
- `_fetch_github_evidence(username)` — top non-fork repos ranked by stars/recency, with README excerpt, deployed link (homepage), topics, languages.
- `POST /proof-resume` (#1) — returns `evidence` (projects w/ links) + `proof` (bullets each tagged with project, evidence, tech, `confidence: verified|inferred`). The foundation for the rest.
- `POST /defense-questions` + `POST /evaluate-answer` (#2) — interview questions from the exact resume, and truthfulness/quality scoring of answers. Voice loop should use the browser Web Speech API (SpeechRecognition + SpeechSynthesis) on the frontend.
- `POST /skill-roadmap` (#3) — target role → fit level + 5-7 day proof-building project plan.
- `POST /quality-gate` (#4) — resume + JD → apply/improve/stretch/skip verdict, evidence map, missing proof, top fixes.
- `POST /recruiter-page` (#5) — targeted single-file HTML recruiter page from evidence + JD (returns `html`).
All are in `RL_PATHS` (rate-limited). The "Proof & Prep" frontend panel (`#proofPanel` in `index.html`, isolated IIFE at bottom) already surfaces these 5 as tabs. **Remaining:** voice mock-interview (browser Web Speech API) for the Interview Prep tab. Narrow the launch to final-year CS students / new grads; get 5-10 paid beta users before expanding.

## Career Evidence Vault — Differentiator #1 (backend DONE + tested, UI PENDING)
Persistent, reviewable, provenance-tracked proof system. **Backend built and unit-tested in isolation** (acceptance H1–H5, H7 pass); the **full UI is the next task** and is the priority for Claude Code.
- **Tables** (schema.sql + init_db): `evidence_sources`, `evidence_items` (confidence_status verified|user_confirmed|ai_inferred, `user_approved`), `resume_claims` (provenance via `evidence_item_ids`), `job_evidence_matches`. Dual-DB via `_TRUE/_FALSE/_NOW` constants + `db_insert()` helper.
- **Endpoints (all auth-gated, user-scoped):** `POST /evidence/import-github` (repos→unapproved ai_inferred items), `GET /evidence` (filter status/category), `POST /evidence/update` (approve|reject|edit), `POST /evidence/add` (manual, approved), `POST /evidence/delete`, `POST /evidence/delete-github` (privacy), `POST /evidence-map` (JD→requirements matched vs APPROVED evidence only), `POST /evidence-resume` (bullets from APPROVED evidence only; drops unbacked bullets; persists provenance), `POST /resume/export-check` (blocks export of any claim lacking approved evidence).
- **Guardrails:** never fabricate metrics/scale/titles; each bullet must cite evidence IDs; server drops model bullets not backed by the user's approved evidence.
- **TODO (Claude Code):** (1) Vault dashboard UI (searchable, filter by confidence/source, approve/edit/reject/add); (2) per-project review questions (what did YOU build? solo/team? real metric? live demo?); (3) Evidence Map view in the tailor flow; (4) "Why this bullet?" control in the resume editor + wire `export-check` to block PDF export; (5) real GitHub OAuth (minimal scopes, private-repo consent); (6) run the pytest/type/lint/build suite and add H6 (existing generation/export still works). The truncation-prone OneDrive mount blocked running the real suite in Cowork — Claude Code on local disk can.

## Conventions
- Keep the DB layer dual-compatible (SQLite + Postgres).
- Frontend is vanilla JS in `index.html`; match the existing cream/brown theme (`--accent:#9a3b1c`).
- Test backend logic before shipping; the app must import cleanly.

## Known gotchas / history
- This project was built in Cowork over a OneDrive-synced folder, which caused **half-synced file truncation** in the old sandbox (broke a build + the extension ZIP once). On a **local filesystem via Claude Code this won't happen** — but if you ever see a file that looks cut off, re-read it fresh.
- Render free tier **sleeps** (cold starts). `/health` endpoint + an on-load warm-ping mitigate; set up an UptimeRobot ping to `/health` every 10 min for best results.

## Open to-do / ideas
- Monetization: "Upgrade to Pro" intent button + pricing page (freemium; auto-apply as paywall). Billing via Razorpay/Stripe.
- A private `/stats` page to view the analytics funnel without opening Supabase.
- Publish extension to Chrome Web Store (see `CHROME_STORE_GUIDE.md`) and/or free Edge Add-ons for true one-click install; then add an "Add to Chrome" button.
- Regenerate the 6 template thumbnails to match the new reportlab PDF look.
- Broaden extension ATS coverage; handle Workday multi-page + custom dropdowns.
- Mobile-responsiveness QA pass on `index.html`.
