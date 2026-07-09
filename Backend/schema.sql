-- ============================================================
-- ResumeForge — Supabase / Postgres schema
-- Run this once in Supabase → SQL Editor → New query → Run
-- ============================================================

-- Accounts (passwords stored ONLY as a salted hash, never plain text)
create table if not exists users (
    id            bigserial primary key,
    name          text,
    email         text unique not null,
    password_hash text not null,
    salt          text not null,
    created_at    timestamptz not null default now()
);

-- Login sessions (token-based)
create table if not exists sessions (
    token      text primary key,
    email      text not null,
    created_at timestamptz not null default now(),
    expires_at timestamptz
);

-- Generated resumes / CVs / cover letters history
create table if not exists resumes (
    id          bigserial primary key,
    user_id     bigint not null references users(id) on delete cascade,
    kind        text not null default 'resume',   -- resume | tailored | cv | cover_letter
    title       text,
    content     text not null,
    job_company text,
    job_role    text,
    created_at  timestamptz not null default now()
);

-- Application tracking history
create table if not exists applications (
    id            bigserial primary key,
    user_id       bigint not null references users(id) on delete cascade,
    company       text,
    role          text,
    url           text,
    status        text default 'Applied',
    applied_date  date,
    followup_date date,
    notes         text,
    resume_id     bigint references resumes(id) on delete set null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz
);

-- Profile vault (one row per user) — source of truth for autofill
create table if not exists profile_vault (
    user_id            bigint primary key references users(id) on delete cascade,
    full_name          text,
    email              text,
    phone              text,
    location           text,
    linkedin_url       text,
    github_url         text,
    portfolio_url      text,
    education          text,   -- JSON string
    experience         text,   -- JSON string
    skills             text,   -- JSON string
    work_authorization text,
    updated_at         timestamptz default now()
);

-- Cached form->role maps for the auto-apply autofill engine.
-- Stores ONLY the generic field->role mapping for a given form layout
-- (never any user's personal values), so the AI solves each unique form
-- template once and every later user reuses it for free.
create table if not exists form_maps (
    id         bigserial primary key,
    ats        text not null,
    signature  text not null,
    field_map  text not null,   -- JSON: { "<field key>": "<role>" }
    created_at timestamptz not null default now(),
    unique (ats, signature)
);

-- Lightweight, privacy-friendly product analytics. Stores event names and the
-- page path only — never resume content or personal data.
create table if not exists events (
    id         bigserial primary key,
    event      text not null,
    path       text,
    user_id    bigint,
    created_at timestamptz not null default now()
);

-- ============================================================
-- CAREER EVIDENCE VAULT (Differentiator #1: proof-backed resumes)
-- ============================================================

-- Where a piece of evidence came from (a repo, a README, a manual entry…).
create table if not exists evidence_sources (
    id             bigserial primary key,
    user_id        bigint not null references users(id) on delete cascade,
    source_type    text not null,   -- github_repository|github_readme|github_deployment|uploaded_resume|user_entry|profile_import
    source_url     text,
    source_title   text,
    source_content text,            -- excerpt / secure reference (never full private source)
    consent_status text default 'granted',
    imported_at    timestamptz not null default now()
);

-- An extracted, reviewable claim/fact. Nothing counts toward a resume until approved.
create table if not exists evidence_items (
    id                bigserial primary key,
    user_id           bigint not null references users(id) on delete cascade,
    source_id         bigint references evidence_sources(id) on delete set null,
    category          text not null,   -- project|skill|achievement|technical_decision|deployment|contribution|metric|education|experience
    title             text,
    description       text,
    structured_tags   text,            -- JSON array
    confidence_status text default 'ai_inferred',  -- verified|user_confirmed|ai_inferred
    user_approved     boolean default false,
    source_excerpt    text,
    source_url        text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz
);

-- A resume bullet with provenance back to the evidence it stands on.
create table if not exists resume_claims (
    id                bigserial primary key,
    resume_id         bigint,
    user_id           bigint not null references users(id) on delete cascade,
    text              text not null,
    claim_type        text,
    confidence_status text default 'ai_inferred',
    approved_by_user  boolean default false,
    evidence_item_ids text,            -- JSON array of evidence_items.id
    created_at        timestamptz not null default now(),
    updated_at        timestamptz
);

-- Result of matching a job requirement to the user's evidence.
create table if not exists job_evidence_matches (
    id                bigserial primary key,
    user_id           bigint not null references users(id) on delete cascade,
    job_ref           text,
    evidence_item_id  bigint references evidence_items(id) on delete cascade,
    matched_requirement text,
    match_strength    text,
    explanation       text,
    status            text,            -- supported|partial|missing|stretch
    created_at        timestamptz not null default now()
);

-- Shareable, clickable proof-backed resume pages (public via GET /p/{slug})
create table if not exists shared_proofs (
    slug       text primary key,
    user_id    bigint,
    title      text,
    data       text not null,
    views      integer not null default 0,
    created_at timestamptz not null default now()
);

-- Helpful indexes
create index if not exists idx_resumes_user  on resumes(user_id);
create index if not exists idx_events_name  on events(event);
create index if not exists idx_evsrc_user   on evidence_sources(user_id);
create index if not exists idx_evitem_user  on evidence_items(user_id);
create index if not exists idx_claims_user  on resume_claims(user_id);
create index if not exists idx_apps_user     on applications(user_id);
create index if not exists idx_sessions_email on sessions(email);
create index if not exists idx_formmaps_sig  on form_maps(ats, signature);
