// node cloudflare/test/mylist.test.mjs  (run by tests/test_cloudflare_function.py)
// One list per account: each logged-in account reads and edits its own and never another's; the
// machine's own account is not written before the machine put its list there; a bad value is never
// written over; the cap comes from site.json; a cross-site request, a non-JSON body and a ticker
// outside the universe are refused. Who is logged in comes from the login middleware (data.user).
import assert from "node:assert/strict";
import { onRequestGet, onRequestPost } from "../functions/api/mylist.js";

const SITE = "https://sepa-vn.pages.dev";
const store = new Map();
const env = {
  MYLIST: { get: async (k) => store.get(k) ?? null, put: async (k, v) => { store.set(k, v); } },
  ASSETS: { fetch: async (u) => {
    const p = new URL(u).pathname;
    if (p === "/data/universe.json") return new Response(JSON.stringify(["PNJ", "ABB", "FPT", "HPG"]));
    if (p === "/site.json") return new Response(JSON.stringify({ static: true, local_account: "admin", mylist_max: 3 }));
    return new Response("", { status: 404 });
  } },
};
const admin = { user: { name: "admin", role: "owner" } }, ban = { user: { name: "ban", role: "viewer" } };
const req = (method, body, headers = {}) => new Request(`${SITE}/api/mylist`, {
  method, body: body === undefined ? undefined : JSON.stringify(body),
  headers: { ...(body === undefined ? {} : { "Content-Type": "application/json", Origin: SITE }), ...headers },
});
const call = async (fn, r, data) => { const res = await fn({ request: r, env, data }); return [res.status, await res.json()]; };
const add = (who, ticker, note) => call(onRequestPost, req("POST", { action: "add", ticker, note }), who);
const tickers = (b) => b.items.map((i) => i.ticker);

// The machine's account before the machine wrote its list: read as empty, never written over.
let [s, b] = await call(onRequestGet, req("GET"), admin);
assert.equal(s, 200); assert.deepEqual(b.items, []); assert.equal(b.user, "admin"); assert.equal(b.max, 3);
[s, b] = await add(admin, "PNJ");
assert.equal(s, 409); assert.equal(store.size, 0);
store.set("mylist:admin", JSON.stringify({ items: [{ ticker: "FPT", added_at: "2026-10-13T00:00:00+00:00", note: null }],
  updated_at: "2026-10-13T13:00:00+00:00" }));                                     // mylist-sync's first write

// Another account starts its own list at once, and the two never mix.
[s, b] = await add(ban, "pnj", " theo dõi ");
assert.equal(s, 200); assert.deepEqual(b.items.map((i) => [i.ticker, i.note]), [["PNJ", "theo dõi"]]);
assert.equal(b.can_edit, true); assert.equal(b.user, "ban");
[s, b] = await add(admin, "ABB");
assert.equal(s, 200); assert.deepEqual(tickers(b), ["FPT", "ABB"]);
[s, b] = await call(onRequestGet, req("GET"), ban);
assert.deepEqual(tickers(b), ["PNJ"]);                                             // not admin's
[s, b] = await call(onRequestGet, req("GET"), admin);
assert.deepEqual(tickers(b), ["FPT", "ABB"]);                                      // not ban's
assert.deepEqual([...store.keys()].sort(), ["mylist:admin", "mylist:ban"]);

// The cap (site.json mylist_max = 3 here; 40 in production), an existing ticker still updates.
[s, b] = await add(admin, "HPG");
assert.equal(s, 200);
[s, b] = await add(admin, "PNJ");
assert.equal(s, 400); assert.match(b.error, /tối đa 3 mã/);
[s, b] = await add(admin, "FPT", "giữ");
assert.equal(s, 200); assert.equal(b.items.find((i) => i.ticker === "FPT").note, "giữ");

// Refused: outside the universe, cross-site, a form post, no account; a bad value is left alone.
[s, b] = await add(ban, "ZZZ");
assert.equal(s, 400);
[s, b] = await call(onRequestPost, req("POST", { action: "add", ticker: "ABB" }, { Origin: "https://evil.example" }), ban);
assert.equal(s, 403);
[s, b] = await call(onRequestPost, new Request(`${SITE}/api/mylist`, { method: "POST", body: "action=add&ticker=ABB",
  headers: { "Content-Type": "application/x-www-form-urlencoded" } }), ban);
assert.equal(s, 400);
[s, b] = await add({}, "ABB");
assert.equal(s, 403);
[s, b] = await call(onRequestGet, req("GET"), {});
assert.equal(s, 401);
store.set("mylist:ban", "not json");
[s, b] = await add(ban, "ABB");
assert.equal(s, 409); assert.equal(store.get("mylist:ban"), "not json");
store.set("mylist:ban", JSON.stringify({ items: [{ ticker: "PNJ" }], updated_at: "x" }));

[s, b] = await call(onRequestPost, req("POST", { action: "remove", ticker: "PNJ" }), ban);
assert.equal(s, 200); assert.deepEqual(b.items, []);
[s, b] = await call(onRequestGet, req("GET"), admin);
assert.deepEqual(tickers(b), ["FPT", "ABB", "HPG"]);                               // untouched
console.log("mylist function: ok");
