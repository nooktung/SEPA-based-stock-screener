// "Danh sách của tôi" on the published copy of the dashboard (Cloudflare Pages Function, 2026-10-13).
//
// GET  /api/mylist  -> { items: [{ticker, added_at, note}], updated_at, can_edit, user, max }
// POST /api/mylist  {action: "add" | "remove", ticker, note}
//
// One list per account of the login (user decision 2026-10-14): each logged-in account reads and
// edits its own, in Workers KV (binding MYLIST, key "mylist:<account>"), and never sees another's.
// The home machine reads every account's list (`python -m src.main mylist-sync`), brings the new
// tickers up to date (technicals, foreign flow, news) and publishes a new copy; its own dashboard
// writes through the list of site.local_account (written to /site.json by the export).
//
// Who: every request has passed the login (functions/_middleware.js), which puts the account in
// `data.user`. The body must be JSON (a cross-site form cannot send it without a preflight this
// function never answers) from the same origin. A ticker must be in /data/universe.json (listed
// 3-character shares with prices) -- a typo never becomes a phantom ticker -- and a list holds at
// most site.json's `mylist_max` (thresholds.yaml site.mylist_max_per_account). Nothing order-like
// exists here or anywhere in this system.
//
// Settings (Pages project -> Settings): KV binding MYLIST; secrets SITE_USERS and SESSION_SECRET
// (`python -m src.main site-user`).

const PREFIX = "mylist:";
const DEFAULT_MAX = 40;
const MAX_NOTE = 120;
const MAX_BODY = 2048;
const TICKER = /^[A-Z0-9]{3}$/;
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
});
const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");

async function asset(request, env, path) {
  try {
    const r = await env.ASSETS.fetch(new URL(path, request.url));
    return r.ok ? await r.json() : null;
  } catch { return null; }
}
async function site(request, env) {
  const s = (await asset(request, env, "/site.json")) || {};
  return { local: String(s.local_account || "admin"), max: Number(s.mylist_max) || DEFAULT_MAX };
}

// `state`: "ok", "missing" (never written) or "bad" (unreadable). An account's list may start
// empty -- except the machine's own (site.local_account): written over before the machine put its
// list there, `mylist-sync` would empty the list on the machine. A bad value is never written over.
async function readList(env, account) {
  const raw = await env.MYLIST.get(PREFIX + account);
  if (!raw) return { items: [], updated_at: null, state: "missing" };
  try {
    const data = JSON.parse(raw);
    return Array.isArray(data.items) ? { items: data.items, updated_at: data.updated_at || null, state: "ok" }
      : { items: [], updated_at: null, state: "bad" };
  } catch { return { items: [], updated_at: null, state: "bad" }; }
}

const accountOf = (data) => (data && data.user && data.user.name) || null;

export async function onRequestGet({ request, env, data }) {
  const account = accountOf(data);
  if (!account) return json({ error: "login" }, 401);
  const list = await readList(env, account);
  return json({ items: list.items, updated_at: list.updated_at, can_edit: true, user: account,
    max: (await site(request, env)).max });
}

export async function onRequestPost({ request, env, data }) {
  const self = new URL(request.url).origin;
  const origin = request.headers.get("Origin");
  if (origin && origin !== self) return json({ error: "forbidden" }, 403);
  if ((request.headers.get("Content-Type") || "").split(";")[0].trim() !== "application/json") return json({ error: "body" }, 400);
  if (Number(request.headers.get("Content-Length") || 0) > MAX_BODY) return json({ error: "body" }, 400);
  const account = accountOf(data);
  if (!account) return json({ error: "Cần đăng nhập để sửa danh sách." }, 403);
  let body;
  try { body = await request.json(); } catch { return json({ error: "body" }, 400); }
  const action = body && body.action;
  const ticker = String((body && body.ticker) || "").trim().toUpperCase();
  if (action !== "add" && action !== "remove") return json({ error: "action phải là add hoặc remove" }, 400);
  if (!TICKER.test(ticker)) return json({ error: "mã phải gồm đúng 3 ký tự (VD: PHP)" }, 400);
  const conf = await site(request, env);
  const list = await readList(env, account);
  if (list.state === "bad" || (list.state === "missing" && account === conf.local)) {
    return json({ error: "Danh sách trên web chưa được máy khởi tạo (chạy mylist-sync) — chưa ghi gì." }, 409);
  }
  const note = String((body && body.note) || "").trim().slice(0, MAX_NOTE) || null;
  if (action === "add") {
    const known = await asset(request, env, "/data/universe.json");
    if (!Array.isArray(known) || !known.includes(ticker)) {
      return json({ error: `không có mã ${ticker} trong danh sách cổ phiếu HOSE/HNX/UPCoM có dữ liệu giá` }, 400);
    }
    const had = list.items.find((i) => i.ticker === ticker);
    if (had) { if (note) had.note = note; }
    else if (list.items.length >= conf.max) return json({ error: `danh sách tối đa ${conf.max} mã` }, 400);
    else list.items.push({ ticker, added_at: nowIso(), note });
  } else {
    list.items = list.items.filter((i) => i.ticker !== ticker);
  }
  list.updated_at = nowIso();
  await env.MYLIST.put(PREFIX + account, JSON.stringify({ items: list.items, updated_at: list.updated_at }));
  return json({ ok: true, items: list.items, updated_at: list.updated_at, can_edit: true, user: account, max: conf.max });
}
