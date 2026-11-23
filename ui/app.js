"use strict";
// Staff dashboard. All text is inserted with textContent / text nodes, never innerHTML.

const $ = (sel, root = document) => root.querySelector(sel);
const state = {
  key: sessionStorage.getItem("wb_key") || "",
  name: localStorage.getItem("wb_name") || "",
  tab: "queue",
  filter: "all",
  timer: null,
};
const TRIGGERS = { payment_failed: "Payment failed", cancellation_request: "Cancellation request",
                   freeze_request: "Freeze request", nightly: "Nightly scan", manual: "Staff request" };

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid !== null && kid !== undefined && kid !== false) n.append(kid.nodeType ? kid : String(kid));
  }
  return n;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.remove("show"), 3800);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", "X-Webhook-Secret": state.key },
  });
  if (res.status === 401) { logout("That access key was not accepted."); throw new Error("unauthorized"); }
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : `Something went wrong (${res.status})`);
  }
  return data;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

const words = (s) => s.trim().split(/\s+/).filter(Boolean).length;
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–");

/* ---------- sign in / out ---------- */
function showLogin(error = "") {
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-error").textContent = error;
  $("#name").value = state.name;
  $("#key").focus();
}
function logout(msg) {
  sessionStorage.removeItem("wb_key");
  state.key = "";
  clearInterval(state.timer);
  showLogin(msg || "");
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  state.key = $("#key").value.trim();
  state.name = $("#name").value.trim();
  try {
    await api("/api/status");
  } catch (err) { if (err.message !== "unauthorized") showLogin("Could not reach the server."); return; }
  sessionStorage.setItem("wb_key", state.key);
  localStorage.setItem("wb_name", state.name);
  $("#key").value = "";
  start();
});
$("#logout").addEventListener("click", () => logout());

/* ---------- shell ---------- */
async function refreshStatus() {
  const s = await api("/api/status");
  const draft = $("#mode-draft"), send = $("#mode-send");
  draft.textContent = s.drafting === "claude" ? "Drafts: Claude" : s.drafting === "sample" ? "Drafts: sample text (demo)" : "Drafts: placeholders (no API key)";
  draft.className = "pill " + (s.drafting === "claude" ? "solid" : "warn");
  send.textContent = s.sender === "mock" ? "Sending: practice mode, nothing leaves" : `Sending: ${s.sender}`;
  send.className = "pill " + (s.sender === "mock" ? "warn" : "solid");
  const c = $("#count-queue");
  c.textContent = s.pending;
  c.classList.toggle("zero", s.pending === 0);
  return s;
}

function start() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#who").textContent = `Signed in as ${state.name}`;
  const tab = location.hash.slice(1);
  show(["queue", "risk", "results", "activity"].includes(tab) ? tab : "queue");
  clearInterval(state.timer);
  state.timer = setInterval(() => { if (state.tab === "queue" && !document.hidden && !editing()) show("queue", true); }, 15000);
}
// Never refresh while a person is working on a card (typing, confirming or rejecting).
const editing = () => !!$(".card[data-touched]") || (document.activeElement && document.activeElement.tagName === "TEXTAREA");

async function show(tab, quiet = false) {
  state.tab = tab;
  location.hash = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  const view = $("#view");
  if (!quiet) view.replaceChildren(el("p", { class: "muted" }, el("span", { class: "spinner" }), " Loading…"));
  try {
    await refreshStatus();
    const nodes = await { queue: viewQueue, risk: viewRisk, results: viewResults, activity: viewActivity }[tab]();
    if (state.tab === tab) view.replaceChildren(...nodes);
  } catch (err) {
    if (err.message !== "unauthorized") view.replaceChildren(el("p", { class: "error" }, err.message));
  }
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
window.addEventListener("hashchange", () => {  // back/forward buttons and typed links
  const tab = location.hash.slice(1);
  if (!$("#app").hidden && tab !== state.tab && ["queue", "risk", "results", "activity"].includes(tab)) show(tab);
});

const head = (title, text) => el("div", { class: "head" }, el("h2", {}, title), el("p", {}, text));

/* ---------- review queue ---------- */
async function viewQueue() {
  const list = (await api("/api/drafts?status=pending")).sort((a, b) => b.score - a.score);
  const top = head("Review queue", "Every message waits here until a person approves it. You can edit the text first. Nothing is sent automatically.");
  if (!list.length) {
    return [top, el("div", { class: "empty" },
      el("div", { class: "big" }, "✓"), el("h3", {}, "All caught up"),
      el("p", { class: "muted" }, "No messages are waiting. Find members who need a nudge in the At risk tab."),
      el("button", { class: "btn primary", onclick: () => show("risk") }, "See at-risk members"))];
  }
  return [top, el("div", { class: "cards" }, list.map(draftCard))];
}

function levelOf(score) { return score >= 60 ? "high" : score >= 35 ? "medium" : "low"; }

function draftCard(d) {
  const first = d.member_name.split(" ")[0];
  const level = levelOf(d.score);
  const box = el("textarea", { rows: "4", "aria-label": `Message to ${first}` });
  box.value = d.message;
  const count = el("span", { class: "words" });
  const refresh = () => {
    const n = words(box.value);
    count.textContent = n > 60 ? `${n} words, over the 60-word guide` : `${n} words`;
    count.classList.toggle("over", n > 60);
  };
  box.addEventListener("input", () => { card.dataset.touched = "1"; refresh(); });
  refresh();

  const actions = el("div", { class: "actions" });
  const card = el("article", { class: "card" },
    el("div", { class: "card-head" },
      el("div", { class: `score ${level}`, title: "Risk score" }, el("span", {}, d.score, el("small", {}, level))),
      el("div", {},
        el("div", { class: "who-name" }, d.member_name),
        el("div", { class: "meta" },
          el("span", { class: "pill" }, d.branch),
          d.trigger && el("span", { class: "pill warn" }, TRIGGERS[d.trigger] || d.trigger),
          d.message.startsWith("[DEMO]") && el("span", { class: "pill" }, "Placeholder text")))),
    el("ul", { class: "chips", "aria-label": "Why flagged" }, d.reasons.map((r) => el("li", {}, r))),
    el("div", {}, box,
      el("div", { class: "msg-meta" }, count,
        el("span", {}, `${d.angle || "—"}${d.offer_id ? " · " + d.offer_id : ""}`))),
    d.reasoning && el("details", { class: "why" }, el("summary", {}, "Why this message?"), el("p", {}, d.reasoning)),
    actions);

  const busy = (on) => card.querySelectorAll("button").forEach((b) => (b.disabled = on));
  const done = (msg) => {
    card.classList.add("leaving");
    setTimeout(() => { card.remove(); refreshStatus(); if (!$(".card")) show("queue", true); }, 300);
    toast(msg);
  };
  const fail = (err) => { busy(false); if (err.message !== "unauthorized") toast(err.message, true); };

  const home = () => {
    actions.replaceChildren(
      el("button", { class: "btn primary", onclick: confirmSend }, "Approve & send"),
      el("button", { class: "btn danger", onclick: askReject }, "Reject"));
  };
  const confirmSend = () => {
    const edited = box.value.trim() !== d.message.trim();
    if (!box.value.trim()) return toast("The message is empty.", true);
    card.dataset.touched = "1";
    actions.replaceChildren(
      el("div", { class: "ask" }, edited ? `Send your edited version to ${first}?` : `Send this message to ${first}?`),
      el("button", { class: "btn primary", onclick: async () => {
        busy(true);
        try { await post(`/api/drafts/${d.id}/approve`, { message: box.value, reviewer: state.name });
              done(`Sent to ${first}${edited ? " (edited)" : ""}.`); } catch (e) { fail(e); }
      } }, "Yes, send"),
      el("button", { class: "btn", onclick: home }, "Back"));
  };
  const askReject = () => {
    const why = el("input", { type: "text", maxlength: "200", placeholder: "Reason, e.g. too pushy", "aria-label": "Rejection reason" });
    card.dataset.touched = "1";
    actions.replaceChildren(why,
      el("button", { class: "btn danger", onclick: async () => {
        if (!why.value.trim()) { why.focus(); return toast("Please give a short reason.", true); }
        busy(true);
        try { await post(`/api/drafts/${d.id}/reject`, { reason: why.value, reviewer: state.name });
              done(`Rejected the draft for ${first}.`); } catch (e) { fail(e); }
      } }, "Confirm reject"),
      el("button", { class: "btn", onclick: home }, "Back"));
    why.focus();
  };
  home();
  return card;
}

/* ---------- at risk ---------- */
async function viewRisk() {
  const list = await api("/api/at-risk");
  const nodes = [head("At-risk members", "Members whose visits, payments or requests suggest they may quit, highest risk first.")];
  if (!list.length) return [...nodes, el("div", { class: "empty" }, el("h3", {}, "Nobody is above the risk threshold right now."))];
  nodes.push(el("div", { class: "list" }, list.map((m) => {
    const btn = el("button", { class: "btn small", disabled: m.drafted }, m.drafted ? "Already contacted" : "Draft message");
    if (!m.drafted) btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.replaceChildren(el("span", { class: "spinner" }), " Drafting…");
      try {
        await post(`/api/members/${m.member_id}/draft`);
        btn.textContent = "In review queue";
        toast(`Draft for ${m.name.split(" ")[0]} is ready in the Review queue.`);
        refreshStatus();
      } catch (e) { btn.disabled = false; btn.textContent = "Draft message"; if (e.message !== "unauthorized") toast(e.message, true); }
    });
    return el("div", { class: "row-item" },
      el("div", { class: `score ${m.level}` }, el("span", {}, m.score)),
      el("div", {},
        el("div", { class: "who-name" }, m.name, " ", el("span", { class: "pill" }, m.branch)),
        el("ul", { class: "chips" }, m.reasons.map((r) => el("li", {}, r)))),
      btn);
  })));
  return nodes;
}

/* ---------- results ---------- */
async function viewResults() {
  const s = await api("/api/stats");
  const t = s.total;
  const kpi = (num, lbl, hero) => el("div", { class: "kpi" + (hero ? " hero" : "") }, el("div", { class: "num" }, num), el("div", { class: "lbl" }, lbl));
  const bar = (label, rate, text) => el("div", { class: "bar-row" }, el("span", {}, label),
    el("div", { class: "bar" }, el("i", { style: `width:${rate}%` })), el("span", { class: "bar-val" }, text));
  const num = (r) => (r === "n/a" ? 0 : parseInt(r, 10));
  const th = (...h) => el("tr", {}, h.map((x) => el("th", {}, x)));
  return [
    head("Results", "How many members came back after a message, and what staff changed along the way."),
    el("div", { class: "kpis" },
      kpi(t.win_rate, `Win-back rate (${t.won_back} of ${t.sent} sent)`, true),
      kpi(t.generated, "Drafts made"), kpi(t.sent, "Messages sent"),
      kpi(t.edited_rate, "Edited by staff"), kpi(t.outcome_pending, "Waiting for outcome")),
    el("div", { class: "two" },
      el("section", { class: "panel" }, el("h3", {}, "What worked, by approach"),
        s.angles.map((a) => bar(a.angle.replace(/_/g, " "), num(a.rate), a.sent ? `${a.won_back}/${a.sent} · ${a.rate}` : "n/a"))),
      el("section", { class: "panel" }, el("h3", {}, "Top reasons staff rejected a draft"),
        s.rejections.length ? el("ol", {}, s.rejections.map((r) => el("li", {}, `${r.reason} (${r.count})`))) : el("p", { class: "muted" }, "None yet."))),
    el("section", { class: "panel", style: "margin-top:1.25rem" }, el("h3", {}, "By branch"),
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, th("Branch", "Drafts", "Pending", "Sent", "Rejected", "Edited", "Won back", "Win rate", "Awaiting", "Not returned")),
        el("tbody", {}, s.branches.map((b) => el("tr", {}, [b.branch, b.generated, b.pending, b.sent, b.rejected, b.edited_rate, b.won_back, b.win_rate, b.outcome_pending, b.not_returned].map((v) => el("td", {}, v))))))))
  ];
}

/* ---------- activity ---------- */
async function viewActivity() {
  const all = (await api("/api/drafts")).reverse();
  const rows = all.filter((d) => state.filter === "all" || d.status === state.filter);
  const select = el("select", { "aria-label": "Filter by status", style: "width:auto", onchange: (e) => { state.filter = e.target.value; show("activity", true); } },
    ["all", "pending", "sent", "rejected"].map((v) => el("option", { value: v, selected: v === state.filter }, v === "all" ? "All statuses" : v)));
  const body = el("tbody");
  rows.forEach((d) => {
    const detail = el("tr", { hidden: true }, el("td", { colspan: "8", class: "wrap" }, el("div", { class: "compare" },
      el("div", {}, el("h4", {}, d.edited_message ? "Original draft" : "Message"), el("p", {}, d.message)),
      d.edited_message ? el("div", {}, el("h4", {}, "Sent version (edited by staff)"), el("p", {}, d.edited_message))
        : d.reject_reason ? el("div", {}, el("h4", {}, "Rejected because"), el("p", {}, d.reject_reason)) : null)));
    const row = el("tr", { class: "clickable", tabindex: "0", "aria-label": `Show message for ${d.member_name}` },
      [d.id, d.member_name, d.branch].map((v) => el("td", {}, v)),
      el("td", {}, el("span", { class: `status ${d.status}` }, d.status)),
      el("td", {}, TRIGGERS[d.trigger] || d.trigger || "–"), el("td", {}, d.angle || "–"),
      el("td", {}, d.reviewer ? `${d.reviewer}${d.edited_message ? " (edited)" : ""}` : "–"),
      el("td", {}, d.won_back ? `✓ back ${when(d.won_back_at)}` : d.status === "sent" ? "waiting" : "–"));
    const toggle = () => { detail.hidden = !detail.hidden; };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    body.append(row, detail);
  });
  return [head("Activity", "Every draft and what happened to it. Click a row to read the message."),
    el("div", { style: "margin-bottom:1rem" }, select),
    rows.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["#", "Member", "Branch", "Status", "Trigger", "Approach", "Reviewer", "Outcome"].map((h) => el("th", {}, h)))), body))
      : el("div", { class: "empty" }, el("h3", {}, "Nothing here yet."))];
}

/* ---------- demo tools ---------- */
$("#event-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { type: $("#ev-type").value, member_id: Number($("#ev-member").value) };
  try {
    const res = await fetch("/webhooks/gym-events", { method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json", "X-Webhook-Secret": state.key } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The event was rejected.");
    toast(body.type === "check_in" ? "Check-in recorded." : "Event accepted. A draft appears in the Review queue in a moment.");
    setTimeout(() => show(state.tab, true), 2500);
  } catch (err) { toast(err.message, true); }
});
$("#run-nightly").addEventListener("click", async () => {
  try { await post("/api/nightly"); toast("Nightly job started. Check the Review queue shortly."); setTimeout(() => show(state.tab, true), 4000); }
  catch (err) { if (err.message !== "unauthorized") toast(err.message, true); }
});

/* ---------- boot ---------- */
if (state.key) { api("/api/status").then(start).catch(() => showLogin()); } else { showLogin(); }
