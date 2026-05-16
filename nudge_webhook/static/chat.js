const elThread = document.getElementById("thread");
const elComposer = document.getElementById("composer");
const elInput = document.getElementById("input");
const elSend = document.getElementById("send");
const elDebug = document.getElementById("debug");
const elContextKv = document.getElementById("contextKv");
const elBadges = document.getElementById("badges");
const elLoanCard = document.getElementById("loanCard");
const elLoanActions = document.getElementById("loanActions");

function nowLabel() {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function getSessionId() {
  const key = "nudge_web_session_id";
  const existing = localStorage.getItem(key);
  if (existing && existing.length > 8) return existing;
  const sid =
    (globalThis.crypto && crypto.randomUUID && crypto.randomUUID()) ||
    `sid_${Math.random().toString(16).slice(2)}_${Date.now()}`;
  localStorage.setItem(key, sid);
  return sid;
}

const sessionId = getSessionId();

function loadTranscript() {
  const raw = localStorage.getItem("nudge_web_transcript");
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.slice(-200);
  } catch (e) {}
  return [];
}

function saveTranscript(items) {
  try {
    localStorage.setItem("nudge_web_transcript", JSON.stringify(items.slice(-200)));
  } catch (e) {}
}

let transcript = loadTranscript();

function renderMessage(m) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${m.role === "me" ? "me" : "bot"}`;

  const body = document.createElement("div");
  body.className = "body";
  body.textContent = m.text || "";

  const meta = document.createElement("div");
  meta.className = "meta";
  const left = document.createElement("div");
  left.textContent = m.role === "me" ? "You" : "Nudge";
  const right = document.createElement("div");
  right.textContent = m.time || nowLabel();
  meta.appendChild(left);
  meta.appendChild(right);

  wrap.appendChild(body);

  if (m.debugSnippet) {
    const mini = document.createElement("div");
    mini.className = "mini";
    mini.textContent = m.debugSnippet;
    wrap.appendChild(mini);
  }

  wrap.appendChild(meta);
  elThread.appendChild(wrap);
  elThread.scrollTop = elThread.scrollHeight;
}

function rebuildThread() {
  elThread.innerHTML = "";
  transcript.forEach(renderMessage);
}

function setSending(sending) {
  elSend.disabled = !!sending;
  elInput.disabled = !!sending;
}

function kvRow(label, value) {
  const row = document.createElement("div");
  row.className = "kv-row";
  const a = document.createElement("span");
  a.textContent = label;
  const b = document.createElement("strong");
  b.textContent = value == null || value === "" ? "—" : String(value);
  row.appendChild(a);
  row.appendChild(b);
  return row;
}

function badge(text, kind) {
  const b = document.createElement("div");
  b.className = `badge ${kind || ""}`.trim();
  b.textContent = text;
  return b;
}

function formatPct(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = Number(x);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(n >= 100 ? 0 : 1)}%`;
}

function formatMoneyInr(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = Math.round(Number(x));
  if (!Number.isFinite(n)) return "—";
  return `₹${n.toLocaleString("en-IN")}`;
}

function updateSide(debug) {
  elDebug.textContent = JSON.stringify(debug || {}, null, 2);

  elContextKv.innerHTML = "";
  elContextKv.appendChild(kvRow("Session", sessionId.slice(0, 10)));
  elContextKv.appendChild(kvRow("Consent", debug?.consent_status));
  elContextKv.appendChild(kvRow("District", debug?.district));
  elContextKv.appendChild(kvRow("MFI districts loaded", debug?.mfi_districts));
  elContextKv.appendChild(kvRow("Policy", debug?.policy));
  elContextKv.appendChild(kvRow("Decision", debug?.decision));
  elContextKv.appendChild(kvRow("Parsed", debug?.parsed));
  elContextKv.appendChild(kvRow("Intent", debug?.intent ?? debug?.last_borrow_intent?.intent));
  elContextKv.appendChild(kvRow("Confidence", debug?.confidence ?? debug?.last_borrow_intent?.confidence));

  elBadges.innerHTML = "";
  if (debug?.consent_status) {
    elBadges.appendChild(
      badge(`consent: ${debug.consent_status}`, debug.consent_status === "granted" ? "good" : "bad")
    );
  }
  if (debug?.district) elBadges.appendChild(badge(`district: ${debug.district}`, "accent"));
  if (typeof debug?.mfi_districts === "number")
    elBadges.appendChild(badge(`mfi: ${debug.mfi_districts}`, debug.mfi_districts > 0 ? "good" : "bad"));
  if (debug?.policy) elBadges.appendChild(badge(`policy: ${debug.policy}`));
  if (debug?.decision) elBadges.appendChild(badge(`decision: ${debug.decision}`));

  const bi = debug?.last_borrow_intent;
  if (!bi) {
    elLoanCard.innerHTML = '<div class="muted">No loan parsed yet.</div>';
    elLoanActions.innerHTML = "";
    return;
  }

  const rows = [
    ["Intent", bi.intent == null ? "—" : String(bi.intent)],
    ["Confidence", bi.confidence == null ? "—" : String(bi.confidence)],
    ["Amount", formatMoneyInr(bi.amount_inr)],
    ["Tenure", bi.tenure_days == null ? "—" : `${bi.tenure_days} days`],
    ["APR", formatPct(bi.interest_rate_apr)],
    ["Lender type", bi.lender_type || "—"],
    ["Stage", bi.negotiation_stage || "—"],
    ["Model", bi.model || "—"],
  ];

  const t = document.createElement("table");
  rows.forEach(([k, v]) => {
    const tr = document.createElement("tr");
    const td1 = document.createElement("td");
    td1.textContent = k;
    const td2 = document.createElement("td");
    td2.textContent = v;
    tr.appendChild(td1);
    tr.appendChild(td2);
    t.appendChild(tr);
  });
  elLoanCard.innerHTML = "";
  elLoanCard.appendChild(t);

  elLoanActions.innerHTML = "";
  const mkAction = (label, text, kind) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `mini-btn ${kind || ""}`.trim();
    b.textContent = label;
    b.addEventListener("click", () => {
      elInput.value = text;
      elInput.focus();
    });
    return b;
  };
  elLoanActions.appendChild(mkAction("CORRECT amount", `CORRECT amount=${bi.amount_inr ?? ""}`, "accent"));
  elLoanActions.appendChild(mkAction("CORRECT tenure", `CORRECT tenure=${bi.tenure_days ?? ""}`));
  elLoanActions.appendChild(mkAction("CORRECT rate", `CORRECT rate=${bi.interest_rate_apr ?? ""}apr`));
  elLoanActions.appendChild(mkAction("CORRECT lender", `CORRECT lender_type=${bi.lender_type ?? ""}`));
  elLoanActions.appendChild(mkAction("Force intent", "CORRECT intent=true", "accent"));
};

function statusSnippet(debug) {
  const bits = [];
  if (debug?.policy) bits.push(`policy=${debug.policy}`);
  if (debug?.decision) bits.push(`decision=${debug.decision}`);
  if (debug?.parsed) bits.push(`parsed=${debug.parsed}`);
  const bi = debug?.last_borrow_intent;
  if (bi && (bi.amount_inr || bi.tenure_days || bi.interest_rate_apr)) {
    bits.push(
      `loan=${bi.amount_inr ?? "?"}/${bi.tenure_days ?? "?"}d/${bi.interest_rate_apr ?? "?"}%APR`
    );
  }
  return bits.length ? `[${bits.join(" | ")}]` : "";
}

async function sendMessage(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return;

  transcript.push({ role: "me", text: trimmed, time: nowLabel() });
  saveTranscript(transcript);
  rebuildThread();

  setSending(true);
  const pendingIdx = transcript.length;
  const pending = { role: "bot", text: "…", time: nowLabel(), debugSnippet: "" };
  transcript.push(pending);
  rebuildThread();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: trimmed }),
    });
    if (!res.ok) {
      const t = await res.text();
      pending.text = `Error: ${t || res.status}`;
      pending.debugSnippet = "";
      transcript[pendingIdx] = pending;
      saveTranscript(transcript);
      rebuildThread();
      return;
    }
    const data = await res.json();
    const reply = String(data.reply || "");
    const debug = data.debug || {};
    pending.text = reply;
    pending.debugSnippet = statusSnippet(debug);
    transcript[pendingIdx] = pending;
    saveTranscript(transcript);
    rebuildThread();
    updateSide(debug);
  } catch (e) {
    pending.text = "Network error. Try again.";
    pending.debugSnippet = "";
    transcript[pendingIdx] = pending;
    saveTranscript(transcript);
    rebuildThread();
  } finally {
    setSending(false);
  }
}

document.getElementById("chips").addEventListener("click", (e) => {
  const t = e.target;
  if (!t || !t.dataset || !t.dataset.send) return;
  sendMessage(t.dataset.send);
});

elComposer.addEventListener("submit", (e) => {
  e.preventDefault();
  const v = elInput.value;
  elInput.value = "";
  sendMessage(v);
});

document.getElementById("btnClear").addEventListener("click", () => {
  transcript = [];
  saveTranscript(transcript);
  rebuildThread();
  updateSide({});
});

document.getElementById("btnCopyDebug").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(elDebug.textContent || "");
  } catch (e) {}
});

rebuildThread();
if (transcript.length === 0) {
  transcript.push({
    role: "bot",
    text:
      "Welcome. This is the web version of Nudge.\n\nStart with START.\nIf you need a district list: DISTRICTS (and MORE).\nThen send your loan terms in plain English.",
    time: nowLabel(),
  });
  saveTranscript(transcript);
  rebuildThread();
}

fetch("/health")
  .then((r) => (r.ok ? r.json() : null))
  .then((j) => {
    if (!j) return;
    const d = {
      policy: undefined,
      decision: undefined,
      parsed: undefined,
      consent_status: undefined,
      district: undefined,
      mfi_districts: j.mfi_districts,
    };
    updateSide(d);
  })
  .catch(() => {});

