// node cloudflare/test/login.test.mjs  (run by tests/test_cloudflare_function.py)
// The login middleware: nothing of the site without a session; right / wrong passwords; the
// lockout; a forged, expired or stale cookie; a removed account; open redirects; fail closed.
import assert from "node:assert/strict";
import { onRequest, passwordMatches, makeSession, readSession, configured } from "../functions/_middleware.js";

const SITE = "https://sepa-vn.pages.dev";
// The same vector as tests/test_site_users.py: Python writes what this checks.
const VECTOR = { salt: "MDEyMzQ1Njc4OWFiY2RlZg==", hash: "W/jVDKe8W6JXKFagk2XBOho6lJXhcU077vgnzseVkeg=", iter: 5000 };
assert.equal(await passwordMatches(VECTOR, "mật-khẩu thử"), true);
assert.equal(await passwordMatches(VECTOR, "mật-khẩu thu"), false);

const store = new Map();
const kv = { get: async (k) => store.get(k) ?? null, put: async (k, v) => { store.set(k, v); },
  delete: async (k) => { store.delete(k); } };
const env = { MYLIST: kv, SESSION_SECRET: "s".repeat(48),
  SITE_USERS: JSON.stringify({ admin: { role: "owner", ...VECTOR }, ban: { role: "viewer", ...VECTOR } }) };
let served = 0;
const run = (r, e = env) => { const data = {}; return onRequest({ request: r, env: e, data,
  next: async () => { served++; return new Response(`asset:${JSON.stringify(data.user)}`, { headers: { "Cache-Control": "public, max-age=0" } }); } }); };
const page = (path, headers = {}) => new Request(`${SITE}${path}`, { headers: { Accept: "text/html", ...headers } });
const form = (fields, headers = {}) => new Request(`${SITE}/login`, { method: "POST", body: new URLSearchParams(fields),
  headers: { "Content-Type": "application/x-www-form-urlencoded", Origin: SITE, "CF-Connecting-IP": "1.2.3.4", ...headers } });
const cookieOf = (res) => (res.headers.get("Set-Cookie") || "").split(";")[0];

// Nothing without a session: the page goes to the form, data answers 401; the asset is never served.
let r = await run(page("/"));
assert.equal(r.status, 303); assert.equal(r.headers.get("Location"), "/login?next=%2F");
r = await run(new Request(`${SITE}/data/stocks.json`));
assert.equal(r.status, 401);
r = await run(new Request(`${SITE}/site.json`));
assert.equal(r.status, 401); assert.equal(served, 0);
r = await run(page("/login"));
assert.equal(r.status, 200); assert.match(await r.text(), /name="password"/);

// Wrong password, unknown account: the same answer, counted per IP.
r = await run(form({ username: "admin", password: "sai", next: "/" }));
assert.equal(r.status, 401); assert.match(await r.text(), /Sai tài khoản hoặc mật khẩu/);
r = await run(form({ username: "khong-co", password: "mật-khẩu thử" }));
assert.equal(r.status, 401); assert.equal(store.get("login-fail:1.2.3.4"), "2");

// Right password (any case of the name): a session cookie, then the site.
r = await run(form({ username: " Admin ", password: "mật-khẩu thử", next: "/#tin" }));
assert.equal(r.status, 303); assert.equal(r.headers.get("Location"), "/#tin");
assert.match(r.headers.get("Set-Cookie"), /HttpOnly; Secure; SameSite=Lax/);
assert.equal(store.has("login-fail:1.2.3.4"), false);                                // the count is cleared
const admin = cookieOf(r);
r = await run(new Request(`${SITE}/data/stocks.json`, { headers: { Cookie: admin } }));
assert.equal(r.status, 200); assert.equal(await r.text(), 'asset:{"name":"admin","role":"owner"}');
assert.equal(r.headers.get("Cache-Control"), "private, no-cache");
r = await run(page("/login", { Cookie: admin }));
assert.equal(r.status, 303);                                                          // already in
r = await run(form({ username: "ban", password: "mật-khẩu thử" }, { "CF-Connecting-IP": "5.6.7.8" }));
r = await run(new Request(`${SITE}/x`, { headers: { Cookie: cookieOf(r) } }));
assert.equal(await r.text(), 'asset:{"name":"ban","role":"viewer"}');

// Open redirects are refused.
for (const next of ["//evil.example", "/\\evil.example", "https://evil.example", "/login?next=/"]) {
  r = await run(form({ username: "admin", password: "mật-khẩu thử", next }, { "CF-Connecting-IP": "9.9.9.9" }));
  assert.equal(r.headers.get("Location"), "/", next);
}
r = await run(form({ username: "admin", password: "mật-khẩu thử" }, { Origin: "https://evil.example" }));
assert.equal(r.status, 403);                                                          // cross-site login

// Lockout: after 8 failures from one IP even the right password is refused.
for (let i = 0; i < 8; i++) await run(form({ username: "admin", password: `sai-${i}` }, { "CF-Connecting-IP": "6.6.6.6" }));
r = await run(form({ username: "admin", password: "mật-khẩu thử" }, { "CF-Connecting-IP": "6.6.6.6" }));
assert.equal(r.status, 429);

// Forged, tampered, expired, stale or removed: no access.
const forged = await makeSession({ ...env, SESSION_SECRET: "x".repeat(48) }, "admin");
assert.equal(await readSession(page("/", { Cookie: `sepa_session=${forged}` }), env), null);
const [body, sig] = admin.split("=")[1].split(".");
const swapped = Buffer.from(JSON.stringify({ u: "admin", t: "x", exp: 9e9 })).toString("base64url");
assert.equal(await readSession(page("/", { Cookie: `sepa_session=${swapped}.${sig}` }), env), null);
assert.equal(await readSession(page("/", { Cookie: `sepa_session=${body}.${sig}.x` }), env), null);
const old = await makeSession(env, "admin", Date.now() - 8 * 24 * 3600 * 1000);
assert.equal(await readSession(page("/", { Cookie: `sepa_session=${old}` }), env), null);    // expired
const newPassword = { ...env, SITE_USERS: JSON.stringify({ admin: { role: "owner", ...VECTOR, hash: "AAAA" + VECTOR.hash.slice(4) } }) };
assert.equal(await readSession(page("/", { Cookie: admin }), newPassword), null);               // password changed
const removed = { ...env, SITE_USERS: JSON.stringify({ ban: { role: "viewer", ...VECTOR } }) };
assert.equal(await readSession(page("/", { Cookie: admin }), removed), null);

// Logout clears the cookie.
r = await run(page("/logout", { Cookie: admin }));
assert.equal(r.status, 303); assert.match(r.headers.get("Set-Cookie"), /^sepa_session=;.*Max-Age=0/);

// Fail closed: no accounts, a short secret or no KV -> nothing gets through.
for (const bad of [{ ...env, SITE_USERS: "" }, { ...env, SITE_USERS: "{}" }, { ...env, SESSION_SECRET: "short" }, { ...env, MYLIST: undefined }]) {
  assert.equal(configured(bad), false);
  r = await run(new Request(`${SITE}/data/stocks.json`, { headers: { Cookie: admin } }), bad);
  assert.equal(r.status, 401);
  r = await run(page("/login"), bad);
  assert.equal(r.status, 503);
}
assert.equal(served, 2);                                              // the two logged-in requests only
console.log("login middleware: ok");
