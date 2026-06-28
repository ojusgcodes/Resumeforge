// ResumeForge Auto-Apply — popup logic
function msg(o) { return new Promise(res => chrome.runtime.sendMessage(o, res)); }
function $(id) { return document.getElementById(id); }
function setStatus(t, cls) { const s = $("status"); s.textContent = t || ""; s.className = cls || ""; }

let STATE = {};

async function refresh() {
  STATE = await msg({ type: "getState" });
  if (STATE.token) {
    $("onboard").classList.add("hide");
    $("ready").classList.remove("hide");
    $("who").textContent = STATE.email || "your account";
    renderQueue(STATE.queue || []);
  } else {
    $("ready").classList.add("hide");
    $("onboard").classList.remove("hide");
    if (STATE.apiBase) $("apiBase").value = STATE.apiBase;
  }
}

function renderQueue(queue) {
  const wrap = $("queueWrap"), box = $("queue");
  if (!queue.length) { wrap.classList.add("hide"); return; }
  wrap.classList.remove("hide");
  box.innerHTML = "";
  queue.forEach(j => {
    const div = document.createElement("div");
    div.className = "queue-item";
    div.innerHTML = "<b>" + (j.role || "Role") + "</b> — " + (j.company || "") +
      '<br><a data-url="' + (j.url || "") + '">Open & set active →</a>';
    div.querySelector("a").onclick = async () => {
      await msg({ type: "setActiveJob", job: j });
      if (j.url) chrome.tabs.create({ url: j.url });
    };
    box.appendChild(div);
  });
}

// --- onboarding ---
$("openSiteBtn").onclick = () => {
  // Prefer the real site URL we learned from a previous visit; otherwise the
  // backend root (a FastAPI app usually serves the page there too).
  const url = STATE.siteUrl || STATE.apiBase || "https://resumeforge-backend-1bu3.onrender.com";
  chrome.tabs.create({ url });
  setStatus("Log in on that tab — I'll connect automatically. Reopen this popup after.", "ok");
};

$("advToggle").onclick = () => {
  const b = $("advBox");
  b.classList.toggle("hide");
  $("advToggle").textContent = b.classList.contains("hide") ? "Set it up manually instead ▾" : "Hide manual setup ▴";
};

$("loginBtn").onclick = async () => {
  const apiBase = $("apiBase").value.trim();
  if (apiBase) await msg({ type: "setApiBase", apiBase });
  setStatus("Logging in…");
  const r = await msg({ type: "login", email: $("email").value.trim(), password: $("password").value });
  if (r && r.ok) { setStatus("Connected ✓", "ok"); refresh(); }
  else setStatus((r && r.error) || "Login failed.", "err");
};

// --- connected ---
$("logoutBtn").onclick = async () => { await msg({ type: "logout" }); setStatus("Logged out."); refresh(); };

$("fillBtn").onclick = async () => {
  setStatus("Triggering autofill on this tab…");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) { setStatus("No active tab.", "err"); return; }
  chrome.tabs.sendMessage(tab.id, { type: "triggerFill" }, () => {
    if (chrome.runtime.lastError) setStatus("Open the actual job posting first, then click Fill.", "err");
    else { setStatus("Filling — see the panel on the page.", "ok"); window.close(); }
  });
};

refresh();
