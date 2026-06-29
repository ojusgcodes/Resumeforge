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

-- Helpful indexes
create index if not exists idx_resumes_user  on resumes(user_id);
create index if not exists idx_events_name  on events(event);
create index if not exists idx_apps_user     on applications(user_id);
create index if not exists idx_sessions_email on sessions(email);
create index if not exists idx_formmaps_sig  on form_maps(ats, signature);
