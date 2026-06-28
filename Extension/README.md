# ResumeForge Auto-Apply — Chrome Extension

Autofills job-application forms using your ResumeForge profile (the "vault")
and your tailored resume. **You always review the fields and click Submit
yourself** — the extension never submits for you. That's deliberate: it keeps
you in control, avoids bot-detection, and stays within job sites' terms.

## What it does

1. On a job-application page it shows a small panel (bottom-right).
2. Click **Fill this form**. It reads every field, asks your ResumeForge
   backend how to fill them (`/autofill-plan`), then types the values and
   attaches your resume PDF.
3. Fields it filled confidently get a **green** outline. Fields you should
   check — anything sensitive (work authorization, salary, demographics),
   low-confidence guesses, or custom screening questions — get a **yellow**
   outline.
4. You review, fix the yellow ones, and click the site's own **Submit**.
5. Click **"I submitted — log it"** to record the application in your
   ResumeForge tracker.

## Install (developer mode)

1. Open `chrome://extensions` in Chrome or Edge.
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select this `Extension` folder.
4. Pin the extension so its icon is visible.

## First-time setup

Open the extension popup and either:

- **Log in** with your ResumeForge email + password and set the **Backend URL**
  (e.g. `https://your-app.onrender.com`, or `http://127.0.0.1:8000` for local), **or**
- Just open ResumeForge in a tab while signed in — the extension copies your
  login token automatically the next time that tab loads.

## How it stays cheap and accurate

The backend solves each unique form layout once (hints → generic rules →
cache → AI) and caches the field→role mapping (never your personal data), so
common forms cost no AI calls at all. See `/autofill-plan` in `Backend/main.py`.

## Limitations (v1)

- Handles text, email, phone, URL, file, textarea and `<select>` fields.
  Checkboxes / radio groups and multi-page flows (e.g. Workday account
  creation) still need manual handling.
- Custom dropdown widgets that aren't real `<select>` elements may need a
  manual pick — they'll be outlined yellow.
- Best supported ATS: Greenhouse, Lever, Ashby, and most generic career
  pages. Workday partially.

## Privacy

- Your resume/vault data is sent only to **your own** ResumeForge backend.
- The form-layout cache on the server stores only generic field→role mappings,
  never any applicant's values.
- The extension requests broad host access because job forms live on many
  different domains; it only acts when you click **Fill**.
