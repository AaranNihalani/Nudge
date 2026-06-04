const elThread  = document.getElementById("thread");
const elComposer= document.getElementById("composer");
const elInput   = document.getElementById("input");
const elSend    = document.getElementById("send");

// ── Session ────────────────────────────────────────────────────────────────
function getSessionId() {
  const key = "nudge_session_id";
  let sid = localStorage.getItem(key);
  if (sid && sid.length > 8) return sid;
  sid = (globalThis.crypto?.randomUUID?.()) || `sid_${Math.random().toString(16).slice(2)}_${Date.now()}`;
  localStorage.setItem(key, sid);
  return sid;
}
const sessionId = getSessionId();

// ── Transcript persistence ─────────────────────────────────────────────────
function loadTranscript() {
  try {
    const raw = localStorage.getItem("nudge_transcript");
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(-200) : [];
  } catch { return []; }
}
function saveTranscript(items) {
  try { localStorage.setItem("nudge_transcript", JSON.stringify(items.slice(-200))); } catch {}
}
let transcript = loadTranscript();

// ── Markdown ───────────────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function renderMarkdown(text) {
  let s = escapeHtml(text);
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(https?:\/\/[^\s<]+[^\s<.)\]])/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.split("\n").map(line => {
    const m = /^(\s*)-\s+(.+)$/.exec(line);
    return m ? `${m[1]}• ${m[2]}` : line;
  }).join("\n");
  return s.replace(/\n/g, "<br />");
}

// ── Render a single message ────────────────────────────────────────────────
function renderMessage(m, isAppend = false) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${m.role === "me" ? "me" : "bot"}${m.pending ? " pending" : ""}`;
  if (m.id) wrap.dataset.id = m.id;

  const sender = document.createElement("div");
  sender.className = "msg-sender";
  sender.textContent = m.role === "me" ? "You" : "Nudge";

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";

  if (m.pending) {
    bubble.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';
  } else {
    bubble.innerHTML = renderMarkdown(m.text || "");
  }

  wrap.appendChild(sender);
  wrap.appendChild(bubble);

  if (isAppend) {
    elThread.appendChild(wrap);
    elThread.scrollTop = elThread.scrollHeight;
  }
  return wrap;
}

function rebuildThread() {
  elThread.innerHTML = "";
  transcript.forEach(m => renderMessage(m, true));
}

// ── Input state ────────────────────────────────────────────────────────────
let isSending = false;
function setSending(sending) {
  isSending = !!sending;
  elSend.disabled = isSending;
  // Never disable the input — disabling causes it to lose focus
}

// ── Send ───────────────────────────────────────────────────────────────────
async function sendMessage(text) {
  const trimmed = (text || "").trim();
  if (!trimmed || isSending) return;

  // Add user message
  const userMsg = { role: "me", text: trimmed, id: `u${Date.now()}` };
  transcript.push(userMsg);
  saveTranscript(transcript);
  renderMessage(userMsg, true);

  // Add pending bot message
  setSending(true);
  const pendingId = `b${Date.now()}`;
  const pendingEl = renderMessage({ role: "bot", text: "", pending: true, id: pendingId }, true);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: trimmed }),
    });

    const reply = res.ok
      ? String((await res.json()).reply || "")
      : `Error ${res.status}. Please try again.`;

    // Replace pending element with real message
    const botMsg = { role: "bot", text: reply, id: pendingId };
    transcript.push(botMsg);
    saveTranscript(transcript);

    const realEl = renderMessage(botMsg);
    pendingEl.replaceWith(realEl);
    elThread.scrollTop = elThread.scrollHeight;

  } catch {
    pendingEl.remove();
    const errMsg = { role: "bot", text: "Network error. Please try again." };
    transcript.push(errMsg);
    saveTranscript(transcript);
    renderMessage(errMsg, true);
  } finally {
    setSending(false);
  }
}

// ── Events ─────────────────────────────────────────────────────────────────
document.getElementById("chips").addEventListener("click", e => {
  const btn = e.target.closest("[data-send]");
  if (!btn) return;
  sendMessage(btn.dataset.send);
});

elComposer.addEventListener("submit", e => {
  e.preventDefault();
  const v = elInput.value;
  elInput.value = "";
  sendMessage(v);
});

document.getElementById("btnClear").addEventListener("click", () => {
  transcript = [];
  saveTranscript(transcript);
  localStorage.removeItem("nudge_session_id");
  location.reload();
});

// ── Init ───────────────────────────────────────────────────────────────────
rebuildThread();

if (transcript.length === 0) {
  const welcome = {
    role: "bot",
    text: "Hi, I'm Nudge.\n\nI help you compare loan options and find regulated alternatives to moneylenders in your area.\n\nSend **START** to begin. Once you've set your district, describe your loan in plain English — for example: *Need ₹5,000 for 30 days at 5% monthly from a moneylender.*",
  };
  transcript.push(welcome);
  saveTranscript(transcript);
  renderMessage(welcome, true);
}
