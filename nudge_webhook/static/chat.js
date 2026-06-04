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
    const p = JSON.parse(raw);
    return Array.isArray(p) ? p.slice(-200) : [];
  } catch { return []; }
}
function saveTranscript(items) {
  try { localStorage.setItem("nudge_transcript", JSON.stringify(items.slice(-200))); } catch {}
}
let transcript = loadTranscript();

// ── Markdown renderer ──────────────────────────────────────────────────────

function escHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Inline markdown: bold, italic, code, links
function inlineMd(text) {
  let s = escHtml(text);
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/(https?:\/\/[^\s<"']+[^\s<"'.,;:!?])/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  return s;
}

// Render a run of lines as a numbered or unordered list
function renderList(lines, ordered) {
  const tag = ordered ? "ol" : "ul";
  let html = `<${tag}>`;
  let itemHtml = null;

  for (const line of lines) {
    const trimmed = line.trimStart();
    const indent = line.length - trimmed.length;

    const numM = /^(\d+)[.)]\s+(.*)$/.exec(trimmed);
    const bulM = /^[-*•]\s+(.*)$/.exec(trimmed);

    if (numM || bulM) {
      if (itemHtml !== null) html += `<li>${itemHtml}</li>`;
      itemHtml = inlineMd(numM ? numM[2] : bulM[1]);
    } else if (indent >= 2 && itemHtml !== null) {
      // Indented continuation — shown as a sub-line beneath the item
      itemHtml += `<span class="li-sub">${inlineMd(trimmed)}</span>`;
    } else if (trimmed) {
      // Non-indented orphan line — treat as new plain item
      if (itemHtml !== null) html += `<li>${itemHtml}</li>`;
      itemHtml = inlineMd(trimmed);
    }
  }
  if (itemHtml !== null) html += `<li>${itemHtml}</li>`;
  html += `</${tag}>`;
  return html;
}

// Render one block (a group of lines separated from others by blank lines)
function renderBlock(block) {
  const lines = block.split("\n").filter((l, i, a) => {
    // Keep all lines except leading/trailing blanks within a block
    return true;
  });
  if (!lines.length) return "";

  const first = lines[0].trimStart();

  // Horizontal rule
  if (lines.length === 1 && /^-{3,}$/.test(first)) return "<hr>";

  // Numbered list starts with "1." / "1)"
  if (/^\d+[.)]\s/.test(first)) return renderList(lines, true);

  // Bullet list starts with "- " / "* " / "• "
  if (/^[-*•]\s/.test(first)) return renderList(lines, false);

  // Regular paragraph — internal single newlines become <br>
  const html = lines.map(l => inlineMd(l)).join("<br>");
  return `<p>${html}</p>`;
}

// Main entry: split on blank lines, render each block
function renderMarkdown(text) {
  const raw = String(text || "").replace(/\r\n/g, "\n");
  // Split into blocks on 2+ newlines
  const blocks = raw.split(/\n{2,}/).map(b => b.trim()).filter(Boolean);
  return blocks.map(renderBlock).join("");
}

// ── Render a single message ────────────────────────────────────────────────
function renderMessage(m, append = false) {
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

  const elThread = document.getElementById("thread");
  if (append) {
    elThread.appendChild(wrap);
    elThread.scrollTop = elThread.scrollHeight;
  }
  return wrap;
}

function rebuildThread() {
  const elThread = document.getElementById("thread");
  elThread.innerHTML = "";
  transcript.forEach(m => renderMessage(m, true));
}

// ── Input state ────────────────────────────────────────────────────────────
let isSending = false;
function setSending(sending) {
  isSending = !!sending;
  document.getElementById("send").disabled = isSending;
}

// ── Send ───────────────────────────────────────────────────────────────────
async function sendMessage(text) {
  const trimmed = (text || "").trim();
  if (!trimmed || isSending) return;

  const elThread = document.getElementById("thread");
  const elInput  = document.getElementById("input");

  const userMsg = { role: "me", text: trimmed, id: `u${Date.now()}` };
  transcript.push(userMsg);
  saveTranscript(transcript);
  renderMessage(userMsg, true);

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

document.getElementById("composer").addEventListener("submit", e => {
  e.preventDefault();
  const elInput = document.getElementById("input");
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
    text: "Hi, I'm Nudge.\n\nI help you find regulated lending alternatives to moneylenders in your area.\n\nSend **START** to begin. Once you've set your district, describe your loan in plain English — for example: *Need ₹5,000 for 30 days at 5% monthly from a moneylender.*",
  };
  transcript.push(welcome);
  saveTranscript(transcript);
  renderMessage(welcome, true);
}
