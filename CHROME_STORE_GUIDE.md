# Publishing ResumeForge Auto-Apply to the Chrome Web Store

This is the **only** way to give users a true one-click *“Add to Chrome”* install
(no download, no unzip, no developer mode). Once it's live, we add an **Add to
Chrome** button to the site and users install in one click.

**Time:** ~20 min to submit · **Cost:** $5 one-time developer fee · **Review:** usually 1–3 days.

---

## What to upload

Upload this file (already built for you):

`resumeforge-extension-store.zip`  ← manifest + icons at the zip root (store format)

It now includes proper icons (16/48/128), which the store requires.

---

## Step by step

1. Go to the **Chrome Web Store Developer Dashboard**: https://chrome.google.com/webstore/devconsole
2. Sign in with a Google account and pay the **one-time $5** registration fee.
3. Click **“Add new item”** → upload `resumeforge-extension-store.zip`.
4. Fill in the **store listing** (copy below).
5. Fill in **Privacy practices** (justifications below) and set the **Privacy policy URL** to your live `privacy.html`.
6. Add at least **one screenshot** (1280×800 or 640×400). You can screenshot the extension panel on a job page, or reuse a frame from the demo video.
7. Choose visibility (**Public**) and submit for review.
8. After approval you'll get a public URL like `https://chromewebstore.google.com/detail/<id>`. Send it to me and I'll wire the **Add to Chrome** button into the site.

---

## Listing copy (paste these)

**Name:** ResumeForge Auto-Apply

**Summary (short, ≤132 chars):**
Auto-fill job applications with your ResumeForge profile and the right resume. You review and submit — one-click apply, done right.

**Category:** Productivity

**Description:**
ResumeForge Auto-Apply fills out job applications for you. Open any posting on Greenhouse, Lever, Ashby, Workday, or most company career pages, click “Fill this form,” and the extension instantly enters your name, email, phone, LinkedIn, GitHub, location and more — and attaches the resume tailored for that exact job.

You stay in control: confidently filled fields are marked green; anything sensitive or unusual (work authorization, salary, custom questions) is flagged for a quick review. The extension never submits on its own — you review and click submit yourself. Every application is logged to your ResumeForge tracker.

Connects automatically to your ResumeForge account — no codes or passwords to copy. Free to use.

To use this extension you need a free ResumeForge account at your ResumeForge site.

---

## Privacy practices (required answers)

**Single purpose:**
Auto-fill job application forms using the user's own saved profile data, at the user's explicit request.

**Permission justifications:**
- **storage** — to save the user's login token and settings locally.
- **activeTab / scripting** — to read and fill the form fields on the job page the user is currently on, only when they click “Fill”.
- **host permissions (`<all_urls>`)** — job applications live on many different company and ATS domains, so the extension must be able to run on any site the user opens to apply; it only acts on user click.

**Data usage disclosures:**
- The extension handles personal info (name, contact details) and sends it **only** to the user's own ResumeForge backend to fill forms. It does **not** sell data, does **not** use it for advertising, and does **not** transfer it to third parties beyond what's needed to provide the service.
- Privacy policy: link to your live `privacy.html`.

---

## After it's approved

Tell me the store URL and I'll replace the “Download & install” flow on
`extension.html` with a single **Add to Chrome** button pointing to it — the
one-click experience you wanted.
