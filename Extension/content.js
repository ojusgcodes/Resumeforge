// ResumeForge Auto-Apply — content script
// Runs on every page. Two jobs:
//   (A) If this page is the ResumeForge app, copy the login token (and any
//       queued jobs) out of its localStorage so the extension can use them.
//   (B) If this page looks like a job-application form, show a floating panel
//       that fills the form from the user's vault + tailored resume. The user
//       always reviews and clicks Submit themselves.

(function () {
  if (window.top !== window && !document.querySelector("input,textarea,select")) return;

  // ---- (A) Sync ResumeForge site data — ONLY on trusted ResumeForge origins.
  // SECURITY: this content script must run on <all_urls> so it can fill forms on
  // any job site. Without the allowlist below, ANY page that happens to have an
  // "rf_token" key in its localStorage could hand a session token to the
  // extension — or plant a fake one. So we only read/sync ResumeForge
  // credentials when we're actually ON a ResumeForge page.
  // NOTE: if you move to a custom domain, add it to RF_ORIGINS.
  const RF_ORIGINS = [
    "https://resumeforge-opal.vercel.app",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
  ];
  const RF_TRUSTED = RF_ORIGINS.indexOf(location.origin) !== -1;

  if (RF_TRUSTED) {
    try {
      const apiBase = localStorage.getItem("rf_api_base");
      const token = localStorage.getItem("rf_token");
      if (apiBase || token) {
        // Let the site detect that the extension is installed.
        document.documentElement.setAttribute("data-rf-ext", "1");
        let queue = [];
        try { queue = JSON.parse(localStorage.getItem("rf_autoapply_queue") || "[]"); } catch (e) {}
        chrome.runtime.sendMessage({
          type: "syncSite",
          apiBase: apiBase || "",
          token: token || "",
          email: localStorage.getItem("rf_email") || "",
          siteUrl: location.origin,
          queue: Array.isArray(queue) ? queue : []
        });
      }
    } catch (e) { /* no localStorage access — ignore */ }
  }

  // ---- helpers ------------------------------------------------------------
  const FIELD_TYPES = ["text", "email", "tel", "url", "number", "search", "file"];
  const fieldRefs = {}; // key -> element
  let panel = null;

  function visible(el) {
    if (!el || el.disabled) return false;
    if (el.type === "hidden") return false;
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    // allow file inputs which are often visually hidden but real
    if (el.type === "file") return true;
    return r.width > 0 && r.height > 0;
  }

  function labelFor(el) {
    // explicit <label for=id>
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && l.textContent.trim()) return l.textContent.trim();
    }
    // aria-label / aria-labelledby
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label").trim();
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = lb.split(/\s+/).map(id => {
        const n = document.getElementById(id);
        return n ? n.textContent.trim() : "";
      }).join(" ").trim();
      if (t) return t;
    }
    // wrapping <label>
    const wrap = el.closest("label");
    if (wrap && wrap.textContent.trim()) return wrap.textContent.trim();
    // a nearby label-ish element above the field
    const prev = el.previousElementSibling;
    if (prev && /label|span|div|p/i.test(prev.tagName) && prev.textContent.trim().length < 80)
      return prev.textContent.trim();
    return el.getAttribute("placeholder") || el.getAttribute("name") || "";
  }

  function collectFields() {
    const out = [];
    let n = 0;
    const nodes = document.querySelectorAll("input, textarea, select");
    nodes.forEach(el => {
      const tag = el.tagName.toLowerCase();
      let type = tag === "select" ? "select" : tag === "textarea" ? "textarea" : (el.type || "text").toLowerCase();
      if (tag === "input" && !FIELD_TYPES.includes(type)) return; // skip checkbox/radio/submit/etc for v1
      if (!visible(el)) return;
      const key = "rf_" + (n++);
      fieldRefs[key] = el;
      let options = [];
      if (tag === "select") options = Array.from(el.options).map(o => o.textContent.trim()).filter(Boolean);
      out.push({
        key: key,
        label: (labelFor(el) || "").slice(0, 160),
        name: el.getAttribute("name") || el.id || "",
        type: type,
        placeholder: el.getAttribute("placeholder") || "",
        options: options
      });
    });
    return out;
  }

  function looksLikeApplication() {
    const fields = document.querySelectorAll("input[type=email], input[type=file], textarea");
    const txt = (document.body.innerText || "").toLowerCase();
    const hasApplyWords = /apply|application|resume|cv|cover letter|submit your/i.test(txt);
    return fields.length >= 1 && hasApplyWords;
  }

  // Framework-aware value setter (React/Vue/Angular listen for these events).
  function setValue(el, value) {
    const tag = el.tagName.toLowerCase();
    if (tag === "select") {
      const opt = Array.from(el.options).find(o =>
        o.value === value ||
        o.textContent.trim().toLowerCase() === String(value).trim().toLowerCase());
      if (opt) { el.value = opt.value; el.dispatchEvent(new Event("change", { bubbles: true })); return true; }
      return false;
    }
    const proto = tag === "textarea" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    return true;
  }

  function b64ToFile(base64, filename) {
    const bin = atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], filename, { type: "application/pdf" });
  }

  function attachFile(el, file) {
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      el.files = dt.files;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    } catch (e) { return false; }
  }

  function mark(el, kind) {
    if (!el) return;
    el.style.transition = "outline 0.2s";
    el.style.outline = kind === "review" ? "2px solid #e0a800" : "2px solid #2e9e5b";
    el.style.outlineOffset = "1px";
    if (kind === "review") el.title = "ResumeForge: please review this field";
  }

  function msg(o) { return new Promise(res => chrome.runtime.sendMessage(o, res)); }

  function _normUrl(u) {
    try {
      const a = document.createElement("a");
      a.href = u;
      return (a.host + a.pathname).toLowerCase().replace(/\/+$/, "");
    } catch (e) { return (u || "").toLowerCase(); }
  }

  // Find the queued job (set on the ResumeForge site) that matches this page,
  // so we attach the resume tailored for THIS exact posting.
  function matchQueuedJob(queue) {
    if (!queue || !queue.length) return null;
    const here = _normUrl(location.href);
    let best = null;
    for (const j of queue) {
      if (!j.url) continue;
      const n = _normUrl(j.url);
      if (!n) continue;
      if (n === here) return j;
      if (here.startsWith(n) || n.startsWith(here)) best = best || j;
    }
    return best;
  }

  // ---- the fill routine ---------------------------------------------------
  async function runFill(statusEl) {
    statusEl.textContent = "Reading the form…";
    const fields = collectFields();
    if (!fields.length) { statusEl.textContent = "No fillable fields found on this page."; return; }

    const state = await msg({ type: "getState" });
    if (!state || !state.token) {
      statusEl.innerHTML = "Not logged in. Open the extension popup to log in (or visit ResumeForge while signed in).";
      return;
    }

    statusEl.textContent = "Asking ResumeForge how to fill " + fields.length + " fields…";
    const resp = await msg({ type: "autofillPlan", url: location.href, fields: fields });
    if (!resp || !resp.ok || !resp.data || !resp.data.success) {
      statusEl.textContent = (resp && resp.data && resp.data.error) || "Could not get a fill plan.";
      return;
    }
    const plan = resp.data.plan || [];

    // Resolve a resume PDF once, only if the form needs a file.
    let pdfFile = null;
    if (plan.some(p => p.is_file && p.role === "resume_file")) {
      statusEl.textContent = "Preparing your resume PDF…";
      // Pick the job for THIS page: the active job if set, otherwise the
      // queued job whose URL matches the page we're on (so each application
      // uses its own tailored resume).
      let job = state.activeJob || matchQueuedJob(state.queue || []) || {};
      let resumeText = job.tailored_resume || "";
      if (!resumeText) {
        const lr = await msg({ type: "latestResume" });
        resumeText = (lr && lr.resume && lr.resume.content) || "";
      }
      if (resumeText) {
        const rendered = await msg({ type: "renderResume", resume: resumeText, name: (job.name || ""), role: (job.role || "") });
        if (rendered && rendered.ok) pdfFile = b64ToFile(rendered.base64, rendered.filename);
      }
    }

    let filled = 0, review = 0, files = 0;
    for (const p of plan) {
      const el = fieldRefs[p.key];
      if (!el) continue;
      if (p.is_file) {
        if (p.role === "resume_file" && pdfFile && attachFile(el, pdfFile)) { files++; mark(el, "ok"); }
        else mark(el, "review");
        continue;
      }
      if (p.needs_review) { review++; mark(el, "review"); continue; }
      if (p.value) { if (setValue(el, p.value)) { filled++; mark(el, "ok"); } }
    }

    statusEl.innerHTML =
      "<b>Filled " + filled + "</b> field(s)" +
      (files ? ", attached resume" : "") +
      ". <b style='color:#b8860b'>" + review + "</b> need your review (outlined in yellow). " +
      "Check everything, then click the site's <b>Submit</b> button yourself.";
  }

  // ---- floating panel UI --------------------------------------------------
  function buildPanel() {
    if (panel) return;
    panel = document.createElement("div");
    panel.id = "rf-autoapply-panel";
    panel.style.cssText = [
      "position:fixed", "right:18px", "bottom:18px", "z-index:2147483647",
      "width:300px", "background:#fff", "border:1px solid #d8d2cc",
      "border-radius:12px", "box-shadow:0 8px 30px rgba(0,0,0,.18)",
      "font:13px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif",
      "color:#2b2b2b", "overflow:hidden"
    ].join(";");
    panel.innerHTML =
      '<div style="background:#3f4a56;color:#fff;padding:10px 12px;display:flex;align-items:center;justify-content:space-between">' +
        '<span style="font-weight:700">ResumeForge Auto-Apply</span>' +
        '<span id="rf-close" style="cursor:pointer;opacity:.85">✕</span>' +
      '</div>' +
      '<div style="padding:12px">' +
        '<div id="rf-status" style="margin-bottom:10px;min-height:34px">Detected an application form on this page.</div>' +
        '<button id="rf-fill" style="width:100%;padding:9px;border:0;border-radius:8px;background:#2e9e5b;color:#fff;font-weight:700;cursor:pointer">⚡ Fill this form</button>' +
        '<button id="rf-applied" style="width:100%;padding:8px;margin-top:8px;border:1px solid #cfc9c3;border-radius:8px;background:#f6f4f1;cursor:pointer">✓ I submitted — log it to my tracker</button>' +
        '<div style="margin-top:9px;font-size:11px;color:#8a837c">You review and submit. Nothing is sent without you.</div>' +
      '</div>';
    document.documentElement.appendChild(panel);

    panel.querySelector("#rf-close").onclick = () => panel.remove();
    const status = panel.querySelector("#rf-status");
    panel.querySelector("#rf-fill").onclick = () => runFill(status).catch(e => status.textContent = "Error: " + e.message);
    panel.querySelector("#rf-applied").onclick = async () => {
      const st = await msg({ type: "getState" });
      const job = (st && st.activeJob) || matchQueuedJob((st && st.queue) || []) || {};
      const r = await msg({
        type: "logApplication",
        company: job.company || document.title.split("|")[0].trim(),
        role: job.role || "",
        url: location.href
      });
      status.textContent = (r && r.ok && r.data && r.data.success)
        ? "Logged to your ResumeForge tracker ✓"
        : "Couldn't log it — are you logged in?";
    };
  }

  // Listen for an explicit trigger from the popup, too.
  chrome.runtime.onMessage.addListener((m, s, send) => {
    if (m && m.type === "triggerFill") {
      buildPanel();
      const status = panel.querySelector("#rf-status");
      runFill(status).catch(e => status.textContent = "Error: " + e.message);
      send({ ok: true });
    }
    return true;
  });

  if (looksLikeApplication()) buildPanel();
})();
