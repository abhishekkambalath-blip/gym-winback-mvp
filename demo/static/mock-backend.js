"use strict";
// Demo-only stand-in for the Python server: answers the same /api and /webhooks calls in the browser,
// with fake members and sample drafts. State lives in localStorage. Nothing leaves the page.
(() => {
  const STORE = "wb_demo_v1";
  const DAY = 86400000;
  const R = {
    drop: (a, b) => `Visits dropped from ${a}/week to ${b}/week over the last 14 days`,
    gone: (n) => `No check-in for ${n} days`,
    pay: "A payment failed in the last 30 days",
    ends: (n) => `Membership ends in ${n} days`,
  };
  // id, name, branch, score, reasons, favourite class, usual time
  const SEED = [
    [29, "Vivaan Patel", "Indiranagar", 85, [R.drop("2.9", "0.0"), R.gone(32), R.pay], "HIIT", "morning"],
    [7, "Ishita Shetty", "Indiranagar", 80, [R.drop("3.2", "0.0"), R.gone(22), R.ends(12)], "Zumba", "morning"],
    [60, "Neha Gowda", "Indiranagar", 75, [R.drop("3.1", "0.0"), R.gone(18), R.pay], "CrossFit", "evening"],
    [63, "Aditya Bhat", "Koramangala", 75, [R.drop("2.6", "0.0"), R.gone(17), R.pay], "HIIT", "morning"],
    [24, "Lakshmi Sharma", "HSR Layout", 70, [R.drop("3.9", "0.0"), R.gone(17), R.ends(11)], "Spin", "lunch"],
    [57, "Aditya Gowda", "Indiranagar", 70, [R.drop("4.0", "0.0"), R.gone(18), R.ends(8)], "Strength", "evening"],
    [72, "Vikram Rao", "HSR Layout", 65, [R.drop("3.5", "0.0"), R.gone(25)], "Yoga", "evening"],
    [140, "Kavya Verma", "Indiranagar", 65, [R.drop("1.9", "0.5"), R.pay, "New member (65 days in) whose visits are tailing off"], "Zumba", "evening"],
    [23, "Siddharth Nair", "HSR Layout", 30, [R.drop("3.4", "0.5")], "CrossFit", "evening"],
    [1, "Rohan Nair", "Indiranagar", 0, [], "Yoga", "morning"],
  ];
  const ANGLES = ["check_in", "free_pt", "pause_plan", "better_class_time", "payment_help"];
  const BRANCHES = ["Koramangala", "Indiranagar", "HSR Layout"];

  class HttpError extends Error { constructor(status, detail) { super(detail); this.status = status; } }
  const iso = (d = new Date()) => d.toISOString();
  const first = (m) => m.name.split(" ")[0];

  function compose(m, reasons) {
    const t = reasons.join(" ");
    const f = first(m);
    if (/Asked to (cancel|freeze)/.test(t))
      return ["pause_plan", "offer_pause_30", `Hi ${f}, thanks for letting us know. Before anything changes, you can pause free for up to 30 days and come back to your ${m.time} ${m.fav} whenever you're ready. Want me to set that up?`];
    if (/payment failed/.test(t))
      return ["payment_help", "offer_grace_7", `Hi ${f}, looks like your last payment didn't go through, probably a card hiccup. No stress, you have a 7-day grace period so your ${m.fav} sessions continue. You can update the card in the app. Need a hand?`];
    if (/New member/.test(t))
      return ["free_pt", "offer_pt_free", `Hi ${f}, we'd love to help you settle in. A free 30-minute session with a trainer can build a simple plan around your ${m.time} ${m.fav}. Want me to book one this week?`];
    if (/Membership ends/.test(t))
      return ["pause_plan", "offer_pause_30", `Hi ${f}, if things have been busy, you can pause free for up to 30 days and pick up your ${m.time} ${m.fav} right where you left off. Want me to set that up?`];
    return ["check_in", null, `Hi ${f}, we've missed you at ${m.time} ${m.fav}! There's always a spot for you. Shall I save you one this week?`];
  }

  function load() {
    try { const s = JSON.parse(localStorage.getItem(STORE)); if (s && s.members) return s; } catch (_) { /* fresh start */ }
    const s = { members: {}, drafts: [], next: 1 };
    SEED.forEach(([id, name, branch, score, reasons, fav, time]) => { s.members[id] = { id, name, branch, score, reasons, fav, time }; });
    [29, 7, 60].forEach((id, i) => makeDraft(s, s.members[id], "nightly", new Date(Date.now() - (i + 1) * 3600000)));
    save(s);
    return s;
  }
  const save = (s) => { try { localStorage.setItem(STORE, JSON.stringify(s)); } catch (_) { /* private mode */ } };

  function makeDraft(s, m, trigger, when = new Date()) {
    const [angle, offer, message] = compose(m, m.reasons);
    const d = { id: s.next++, member_id: m.id, member_name: m.name, branch: m.branch, score: m.score, reasons: [...m.reasons],
      message, status: "pending", edited_message: null, reject_reason: null, created_at: iso(when), sent_at: null, won_back: 0,
      won_back_at: null, channel: "whatsapp", offer_id: offer, angle,
      reasoning: `Sample draft: ${angle.replace(/_/g, " ")} fits what is flagged for ${first(m)}${offer ? ", using " + offer : ""}.`,
      reviewer: null, decided_at: null, delivery_status: null, replied: 0, trigger };
    s.drafts.push(d);
    return d;
  }
  const recent = (s, id) => s.drafts.some((d) => d.member_id === id && ["pending", "sent"].includes(d.status) && Date.now() - Date.parse(d.created_at) < 14 * DAY);

  /* ---------- stats (same rules as stats.py) ---------- */
  const pct = (a, b) => (b ? Math.round((a / b) * 100) + "%" : "n/a");
  function summarize(rows) {
    const sent = rows.filter((r) => r.status === "sent");
    const won = sent.filter((r) => r.won_back);
    const waiting = sent.filter((r) => !r.won_back);
    const inWindow = waiting.filter((r) => Date.now() - Date.parse(r.sent_at) < 14 * DAY);
    const edited = sent.filter((r) => r.edited_message);
    return { generated: rows.length, pending: rows.filter((r) => r.status === "pending").length, sent: sent.length,
      rejected: rows.filter((r) => r.status === "rejected").length, edited: edited.length, edited_rate: pct(edited.length, sent.length),
      won_back: won.length, win_rate: pct(won.length, sent.length), outcome_pending: inWindow.length, not_returned: waiting.length - inWindow.length };
  }

  /* ---------- routes ---------- */
  function route(method, path, params, body) {
    const s = load();
    let m;
    if (path === "/api/status") return { drafting: "sample", sender: "mock", threshold: 50, pending: s.drafts.filter((d) => d.status === "pending").length };
    if (path === "/api/drafts" && method === "GET") {
      const st = params.get("status");
      return s.drafts.filter((d) => !st || d.status === st);
    }
    if ((m = path.match(/^\/api\/drafts\/(\d+)\/(approve|reject)$/))) {
      const d = s.drafts.find((x) => x.id === Number(m[1]));
      if (!d) throw new HttpError(409, "Draft does not exist");
      if (d.status !== "pending") throw new HttpError(409, `Draft ${d.id} is '${d.status}', not pending`);
      d.reviewer = (body.reviewer || "staff").trim() || "staff";
      d.decided_at = iso();
      if (m[2] === "reject") {
        if (!body.reason || !body.reason.trim()) throw new HttpError(422, "A rejection reason is required");
        d.status = "rejected"; d.reject_reason = body.reason.trim();
      } else {
        const text = (body.message || "").trim();
        d.edited_message = text && text !== d.message.trim() ? text : null;
        d.status = "sent"; d.sent_at = iso();
      }
      save(s);
      return { ok: true };
    }
    if (path === "/api/stats") {
      const rows = s.drafts.map((d) => d);
      const counts = {};
      rows.filter((r) => r.status === "rejected" && r.reject_reason).forEach((r) => { const k = r.reject_reason.trim().toLowerCase(); counts[k] = (counts[k] || 0) + 1; });
      return { total: summarize(rows),
        branches: BRANCHES.map((b) => ({ branch: b, ...summarize(rows.filter((r) => r.branch === b)) })),
        angles: ANGLES.map((a) => { const sent = rows.filter((r) => r.status === "sent" && r.angle === a); const won = sent.filter((r) => r.won_back).length;
          return { angle: a, sent: sent.length, won_back: won, rate: pct(won, sent.length) }; }),
        rejections: Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([reason, count]) => ({ reason, count })) };
    }
    if (path === "/api/at-risk") {
      return Object.values(s.members).filter((x) => x.score >= 50).sort((a, b) => b.score - a.score).slice(0, 25).map((x) => ({
        member_id: x.id, name: x.name, branch: x.branch, score: x.score, level: x.score >= 60 ? "high" : x.score >= 35 ? "medium" : "low",
        reasons: x.reasons, drafted: recent(s, x.id) }));
    }
    if ((m = path.match(/^\/api\/members\/(\d+)\/draft$/))) {
      const mem = s.members[m[1]];
      if (!mem) throw new HttpError(404, "Unknown member");
      if (recent(s, mem.id)) throw new HttpError(409, "This member was already contacted in the last 14 days.");
      const d = makeDraft(s, mem, "manual"); save(s);
      return { drafted: 1, draft_ids: [d.id] };
    }
    if (path === "/api/nightly") {
      Object.values(s.members).filter((x) => x.score >= 50 && !recent(s, x.id)).sort((a, b) => b.score - a.score).slice(0, 3)
        .forEach((x) => makeDraft(s, x, "nightly"));
      save(s);
      return { accepted: true };
    }
    if (path === "/webhooks/gym-events") {
      const mem = s.members[body.member_id];
      if (!mem) throw new HttpError(404, "Unknown member");
      const add = (reason, points) => { if (!mem.reasons.includes(reason)) { mem.reasons.unshift(reason); mem.score = Math.min(100, mem.score + points); } };
      if (body.type === "check_in") {
        s.drafts.filter((d) => d.member_id === mem.id && d.status === "sent" && !d.won_back).forEach((d) => { d.won_back = 1; d.won_back_at = iso(); });
      } else if (body.type === "payment_failed") {
        add(R.pay, 20);
        if (mem.score >= 50 && !recent(s, mem.id)) makeDraft(s, mem, "payment_failed");
      } else if (body.type === "cancellation_request" || body.type === "freeze_request") {
        add(body.type === "cancellation_request" ? "Asked to cancel their membership" : "Asked to freeze their membership", body.type === "cancellation_request" ? 40 : 25);
        if (!recent(s, mem.id)) makeDraft(s, mem, body.type);
      }
      save(s);
      return { accepted: true };
    }
    throw new HttpError(404, "Not found");
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts = {}) => {
    const u = new URL(url, location.href);
    if (!u.pathname.startsWith("/api/") && !u.pathname.startsWith("/webhooks/")) return realFetch(url, opts);
    await new Promise((r) => setTimeout(r, 120 + Math.random() * 180));  // feel like a network call
    const reply = (data, status) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    try {
      const body = opts.body ? JSON.parse(opts.body) : {};
      const out = route((opts.method || "GET").toUpperCase(), u.pathname, u.searchParams, body);
      return reply(out, u.pathname === "/webhooks/gym-events" || u.pathname === "/api/nightly" ? 202 : 200);
    } catch (e) { return reply({ detail: e.message }, e.status || 500); }
  };

  /* ---------- demo chrome: banner, sign-in prefill, reset button ---------- */
  window.addEventListener("load", () => {  // after app.js has run, so its own sign-in setup cannot undo ours
    const bar = document.createElement("div");
    bar.textContent = "DEMO with fake members and sample drafts. Nothing is sent, and your changes stay in this browser.";
    bar.setAttribute("style", "position:sticky;top:0;z-index:20;background:#ffd60a;color:#0a0a0a;font-weight:700;font-size:.82rem;text-align:center;padding:.4rem .75rem");
    document.body.prepend(bar);
    document.documentElement.style.setProperty("--bar", bar.offsetHeight + "px");
    const key = document.getElementById("key"), name = document.getElementById("name");
    if (key && !key.value) { key.value = "demo"; key.placeholder = "Any text works in this demo"; }
    if (name && !name.value) name.value = "Demo manager";
    const form = document.getElementById("event-form");
    if (form) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn"; b.textContent = "Reset demo";
      b.addEventListener("click", () => { localStorage.removeItem(STORE); location.reload(); });
      form.append(b);
    }
  });
})();
