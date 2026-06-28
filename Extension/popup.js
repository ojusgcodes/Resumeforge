// ResumeForge Auto-Apply — popup logic
function msg(o) { return new Promise(res => chrome.runtime.sendMessage(o, res)); }
function $(id) { return document.getElementById(id); }
function setStatus(t, cls) { const s = $("status"); s.textContent = t || ""; s.className = cls || ""; }

async function refresh() {
  const st = await msg({ type: "getState" });
  $("apiBase").value = st.apiBase || "";
  if (st.token) {
    $("loggedOut").classList.add("hide");
    $("loggedIn").classList.remove("hide");
    $("who").textContent = st.email || "your account";
    renderQueue(st.queue || []);
  } else {
    $("loggedOut").classList.remove("hide");
    $("loggedIn").classList.add("hide");
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
    const a = div.querySelector("a");
    a.onclick = async () => {
      await msg({ type: "setActiveJob", job: j });
      if (j.url) chrome.tabs.create({ url: j.url });
    };
    box.appendChild(div);
  });
}

$("loginBtn").onclick = async () => {
  const apiBase = $("apiBase").value.trim();
  if (apiBase) await msg({ type: "setApiBase", apiBase });
  setStatus("Logging in…");
  const r = await msg({ type: "login", email: $("email").value.trim(), password: $("password").value });
  if (r && r.ok) { setStatus("Logged in ✓", "ok"); refresh(); }
  else setStatus((r && r.error) || "Login failed.", "err");
};

$("logoutBtn").onclick = async () => { await msg({ type: "logout" }); setStatus("Logged out."); refresh(); };

$("apiBase").onchange = async () => {
  const apiBase = $("apiBase").value.trim();
  if (apiBase) { await msg({ type: "setApiBase", apiBase }); setStatus("Backend URL saved.", "ok"); }
};

$("fillBtn").onclick = async () => {
  setStatus("Triggering autofill on this tab…");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) { setStatus("No active tab.", "err"); return; }
  chrome.tabs.sendMessage(tab.id, { type: "triggerFill" }, (resp) => {
    if (chrome.runtime.lastError) {
      setStatus("Open the actual job page first, then click Fill.", "err");
    } else {
      setStatus("Filling — see the panel on the page.", "ok");
      window.close();
    }
  });
};

refresh();
