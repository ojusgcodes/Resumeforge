# ResumeForge — Project Guide (for Claude Code)

AI-powered resume builder + job-application platform. Turns a user's GitHub +
LinkedIn into a tailored resume, matches roles, searches jobs, tailors a resume
per job, and (via a Chrome extension) auto-fills applications. Includes accounts,
an application tracker, and resume history.

## ⚠️ Where deliverables go
- **`Business/`** — every non-code deliverable: plans, decks, outreach lists, pricing docs, target sheets, research PDFs, contact sheets. Anything about the **company** rather than the **code**. This folder is **gitignored** (`Business/` in `.gitignore`), so it can never be pushed — not even by `git add .`. Save all such files here by default; do not put them in the repo root.
- **The repo** — code only.
- **Never run `git add .`.** Stage files by name. The repo was public for its first 49 commits and `git add .` had already pushed the club-outreach target list and the day-one plan to it. (No API keys ever leaked — `.gitignore` covered `.env` and `users.db`.)

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
- Billing (all optional; unset = payments simply off): `CHECKOUT_LINK_INR`, `CHECKOUT_LINK_USD`, `RAZORPAY_WEBHOOK_SECRET`, `LEMONSQUEEZY_WEBHOOK_SECRET`, `ADMIN_KEY`, `ENFORCE_PRO` (0/1), `SEASON_PASS_MONTHS`/`SEASON_PASS_INR`/`SEASON_PASS_USD`, `DAILY_QUOTA_ANON`/`DAILY_QUOTA_FREE`/`DAILY_QUOTA_PRO`.
- Render build command must install Chromium only if you re-enable browser PDF (currently NOT needed — see below).

## Monetization — "Placement Season Pass" (₹599 / $29 for 3 months of Pro)
One-time pass, **not** a subscription: students are seasonal, so this matches how they buy (no churn, no e-mandate paperwork, nothing to cancel).
- **Provider-agnostic by design.** Checkout is just a **hosted payment link** whose URL lives in an env var — Razorpay Payment Link for India (`CHECKOUT_LINK_INR`), a Merchant of Record (Lemon Squeezy / Dodo / Paddle) for everyone else (`CHECKOUT_LINK_USD`). Going live = pasting two URLs into Render. No PCI surface; no card data ever hits this server.
- **Golden rule:** entitlement is granted **only** by a signature-verified webhook (`POST /webhook/razorpay`, `POST /webhook/lemonsqueezy`, HMAC-SHA256 over the raw body). Nothing the browser sends can make someone Pro.
- **Tables:** `subscriptions` (user_id, plan, expires_at epoch, provider…) and `payments` (`provider_payment_id` UNIQUE → webhook replay can't grant a second pass). Kept off `users` so no ALTER is ever needed. **Run `schema.sql` in Supabase** or billing writes fail in prod.
- **Endpoints:** `GET /billing/me` (plan + days left + prices), `POST /billing/checkout` ({region:"in"|"global"} → hosted URL), the two webhooks, and `POST /billing/grant` (needs `ADMIN_KEY`) to comp people — a free pass for a club's officers is cheap goodwill.
- **Enforcement** lives in `rate_limit_mw`: `_plan_from_auth()` (DB-backed, 2-min cached) → daily caps `DAILY_QUOTA_ANON=5` / `DAILY_QUOTA_FREE=15` / `DAILY_QUOTA_PRO=300`, plus `PRO_PATHS` (quality-gate, evidence-quality-gate, skill-roadmap, recruiter-page) returning **402** when `ENFORCE_PRO=1`. **`ENFORCE_PRO` defaults to 0** — the wall ships inert; flip it only once people would miss those features.
- **Frontend:** `#payOverlay` panel + IIFE at the bottom of `index.html`; nav item `#navUpgradeBtn`, `#planBadge` shows "PRO · Nd". A one-time `window.fetch` wrapper turns any **402/429 with `upgrade:true`** into the pricing panel, so every existing call site gets the paywall UX with zero changes (it `clone()`s the response, so callers still read the body).
- **Tests:** `Backend/test_billing.py` — lifts the real billing functions out of `main.py` with `ast` and runs them on a temp SQLite DB. Covers forged/tampered/missing signature, fail-closed on an unset secret, grant, **webhook replay**, pass **stacking**, and expiry. Run: `py -m pytest test_billing.py -v`.

## Architecture notes
- **DB layer:** `get_db()/db_one/db_all/db_exec` abstract SQLite vs Postgres; `_q()` swaps `?`→`%s` for PG. `USE_PG = bool(DATABASE_URL)`. Inserts omit `created_at` (DB defaults handle it).
- **Auth:** PBKDF2-HMAC-SHA256 (200k iters) + per-user salt; opaque session tokens; 30-day expiry enforced in `get_user_from_token`. Never store plaintext passwords.
- **PDF generation:** two engines, on purpose.
  - **WeasyPrint** (`render_template_pdf`) renders the 5 HTML templates (`TEMPLATE_SLATE/PHOTO/GREEN/CLASSIC/MAUVE` → `TEMPLATES`, filled by `_fill` + `_resume_context`). The site's thumbnails were generated from this same HTML, so **this is what makes the PDF match the preview**. Needs Pango/Cairo → hence `Backend/Dockerfile` (Render must be deployed as a **Docker** service, not native Python). No browser: it fits in 512MB where Chromium OOM-killed the box.
  - **reportlab** (`build_resume_pdf`, `build_cover_pdf`) is the fallback *and* the `professional` template — deliberately single-column and ATS-safe. `/download-resume-pdf` tries WeasyPrint, falls back to reportlab on any error, so a bad template degrades to an ugly PDF instead of a 500. `/render-resume` (used by the extension for auto-apply) stays on reportlab.
  - Do **not** reintroduce Playwright/Chromium — it OOM-crashed Render's 512MB tier, and an OOM kill can't be caught in code. `render_html_to_pdf` is dead; `playwright` was dropped from requirements (its import is guarded).
- **⚠️ Never reintroduce `latest_resume.txt` / `latest_contact.txt` / `tailored_resume.txt`.** These were process-wide files: the app wrote the current user's resume + name/email/phone to disk, and download endpoints read them back. Any anonymous caller could `GET /download-template/{t}`, `/download-professional-pdf` or `/download-tailored/{t}` and receive **the last user's resume and contact details**; the cover-letter and CV endpoints also fell back to `latest_contact.txt`, stamping someone else's email/phone onto another user's document. All three GETs now return **410** and the files are never written. Every download carries its own data in the request body.
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

## Career Evidence Vault — Differentiator #1 (DONE — backend + full UI + tests)
Persistent, reviewable, provenance-tracked proof system. **Backend + full frontend UI + pytest suite are complete and passing** (acceptance H1–H7). Verified end-to-end in a browser against a live backend (real GitHub import, approve, review-with-metric, share page, export block).
- **Tables** (schema.sql + init_db): `evidence_sources`, `evidence_items` (confidence_status verified|user_confirmed|ai_inferred, `user_approved`), `resume_claims` (provenance via `evidence_item_ids`), `job_evidence_matches`. Dual-DB via `_TRUE/_FALSE/_NOW` constants + `db_insert()` helper.
- **Endpoints (all auth-gated, user-scoped):** `POST /evidence/import-github` (repos→unapproved ai_inferred items), `GET /evidence` (filter status/category), `POST /evidence/update` (approve|reject|edit), `POST /evidence/add` (manual, approved), `POST /evidence/review` (project Q&A: contribution/solo-or-team/problem/demo → enriches + approves the item; a **user-typed** metric becomes a separate approved `metric` item — the ONLY way a number enters the vault), `POST /evidence/delete`, `POST /evidence/delete-github` (privacy), `POST /evidence-map` (JD→requirements matched vs APPROVED evidence only), `POST /evidence-resume` (bullets from APPROVED evidence only; drops unbacked bullets; persists provenance; returns `dropped_unsupported`), `POST /resume/export-check` (blocks export of any claim lacking approved evidence), `POST /proof-page` (private, opt-in share page from ONLY the selected approved items; never dumps imported README/source), `POST /github/oauth/url` + `GET /github/oauth/callback` (optional real OAuth, env-gated on `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`/`GITHUB_OAUTH_REDIRECT`; token used once, never stored; degrades gracefully when unset).
- **Guardrails:** never fabricate metrics/scale/titles; each bullet must cite evidence IDs; server drops model bullets not backed by the user's approved evidence. `_bullet_metric_supported()` deterministically strips any bullet whose numeric impact figure (%, $, Nx, large counts, "N users") isn't present in its cited evidence (H4).
- **Frontend:** self-contained `#vaultPanel` overlay + IIFE at the bottom of `index.html` (reuses the `pp-*` themed shell). Launchers: `#navVaultBtn` (nav, always visible) and `#openVaultBtn` (results area). Tabs: My Vault (search + filter by confidence/type/approved, import/add/approve/edit/reject/review/delete, delete-all-GitHub), Review Projects, Job Match (Evidence Map), Build Resume ("Why this bullet?" provenance + export-check-gated PDF), Share Proof (opt-in checklist → `/proof-page`). Auth-gated; sends `Authorization: Bearer <rf_token>`.
- **Tests:** `Backend/test_evidence_vault.py` + `Backend/conftest.py` — H1–H7, deterministic (temp SQLite via `RF_DB_PATH`, Gemini/GitHub mocked). Run: from `Backend/`, `py -m pytest test_evidence_vault.py` (uses the `py` launcher → Python 3.14; `python` hits the Win Store stub). All 15 pass.
- **Remaining/optional:** real GitHub OAuth is implemented but **untested end-to-end** (needs a registered OAuth app + secrets); the default public-username import is the tested path. `type/lint` not configured in this repo (no mypy/ruff config); PDF export uses reportlab (no build step).

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
