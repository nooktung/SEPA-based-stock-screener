// Login for the published copy of the dashboard (Cloudflare Pages Function middleware, 2026-10-13).
//
// The user chose an account and password of their own over Cloudflare Access's email codes. This
// runs before EVERY request of the site -- the page, its JSON data, /api/mylist -- except the
// company logos (excluded in _routes.json, written by src/web/export.py). Without a valid session
// it answers the login form (a page) or 401 (data). When the accounts are not set up it lets
// nothing through (fail closed).
//
// Accounts: secret SITE_USERS = {"admin": {"role": "owner" | "viewer", "salt", "hash", "iter"}},
// PBKDF2-SHA256 of each password with its own salt, written by `python -m src.main site-user`
// (src/web/site_users.py): the password is typed at that prompt and kept in clear nowhere.
// "owner" may edit "Danh sách của tôi" (functions/api/mylist.js); "viewer" only reads.
// Session: cookie sepa_session = payload "." HMAC-SHA256(SESSION_SECRET) holding the name, a tag of
// the password hash (a new password ends the old sessions) and an expiry; HttpOnly, Secure,
// SameSite=Lax. The account is looked up again on every request, so a removed one is out at once.
// Failed logins are counted per IP in KV (binding MYLIST, key "login-fail:<ip>"): MAX_FAILS within
// FAIL_WINDOW_S and the form refuses until the window has passed since the last failure.
// PBKDF2 iterations are low by offline-cracking standards (the free plan allows ~10 ms of CPU per
// request); the hashes live only in Cloudflare's secrets and data/site_users.json, and online
// guessing is what the lockout stops.

const COOKIE = "sepa_session";
const SESSION_S = 7 * 24 * 3600;
const MAX_FAILS = 8;
const FAIL_WINDOW_S = 15 * 60;
const MAX_FORM = 2048;
const DEFAULT_ITER = 5000;
const enc = new TextEncoder();
// Burns the same PBKDF2 work for an unknown name, so the answer's timing does not tell names apart.
const DUMMY = { salt: "AAAAAAAAAAAAAAAAAAAAAA==", hash: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", iter: DEFAULT_ITER };

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)),
  (c) => c.charCodeAt(0));
const unb64 = (s) => Uint8Array.from(atob(String(s)), (c) => c.charCodeAt(0));
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function users(env) {
  try {
    const u = JSON.parse(env.SITE_USERS || "");
    return u && typeof u === "object" && !Array.isArray(u) ? u : null;
  } catch { return null; }
}
export const configured = (env) => {
  const u = users(env);
  return !!(u && Object.keys(u).length && String(env.SESSION_SECRET || "").length >= 32 && env.MYLIST);
};

function sameBytes(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

export async function passwordMatches(rec, password) {
  try {
    const key = await crypto.subtle.importKey("raw", enc.encode(String(password)), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: unb64(rec.salt),
      iterations: Number(rec.iter) || DEFAULT_ITER }, key, 256);
    return sameBytes(new Uint8Array(bits), unb64(rec.hash));
  } catch { return false; }
}

const hmacKey = (env) => crypto.subtle.importKey("raw", enc.encode(String(env.SESSION_SECRET)),
  { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
const tagOf = (rec) => String(rec.hash).slice(0, 12);

export async function makeSession(env, name, now = Date.now()) {
  const rec = users(env)[name];
  const body = b64url(enc.encode(JSON.stringify({ u: name, t: tagOf(rec), exp: Math.floor(now / 1000) + SESSION_S })));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(env), enc.encode(body));
  return `${body}.${b64url(sig)}`;
}

function cookie(request, name) {
  for (const part of (request.headers.get("Cookie") || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}

// {name, role} of a valid session, else null.
export async function readSession(request, env, now = Date.now()) {
  const raw = cookie(request, COOKIE);
  if (!raw || !configured(env)) return null;
  const [body, sig, extra] = raw.split(".");
  if (!body || !sig || extra !== undefined) return null;
  let p;
  try {
    if (!(await crypto.subtle.verify("HMAC", await hmacKey(env), unb64url(sig), enc.encode(body)))) return null;
    p = JSON.parse(new TextDecoder().decode(unb64url(body)));
  } catch { return null; }
  const rec = users(env)[p.u];
  if (!rec || p.t !== tagOf(rec) || !(p.exp > now / 1000)) return null;
  return { name: p.u, role: rec.role === "owner" ? "owner" : "viewer" };
}

const SECURITY = { "X-Robots-Tag": "noindex, nofollow", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin" };
const redirect = (to, headers = {}) => new Response(null,
  { status: 303, headers: { Location: to, "Cache-Control": "no-store", ...SECURITY, ...headers } });
const json = (body, status) => new Response(JSON.stringify(body),
  { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...SECURITY } });

// Only a path on this site, never the login page itself or another host ("//evil", "/\evil").
function safeNext(v) {
  const s = String(v || "/");
  return /^\/(?![\/\\])/.test(s) && !/^\/(login|logout)\b/.test(s) && !/[\r\n]/.test(s) ? s : "/";
}

function loginPage(next, msg = "", status = 200) {
  const html = `<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>Đăng nhập · SEPA Việt Nam</title><style>
:root{--bg:#f4f6f9;--card:#fff;--text:#16202c;--muted:#5d6b7b;--line:#d8dee6;--acc:#2f6fed;--err:#c0392b}
@media (prefers-color-scheme:dark){:root{--bg:#0f141b;--card:#171e27;--text:#e6ebf1;--muted:#93a1b1;--line:#2a3441;--acc:#5b8ff9;--err:#ff7a6b}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:16px}
form{width:100%;max-width:360px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px 24px}
.brand{display:flex;gap:10px;align-items:center;margin-bottom:18px}.logo{width:36px;height:36px;border-radius:9px;background:var(--acc);
color:#fff;display:grid;place-items:center;font-weight:700}.brand b{display:block}.brand span{color:var(--muted);font-size:13px}
label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
input{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:transparent;color:var(--text);font:inherit}
input:focus{outline:2px solid var(--acc);outline-offset:1px}
button{width:100%;margin-top:18px;padding:11px;border:0;border-radius:9px;background:var(--acc);color:#fff;font:inherit;font-weight:600;cursor:pointer}
.err{color:var(--err);font-size:14px;margin:10px 0 0}.foot{color:var(--muted);font-size:12px;margin:16px 0 0}
</style></head><body><form method="post" action="/login" autocomplete="on">
<div class="brand"><div class="logo">S</div><div><b>SEPA Việt Nam</b><span>Bản web chỉ đọc · cần đăng nhập</span></div></div>
<label for="u">Tài khoản</label><input id="u" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required autofocus>
<label for="p">Mật khẩu</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<input type="hidden" name="next" value="${esc(next)}">
${msg ? `<p class="err" role="alert">${esc(msg)}</p>` : ""}
<button type="submit">Đăng nhập</button>
<p class="foot">Công cụ nghiên cứu, không có chức năng đặt lệnh.</p>
</form></body></html>`;
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
    ...SECURITY } });
}

async function login({ request, env }) {
  const url = new URL(request.url);
  const next = safeNext(url.searchParams.get("next"));
  if (!configured(env)) return loginPage(next, "Trang chưa được cấu hình tài khoản.", 503);
  if (request.method === "GET" || request.method === "HEAD") {
    return (await readSession(request, env)) ? redirect(next) : loginPage(next);
  }
  if (request.method !== "POST") return new Response(null, { status: 405, headers: { Allow: "GET, POST" } });
  const origin = request.headers.get("Origin");
  if (origin && origin !== url.origin) return loginPage(next, "Yêu cầu không hợp lệ.", 403);
  if (Number(request.headers.get("Content-Length") || 0) > MAX_FORM) return loginPage(next, "Yêu cầu không hợp lệ.", 400);
  const failKey = `login-fail:${request.headers.get("CF-Connecting-IP") || "unknown"}`;
  const fails = Number(await env.MYLIST.get(failKey)) || 0;
  if (fails >= MAX_FAILS) return loginPage(next, "Sai quá nhiều lần. Hãy thử lại sau 15 phút.", 429);
  let form;
  try { form = await request.formData(); } catch { return loginPage(next, "Yêu cầu không hợp lệ.", 400); }
  const name = String(form.get("username") || "").trim().toLowerCase();
  const to = safeNext(form.get("next"));
  const rec = Object.prototype.hasOwnProperty.call(users(env), name) ? users(env)[name] : null;
  const ok = (await passwordMatches(rec || DUMMY, String(form.get("password") || ""))) && !!rec;
  if (!ok) {
    await env.MYLIST.put(failKey, String(fails + 1), { expirationTtl: FAIL_WINDOW_S });
    return loginPage(to, "Sai tài khoản hoặc mật khẩu.", 401);
  }
  if (fails) await env.MYLIST.delete(failKey);
  return redirect(to, { "Set-Cookie": `${COOKIE}=${await makeSession(env, name)}; Path=/; Max-Age=${SESSION_S}; HttpOnly; Secure; SameSite=Lax` });
}

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  if (url.pathname === "/login") return login(context);
  if (url.pathname === "/logout") {
    return redirect("/login", { "Set-Cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` });
  }
  const user = await readSession(request, env);
  if (!user) {
    const page = (request.method === "GET" || request.method === "HEAD")
      && (url.pathname === "/" || url.pathname.endsWith(".html") || (request.headers.get("Accept") || "").includes("text/html"));
    return page ? redirect(`/login?next=${encodeURIComponent(url.pathname + url.search)}`) : json({ error: "login" }, 401);
  }
  context.data.user = user;
  const res = await context.next();
  const out = new Response(res.body, res);
  out.headers.set("Cache-Control", "private, no-cache");   // never kept by a shared cache
  for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
  return out;
}
