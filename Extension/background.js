// ResumeForge Auto-Apply — background service worker (MV3)
// Centralizes all backend calls (cross-origin is allowed here via
// host_permissions) and stores the token / API base / job queue.

// Sensible default so the extension works out of the box; the live site also
// pushes its real backend URL automatically (see syncSite), so users never
// have to type it.
const DEFAULT_API_BASE = "https://resumeforge-backend-1bu3.onrender.com";

async function getState() {
  const s = await chrome.storage.local.get([
    "rf_api_base", "rf_token", "rf_email", "rf_queue", "rf_active_job", "rf_site_url"
  ]);
  return {
    apiBase: (s.rf_api_base || DEFAULT_API_BASE).replace(/\/+$/, ""),
    token: s.rf_token || "",
    email: s.rf_email || "",
    queue: s.rf_queue || [],
    activeJob: s.rf_active_job || null,
    siteUrl: s.rf_site_url || ""
  };
}

// Generic JSON call to the backend.
async function apiJson(method, path, body, withAuth) {
  const st = await getState();
  const headers = { "Content-Type": "application/json" };
  if (withAuth && st.token) headers["Authorization"] = "Bearer " + st.token;
  const res = await fetch(st.apiBase + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined
  });
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  return { ok: res.ok, status: res.status, data };
}

// Fetch a rendered PDF and hand it back to the content script as base64
// (Blobs don't survive message passing, base64 does).
async function renderResumePdf({ resume, name, role }) {
  const st = await getState();
  const res = await fetch(st.apiBase + "/render-resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume: resume || "", name: name || "", role: role || "", template: "classic" })
  });
  if (!res.ok) return { ok: false, status: res.status };
  const buf = await res.arrayBuffer();
  // base64 encode
  let binary = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return { ok: true, base64: btoa(binary), filename: (name || "Resume").replace(/[^A-Za-z0-9]+/g, "_") + "_Resume.pdf" };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "getState") {
        sendResponse(await getState());

      } else if (msg.type === "setApiBase") {
        await chrome.storage.local.set({ rf_api_base: (msg.apiBase || "").replace(/\/+$/, "") });
        sendResponse({ ok: true });

      } else if (msg.type === "syncSite") {
        // Called by the content script when it finds ResumeForge data in the
        // page's localStorage (backend URL / token / email / queue). Only
        // overwrites when a value is actually present.
        // SECURITY (defence in depth): never accept a session token unless the
        // message genuinely came from a ResumeForge page. The content script
        // already gates this, but we re-verify the sender here too.
        const TRUSTED_ORIGINS = [
          "https://resumeforge-opal.vercel.app",
          "http://localhost:8000",
          "http://127.0.0.1:8000",
          "http://localhost:3000",
          "http://127.0.0.1:3000"
        ];
        const senderUrl = (sender && sender.url) || "";
        const trusted = TRUSTED_ORIGINS.some(o => senderUrl === o || senderUrl.indexOf(o + "/") === 0);
        if (!trusted) {
          console.warn("ResumeForge: ignored syncSite from untrusted origin:", senderUrl);
          sendResponse({ ok: false, error: "untrusted origin" });
          return;
        }
        const patch = {};
        if (msg.apiBase) patch.rf_api_base = (msg.apiBase || "").replace(/\/+$/, "");
        if (msg.token) patch.rf_token = msg.token;
        if (msg.email) patch.rf_email = msg.email;
        if (msg.siteUrl) patch.rf_site_url = msg.siteUrl;
        if (Array.isArray(msg.queue)) patch.rf_queue = msg.queue;
        if (Object.keys(patch).length) await chrome.storage.local.set(patch);
        sendResponse({ ok: true });

      } else if (msg.type === "login") {
        const r = await apiJson("POST", "/login", { email: msg.email, password: msg.password }, false);
        if (r.ok && r.data && r.data.success) {
          await chrome.storage.local.set({ rf_token: r.data.token, rf_email: r.data.email });
          sendResponse({ ok: true, email: r.data.email });
        } else {
          sendResponse({ ok: false, error: (r.data && r.data.error) || "Login failed." });
        }

      } else if (msg.type === "logout") {
        await chrome.storage.local.remove(["rf_token", "rf_email"]);
        sendResponse({ ok: true });

      } else if (msg.type === "setActiveJob") {
        await chrome.storage.local.set({ rf_active_job: msg.job || null });
        sendResponse({ ok: true });

      } else if (msg.type === "autofillPlan") {
        sendResponse(await apiJson("POST", "/autofill-plan", { url: msg.url, fields: msg.fields }, true));

      } else if (msg.type === "latestResume") {
        const r = await apiJson("GET", "/resumes", null, true);
        const list = (r.data && r.data.resumes) || [];
        sendResponse({ ok: r.ok, resume: list[0] || null });

      } else if (msg.type === "renderResume") {
        sendResponse(await renderResumePdf(msg));

      } else if (msg.type === "logApplication") {
        sendResponse(await apiJson("POST", "/applications/add", {
          company: msg.company || "", role: msg.role || "", url: msg.url || "",
          status: "Applied"
        }, true));

      } else {
        sendResponse({ ok: false, error: "Unknown message type." });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message || e) });
    }
  })();
  return true; // keep the message channel open for the async response
});
