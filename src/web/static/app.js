"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1, sign = true) => (v === null || v === undefined) ? "–" : `${sign && v > 0 ? "+" : ""}${fmt(v, d)}%`;
const sfmt = (v, d = 0) => (v === null || v === undefined) ? "–" : `${v > 0 ? "+" : ""}${fmt(v, d)}`;
const tone = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const dmy = (s) => (s ? `${s.slice(8, 10)}/${s.slice(5, 7)}/${s.slice(0, 4)}` : "–");
const dm = (s) => (s ? `${s.slice(8, 10)}/${s.slice(5, 7)}` : "–");

// Static copy (python -m src.main export-site, published to Cloudflare Pages): no server; every
// /api/<route>?<query> the page asks is a JSON file at staticPath(), written by the same Python
// functions (src/web/export.py static_path). site.json says the page is that copy; the local
// server has none, so the same script runs in both places.
let SITE = null;
function staticPath(url) {
  const u = new URL(url, location.href);
  const safe = (s) => String(s).replace(/[^A-Za-z0-9_-]/g, "_");
  const route = safe(u.pathname.replace(/^\/api\//, ""));
  const ps = [...u.searchParams.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return ps.length ? `data/${route}/${ps.map(([k, v]) => `${safe(k)}-${safe(v)}`).join("_")}.json` : `data/${route}.json`;
}
// The web copy's login (cloudflare/functions/_middleware.js): a session that ended goes back to the form.
function toLogin() {
  location.href = `/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`;
  throw new Error("Phiên đăng nhập đã hết — đang chuyển tới trang đăng nhập.");
}
async function detectSite() {
  let s = null;
  try { const r = await fetch("site.json", { cache: "no-store" }); if (r.ok) s = await r.json(); } catch { /* local server */ }
  if (!s || !s.static) return;
  SITE = s;
  document.body.classList.add("static-site");
  $("#sidebar-logout").hidden = false;
  pageVersion = s.version;
  // A new copy is published after each session: offer a reload to a tab left open.
  setInterval(async () => {
    try { const r = await fetch("site.json", { cache: "no-store" }); if (r.ok) noteVersion((await r.json()).version); } catch { /* offline */ }
  }, 600000);
}

// Keep the first screen visible until initialization finishes; overlapping requests share it.
let loadingRequests = 1, loadingTimer;
function beginLoading() {
  loadingRequests++;
  if (!loadingTimer) loadingTimer = setTimeout(() => {
    loadingTimer = null;
    if (loadingRequests) $("#page-loading").hidden = false;
  }, 150);
}
function endLoading() {
  loadingRequests = Math.max(0, loadingRequests - 1);
  if (!loadingRequests) {
    clearTimeout(loadingTimer); loadingTimer = null;
    $("#page-loading").hidden = true;
  }
}
$("#page-loading-retry").addEventListener("click", () => location.reload());
async function get(url) {
  beginLoading();
  try { return await fetchData(url); }
  finally { endLoading(); }
}
async function fetchData(url) {
  if (SITE && url.startsWith("/api/")) {
    const t = new URL(url, location.href).searchParams.get("ticker");
    const missing = t ? `${t} chưa có dữ liệu chi tiết trong bản cập nhật này. Hãy tải lại trang hoặc thử lại sau.` : "Bản web chưa có dữ liệu cho mục này. Hãy tải lại trang.";
    if (t && Array.isArray(SITE.detail) && !SITE.detail.includes(t)) throw new Error(missing);
    const r = await fetch(staticPath(url), { cache: "no-cache" });
    if (r.status === 401) toLogin();
    if (!r.ok || !(r.headers.get("Content-Type") || "").includes("application/json")) throw new Error(missing);
    try { return await r.json(); } catch { throw new Error("Dữ liệu chi tiết không hợp lệ. Hãy tải lại trang sau khi bản web cập nhật."); }
  }
  const r = await fetch(url);
  noteVersion(r.headers.get("X-App-Version"));
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || `${url}: ${r.status}`); }
  return r.json();
}
// A tab left open across an update kept running the old script against new data ("APPROACHING"
// shown raw, a stock still in the group of the earlier screen run). The server tags every answer
// with the page files' and the latest screen run's version; when it changes, offer a reload.
let pageVersion = null;
function noteVersion(v) {
  if (!v) return;
  if (pageVersion === null) { pageVersion = v; return; }
  if (v === pageVersion || document.getElementById("reload-note")) return;
  const b = document.createElement("button");
  b.id = "reload-note"; b.type = "button"; b.className = "reload-note";
  b.textContent = SITE ? "Đã có dữ liệu phiên mới — bấm để tải lại" : "Trang web hoặc lượt lọc đã được cập nhật — bấm để tải lại";
  b.onclick = () => location.reload();
  document.body.appendChild(b);
}
async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${url}: ${r.status}`);
  return data;
}
const store = {
  get(k, dflt) { try { const v = localStorage.getItem(k); return v === null ? dflt : JSON.parse(v); } catch { return dflt; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } },
};

// ---------------------------------------------------------------- vocabulary
const BASE_TEXT = {
  VCP: "VCP", FLAT_BASE: "Nền phẳng", CUP_HANDLE: "Cốc tay cầm", CUP: "Cốc chưa có tay cầm", BASE: "Nền", NEAR_HIGH: "Sát đỉnh, chưa có nền",
  SHARP_PULLBACK: "Vừa giảm mạnh", EXTENDED: "Đã chạy xa", TOO_DEEP: "Điều chỉnh quá sâu", BROKEN: "Thủng nền", NONE: "–",
};
const EVENT_TEXT = {
  BREAKOUT: "Breakout hợp lệ", BREAKOUT_WEAK: "Breakout yếu", BREAKOUT_LOW_VOLUME: "Breakout thiếu khối lượng", BREAKOUT_FAILED: "Breakout thất bại",
  NEW_HIGH_NO_BASE: "Đỉnh mới, chưa có nền", STOP_BREACHED: "Thủng stop", TREND_TEMPLATE_BROKEN: "Mất tiêu chí xu hướng",
  TREND_TEMPLATE_RESTORED: "Đạt lại tiêu chí xu hướng", MA50_BREAK_HEAVY_VOLUME: "Thủng MA50, khối lượng lớn", LARGEST_DOWN_DAY: "Phiên giảm mạnh nhất",
  SETUP_READY: "Nền sẵn sàng", CLIMAX_WARNING: "Cảnh báo tăng nóng (climax)",
};
const MARKET_TEXT = { UPTREND: "Xu hướng tăng", UNDER_PRESSURE: "Tăng nhưng chịu áp lực", CORRECTION: "Đang điều chỉnh", MIXED: "Trung tính", UNKNOWN: "Chưa rõ" };
// "Ở vùng pivot" = from the pivot to +5% (SEPA's buy zone); under the pivot is "Sắp tới pivot" (tester R3-01).
const STATUS_LABEL = { AT_PIVOT: "Ở vùng pivot", BREAKOUT: "Vừa breakout", APPROACHING: "Sắp tới pivot", WATCH: "Đang theo dõi", INVALIDATED: "Mất hiệu lực" };
const STATUS_ORDER = { AT_PIVOT: 0, BREAKOUT: 1, APPROACHING: 2, WATCH: 3, INVALIDATED: 4 };
const STATUS_TITLE = { AT_PIVOT: "Giá từ pivot tới +5% (vùng mua theo SEPA) — không phải tín hiệu mua",
  APPROACHING: "Giá dưới pivot tối đa 5%: chưa có điểm mua, chờ vượt pivot", BREAKOUT: "Breakout hợp lệ trong 5 phiên, giá vẫn trên pivot",
  WATCH: "Còn thiếu xu hướng, nền hoặc điểm vào", INVALIDATED: "Breakout thất bại, thủng stop hoặc mất tiêu chí xu hướng trong 5 phiên" };
const TIER_LABEL = { STRONG_LEADER: "Dẫn dắt mạnh", LEADER: "Dẫn dắt", QUALIFIED: "Đạt" };
const LIQ_LABEL = { HIGH: "Cao", GOOD: "Khá", LOW: "Thấp", UNDER: "Dưới 3 tỷ" };
const LIQ_TITLE = { HIGH: "Giá trị giao dịch ≥ 10 tỷ đồng/phiên", GOOD: "8–10 tỷ đồng/phiên",
  LOW: "3–8 tỷ đồng/phiên — khó mua bán lượng lớn, dễ kẹt khi giảm sàn liên tiếp", UNDER: "Dưới ngưỡng lọc 3 tỷ đồng/phiên" };
const FUND_LABEL = { "A+": "A+", A: "A", B: "B", WEAK: "Yếu", FAIL: "Kém", NO_DATA: "–" };
const FUND_ORDER = { "A+": 0, A: 1, B: 2, WEAK: 3, FAIL: 4, NO_DATA: 5 };
const GROUP_TEXT = { A: "Đạt đủ 8/8 tiêu chí xu hướng", B: "Gần đạt, đang tạo nền", C: "Theo dõi mở rộng — chưa đạt chuẩn SEPA" };

const liqOf = (r) => r.liquidity_tier || (r.liquidity_vnd != null ? "UNDER" : "");
const liqBadge = (tier) => tier ? `<span class="lq lq-${esc(tier)}" title="${esc(LIQ_TITLE[tier] || "")}">${esc(LIQ_LABEL[tier] || tier)}</span>` : "";
const fundClass = (g) => (g === "A+" ? "Ap" : g || "");
const fundBadge = (f) => `<span class="fg fg-${esc(fundClass(f && f.grade))}" title="${esc((f && f.grade_text ? f.grade_text + ((f.reasons || []).length ? " — " + f.reasons.join("; ") : "") : "") || "chưa có dữ liệu")}">${esc(FUND_LABEL[f && f.grade] || "–")}</span>`;
const stBadge = (st) => (st ? `<span class="stbadge st-${esc(st)}" title="${esc(STATUS_TITLE[st] || "")}">${esc(STATUS_LABEL[st] || st)}</span>` : '<span class="muted">–</span>');
// Company logo (python -m src.main logos); a tinted tile with the code when none is stored.
const hue = (t) => [...t].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const logoTile = (t, size = "") => (state.logos.has(t)
  ? `<span class="clogo${size}" style="--h:${hue(t)}"><img src="${SITE ? `logo/${esc((SITE.logos || {})[t] || t)}` : `/logo/${esc(t)}`}" alt="" loading="lazy" onerror="this.parentNode.classList.add('mono');this.replaceWith('${esc(t)}')"></span>`
  : `<span class="clogo mono${size}" style="--h:${hue(t)}" aria-hidden="true">${esc(t)}</span>`);
// Above a million shares the exact count stops helping a scan: 55.332.492 -> 55,3 tr.
const vol = (v, signed = false) => (v == null ? "–" : Math.abs(v) >= 1e6 ? `${signed && v > 0 ? "+" : ""}${fmt(v / 1e6, 1)} tr` : signed ? sfmt(v) : fmt(v));

// ---------------------------------------------------------------- state
const state = {
  rows: [], run: null, group: "main", view: store.get("sepa.view.v2", "overview"),
  q: "", grp: "", status: "", trend: "", ind: "", ex: "", liq: "", sortKey: "", asc: true, mine: new Set(), logos: new Set(),
  market: null, marketAsOf: null, marketNote: "", marketShown: 0,
};
const GROUPS = {
  main: (r) => r.grp === "A" || r.grp === "B",
  ext: (r) => r.grp === "C",
  mine: (r) => state.mine.has(r.ticker),
  leaders: (r) => !!r.leader_of,
  market: () => true,
};
// "Toàn thị trường" reads the whole-market snapshot (/api/market); every other group the monitored rows.
function mineRows() {
  const monitored = new Map(state.rows.map((r) => [r.ticker, r]));
  const market = new Map((state.market || []).map((r) => [r.ticker, r]));
  return [...state.mine].map((ticker) => {
    const existing = monitored.get(ticker), available = market.get(ticker);
    let row = existing ? { ...existing } : { tech: {}, setup: {}, not_assessable: ["TT1", "TT2", "TT3", "TT4", "TT5", "TT6", "TT7", "TT8"], ...available, ticker };
    if (existing && available && available.trade_date > (existing.trade_date || "")) {
      row.state_date = existing.state_date || existing.trade_date;
      for (const key of ["trade_date", "close", "change_pct", "change_basis", "reference", "reference_method", "reference_averaged"]) row[key] = available[key];
    }
    row.mine = true;
    if (SITE) row.note = state.mineNotes?.get(ticker) || null;
    return row;
  });
}
const groupRows = () => (state.group === "market" ? state.market || [] : state.group === "mine" ? mineRows() : state.rows.filter(GROUPS[state.group]));
const MARKET_PAGE = 150;      // rows drawn at a time: ~1,500 tickers stay smooth

// ---------------------------------------------------------------- VN-Index card
function renderMarket(run) {
  const live = run && run.live_market && run.live_market.status;
  const m = live ? run.live_market : ((run && run.market_json) || {});
  const note = live ? run.live_market_note : run && run.market_note;
  const level = live ? run.live_market_level : run && run.market_level;
  const spark = (m.spark || []);
  let svg = "";
  if (spark.length > 2) {
    const lo = Math.min(...spark), hi = Math.max(...spark);
    const p = spark.map((v, i) => `${(i / (spark.length - 1) * 96 + 2).toFixed(1)},${(30 - (v - lo) / ((hi - lo) || 1) * 26).toFixed(1)}`).join(" ");
    svg = `<svg class="spark ${tone(m.change_pct)}" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true"><polyline points="${p}"/></svg>`;
  }
  $("#vni").innerHTML = `
    <div class="vni-l">
      <div class="vni-top"><span class="vni-name">VN-Index</span><span class="muted small">${dm(m.as_of || (run && run.as_of_date))}</span></div>
      <div class="vni-val"><b>${fmt(m.close, 2)}</b><span class="${tone(m.change_pct)}">${pct(m.change_pct, 2)}</span></div>
      <span class="mk mk-${esc(level || "")}" title="${esc(note || "")}">${esc(MARKET_TEXT[m.status] || "–")}${m.distribution_days != null ? ` · ${m.distribution_days} ngày phân phối` : ""}</span>
      ${breadthLine(m.session)}
    </div>${svg}`;
  $("#vni").title = note || "";
}

// Advancers / unchanged / decliners of the session (DNSE market-index). Advancers include
// the stocks at the ceiling and decliners those at the floor.
function breadthLine(s) {
  if (!s || s.advancers == null) return "";
  const tip = `Phiên ${dmy(s.trade_date)}: ${s.advancers} mã tăng (${s.ceilings ?? 0} trần), ${s.unchanged ?? 0} đứng giá, `
    + `${s.decliners} mã giảm (${s.floors ?? 0} sàn) trên HOSE, theo DNSE (số Sở công bố có thể khác vài mã do cách đếm)`;
  return `<span class="vni-br small" title="${esc(tip)}"><span class="pos">▲${s.advancers}</span>`
    + `<span class="zero">■${s.unchanged ?? 0}</span><span class="neg">▼${s.decliners}</span></span>`;
}

// Matched trading and put-through deals of the session, in millions of shares.
function volumeSplitTag(s) {
  if (!s || s.matched_volume == null) return "";
  const mn = (v) => fmt(v / 1e6, 1);
  return `<span class="tag" title="Ngày phân phối / FTD so khối lượng khớp lệnh (con số bảng giá và VNDirect hiển thị); thỏa thuận là giao dịch lô lớn dàn xếp trước">`
    + `KL khớp ${mn(s.matched_volume)} tr · thỏa thuận ${mn(s.put_through_volume)} tr</span>`;
}

// ---------------------------------------------------------------- dropdown
// A styled stand-in for <select>: the browser's own list has square corners and
// system colours that CSS cannot change. One popover at a time, placed with
// position: fixed so the scrolling filter row never clips it.
const CARET = '<svg class="dd-caret" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const fold = (s) => String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[đĐ]/g, "d").toLowerCase();
const ddButton = (attrs, label, valueText, active) => `<button type="button" class="dd${active ? " active" : ""}" ${attrs} aria-haspopup="listbox" aria-expanded="false">`
  + `${label ? `<span class="dd-label">${esc(label)}</span>` : ""}<span class="dd-value">${esc(valueText)}</span>${CARET}</button>`;
const dd = { pop: null, trigger: null };

function closeDropdown() {
  if (!dd.pop) return;
  const { pop, trigger } = dd;
  dd.pop = dd.trigger = null;
  trigger.setAttribute("aria-expanded", "false");
  pop.classList.remove("show");
  setTimeout(() => pop.remove(), 180);
}

// items: [{ value, text, count?, sub? } | { group }]. Picking calls onPick(value).
function openDropdown(trigger, { items, value, onPick, searchable = items.length > 10, placeholder = "Tìm…" }) {
  if (dd.trigger === trigger) { closeDropdown(); return; }
  closeDropdown();
  const pop = document.createElement("div");
  pop.className = "dd-pop";
  pop.innerHTML = (searchable ? `<div class="dd-search"><span aria-hidden="true">⌕</span><input type="search" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></div>` : "")
    + `<div class="dd-list" role="listbox">${items.map((it) => (it.group != null ? `<div class="dd-group">${esc(it.group)}</div>`
      : `<button type="button" role="option" class="dd-opt${it.value === value ? " sel" : ""}" data-v="${esc(it.value)}" aria-selected="${it.value === value}">`
        + `<span class="dd-check" aria-hidden="true">${it.value === value ? "✓" : ""}</span>`
        + `<span class="dd-text">${esc(it.text)}${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</span>`
        + `${it.count != null ? `<span class="dd-count">${it.count}</span>` : ""}</button>`)).join("")}`
    + `<div class="dd-empty" hidden>Không có lựa chọn phù hợp</div></div>`;
  document.body.appendChild(pop);
  dd.pop = pop; dd.trigger = trigger;
  trigger.setAttribute("aria-expanded", "true");

  // Under the trigger, or above it when there is no room below.
  const r = trigger.getBoundingClientRect();
  pop.style.minWidth = `${Math.max(r.width, 210)}px`;
  const below = innerHeight - r.bottom - 14, above = r.top - 14, up = below < 260 && above > below;
  $(".dd-list", pop).style.maxHeight = `${Math.max(140, Math.min(360, (up ? above : below) - (searchable ? 54 : 14)))}px`;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8))}px`;
  if (up) { pop.classList.add("up"); pop.style.bottom = `${innerHeight - r.top + 6}px`; }
  else pop.style.top = `${r.bottom + 6}px`;
  void pop.offsetHeight;                   // commit the start state so the class change animates
  pop.classList.add("show");

  const visible = () => $$(".dd-opt:not([hidden])", pop);
  const pick = (opt) => { const t = dd.trigger; closeDropdown(); onPick(opt.dataset.v, t); };
  pop.addEventListener("click", (e) => { const o = e.target.closest(".dd-opt"); if (o) pick(o); });
  pop.addEventListener("keydown", (e) => {
    e.stopPropagation();                   // Escape here must not also close the detail drawer
    const list = visible(), i = list.indexOf(document.activeElement), search = $(".dd-search input", pop);
    if (e.key === "ArrowDown") { e.preventDefault(); (list[Math.min(i + 1, list.length - 1)] || list[0])?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (i <= 0 && search) search.focus(); else list[Math.max(i - 1, 0)]?.focus(); }
    else if (e.key === "Enter" && document.activeElement === search && list.length) { e.preventDefault(); pick(list[0]); }
    else if (e.key === "Escape") { e.preventDefault(); const t = dd.trigger; closeDropdown(); t.focus(); }
    else if (e.key === "Tab") closeDropdown();
  });
  const search = $(".dd-search input", pop);
  if (search) {
    search.addEventListener("input", () => {
      const q = fold(search.value.trim());
      let header = null, any = false, total = 0;
      const settle = () => { if (header) header.hidden = !any; };
      for (const el of $(".dd-list", pop).children) {
        if (el.classList.contains("dd-group")) { settle(); header = el; any = false; }
        else if (el.classList.contains("dd-opt")) {
          const ok = !q || fold($(".dd-text", el).textContent).includes(q);
          el.hidden = !ok;
          if (ok) { any = true; total += 1; }
        }
      }
      settle();
      $(".dd-empty", pop).hidden = total > 0;
    });
    search.focus();
  } else ($(".dd-opt.sel", pop) || visible()[0])?.focus();
}

// ---------------------------------------------------------------- filters
const FILTERS = [
  { key: "grp", label: "Mức đạt chuẩn", only: "main", test: (r, v) => r.grp === v,
    options: () => [["A", "Đạt đủ 8/8 (A)"], ["B", "Gần đạt (B)"]] },
  { key: "status", label: "Trạng thái", skip: "market", test: (r, v) => (r.setup || {}).status === v,
    options: () => Object.entries(STATUS_LABEL) },
  { key: "trend", label: "Xu hướng", only: "market",
    test: (r, v) => ({ A8: r.tt_passed === 8, NEAR: r.tt_passed === 6 || r.tt_passed === 7,
      UP200: (r.above_ma || {})["200"] === true, DN200: (r.above_ma || {})["200"] === false, FLAG: !!r.flagged }[v]),
    options: () => [["A8", "Đạt đủ 8/8 tiêu chí"], ["NEAR", "Gần đạt 6–7/8"], ["UP200", "Giá trên MA200"],
      ["DN200", "Giá dưới MA200"], ["FLAG", "Dữ liệu cần kiểm tra"]] },
  { key: "ind", label: "Ngành", test: (r, v) => (r.themes || []).some(t => t.name === v.slice(2)) || (r.industry || {}).name === v.slice(2),
    options: base => [...new Set(base.flatMap(r => [...(r.themes || []).map(t => t.name), (r.industry || {}).name]).filter(Boolean))]
      .sort((a,b) => a.localeCompare(b,"vi")).map(name => [`n:${name}`, name]) },
  { key: "ex", label: "Sàn", test: (r, v) => r.exchange === v, options: () => [["HOSE", "HOSE"], ["HNX", "HNX"], ["UPCOM", "UPCoM"]] },
  { key: "liq", label: "Thanh khoản", test: (r, v) => liqOf(r) === v,
    options: () => [["HIGH", "Cao · ≥ 10 tỷ/phiên"], ["GOOD", "Khá · 8–10 tỷ"], ["LOW", "Thấp · 3–8 tỷ"], ["UNDER", "Dưới 3 tỷ"]] },
];
const filtersHere = () => FILTERS.filter((f) => (!f.only || f.only === state.group) && f.skip !== state.group);
const optionText = (f, v, base) => {
  for (const o of f.options(base)) {
    if (o.group) { const hit = o.items.find(([x]) => x === v); if (hit) return hit[1]; }
    else if (o[0] === v) return o[1];
  }
  return v;
};

function passes(r, skipKey) {
  const q = state.q.trim();
  if (q && !(r.ticker.includes(q.toUpperCase()) || fold(r.name || "").includes(fold(q)))) return false;
  return filtersHere().every((f) => f.key === skipKey || !state[f.key] || f.test(r, state[f.key]));
}

// Each option's count is taken with every *other* filter applied: the number of
// rows you would see after picking it. Options with no row are left out.
function filterItems(f, base) {
  const pool = base.filter((r) => passes(r, f.key));
  const items = [{ value: "", text: "Tất cả", count: pool.length }];
  const add = ([v, text]) => {
    const n = pool.filter((r) => f.test(r, v)).length;
    if (n || state[f.key] === v) items.push({ value: v, text, count: n });
  };
  for (const o of f.options(base)) {
    if (!o.group) { add(o); continue; }
    const at = items.push({ group: o.group });
    o.items.forEach(add);
    if (items.length === at) items.pop();         // a group with nothing in it
  }
  return items;
}

function renderFilters(base, shown) {
  $("#filter-selects").innerHTML = filtersHere().map((f) =>
    ddButton(`data-f="${f.key}"`, f.label, state[f.key] ? optionText(f, state[f.key], base) : "Tất cả", !!state[f.key])).join("");
  const act = filtersHere().filter((f) => state[f.key]);
  const pills = act.map((f) => `<span class="fpill">${esc(f.label)}: <b>${esc(optionText(f, state[f.key], base))}</b><button data-clear="${f.key}" title="Bỏ lọc này">×</button></span>`);
  if (state.q.trim()) pills.unshift(`<span class="fpill">Tìm: <b>${esc(state.q.trim())}</b><button data-clear="q" title="Bỏ">×</button></span>`);
  $("#filter-summary").innerHTML = `<span><b>${shown}</b>${shown !== base.length ? ` / ${base.length}` : ""} mã</span>${pills.join("")}`
    + (pills.length ? `<button class="linkbtn" data-clear="all">Xóa tất cả</button>` : "");
}

// ---------------------------------------------------------------- columns
const dotsOf = (r) => {
  const na = r.not_assessable || [], miss = r.missing || [];
  return `<span class="dots" title="${na.length ? "mới niêm yết: chưa đủ dữ liệu cho " + na.join(", ") : miss.length ? "chưa đạt " + miss.join(", ") : "đạt cả 8 tiêu chí"}">`
    + ["TT1", "TT2", "TT3", "TT4", "TT5", "TT6", "TT7", "TT8"].map((k) =>
      `<i class="${na.includes(k) ? "dot-na" : miss.includes(k) ? "dot-off" : "dot-on"}"></i>`).join("") + "</span>";
};
const srcTag = (r) => (r.grp ? `<span class="g g-${esc(r.grp)}" title="${esc(GROUP_TEXT[r.grp])}">${esc(r.grp)}</span>` : "")
  + (state.group === "mine" && !r.flagged && !Object.keys(r.tech || {}).length ? '<span class="tag">Chưa có phân tích chi tiết</span>' : "")
  + (r.leader_of && state.group !== "leaders" ? `<span class="tag" title="Đầu ngành ${esc(r.leader_of)}">đầu ngành</span>` : "")
  + (r.flagged ? `<span class="tag tag-flag" title="${esc(r.flag_note || "Chuỗi giá bị gắn cờ (vd chưa điều chỉnh quyền)")}. Không tính chỉ báo cho mã này.">dữ liệu cần kiểm tra</span>` : "");
const tickerCell = (r) => `<div class="tkw">${logoTile(r.ticker)}<div class="tkc"><div class="tk-line"><span class="tk">${esc(r.ticker)}</span>${srcTag(r)}</div>
  <span class="nm" title="${esc(r.name || "")}">${esc(r.exchange || "")}${r.name ? " · " + esc(r.name) : ""}</span>
  ${state.group === "mine" && r.note ? `<span class="nm note-line" title="${esc(r.note)}">✎ ${esc(r.note)}</span>` : ""}</div></div>`;
// % as the price boards show it: against the session's reference price when it was read
// (refprices). Without one it is against the previous close, which on UPCoM differs (reference
// = previous session's average price): those cells get a dotted underline.
const basisTitle = (r) => (r.change_basis === "ref" ? `So với giá tham chiếu ${fmt(r.reference)} (như bảng giá)${String(r.reference_method || r.method || "").startsWith("avg_price") ? ` — giá bình quân phiên ${dmy(r.reference_averaged || r.averaged)} làm tròn 100 đ, theo quy tắc UPCoM` : ""}`
  : r.exchange === "UPCOM" ? "So với giá đóng cửa phiên trước — chưa có giá tham chiếu của phiên này. Bảng giá UPCoM tính so với giá bình quân phiên trước nên % có thể khác"
  : "So với giá đóng cửa phiên trước — HOSE / HNX lấy giá này làm tham chiếu (trừ ngày giao dịch không hưởng quyền), nên trùng bảng giá");
// A price older than the newest session, or a state older than the price (a ticker that left the
// monitored list keeps its last state while its price goes on), says which session it is (tester N02).
const dateNote = (r) => (r.state_date
  ? `<span class="stale" title="Mã không còn được theo dõi hằng ngày: giá theo phiên ${dmy(r.trade_date)}, còn pivot / trạng thái theo phiên ${dmy(r.state_date)}">trạng thái ${dm(r.state_date)}</span>`
  : r.trade_date && state.lastSession && r.trade_date < state.lastSession
    ? `<span class="stale" title="Giá của phiên ${dmy(r.trade_date)}; phiên mới nhất là ${dmy(state.lastSession)}">giá ${dm(r.trade_date)}</span>` : "");
const priceCell = (r) => `<div class="two"><b>${fmt(r.close)}</b><span class="${tone(r.change_pct)}${r.change_basis === "close" && r.exchange === "UPCOM" ? " basis-close" : ""}" title="${esc(basisTitle(r))}">${pct(r.change_pct, 2)}</span>${dateNote(r)}${r.since_screen
  ? `<span class="small muted" title="Giá đóng cửa ngày lọc ${dmy(r.since_screen.date)}: ${fmt(r.since_screen.close)} — giá đã điều chỉnh theo quyền (chia / thưởng cổ phiếu), nên có thể đổi khi có quyền mới">từ ngày lọc <span class="${tone(r.since_screen.pct)}">${pct(r.since_screen.pct, 1)}</span></span>` : ""}</div>`;
const techOf = (r) => r.tech || {};
const RT_ARROW = { STRONG_POS: "⇈", POS: "↑", NEUTRAL: "=", NEG: "↓", STRONG_NEG: "⇊" };
const rtBadge = (x) => (x && x.key) ? `<span class="rt rt-${esc(x.key)}" title="TradingView: ${esc(x.tv_word || "")}${x.positive != null ? ` · ${x.positive} tích cực / ${x.negative} tiêu cực / ${x.neutral} trung tính` : ""}">${RT_ARROW[x.key]} ${esc(x.label)}</span>` : '<span class="muted">–</span>';
const patBadge = (p) => p ? `<span class="pat pat-${p.bias === "tăng" ? "up" : p.bias === "giảm" ? "down" : "flat"}" title="${esc(p.bias)} · ${esc(p.note)}${p.stat ? " · Bulkowski (Mỹ): " + esc(p.stat) : ""}">${esc(p.name)}</span>` : '<span class="muted">–</span>';
const numCell = (v, d = 2) => (v == null ? '<span class="muted">–</span>' : `<span class="${v < 0 ? "neg" : ""}">${fmt(v, d)}</span>`);
// Signed value as shown: green with "+", red with "−", yellow when it rounds to 0;
// bold beyond ±`ext` (CCI's ±100).
const signCell = (v, d = 0, ext = null) => {
  if (v == null) return '<span class="muted">–</span>';
  const r = Number(v.toFixed(d)) || 0;            // -0 shows as 0
  return `<span class="${r > 0 ? "pos" : r < 0 ? "neg" : "zero"}${ext != null && Math.abs(r) >= ext ? " ext" : ""}">${r > 0 ? "+" : ""}${fmt(r, d)}</span>`;
};
// 0–100 oscillators: green above the midline 50, red below, yellow on it; bold in the
// stretched zones (RSI ≥ 70 / ≤ 30, Stoch ≥ 80 / ≤ 20).
const midCell = (v, d, lo, hi) => {
  if (v == null) return '<span class="muted">–</span>';
  const r = Number(v.toFixed(d));
  return `<span class="${r > 50 ? "pos" : r < 50 ? "neg" : "zero"}${r >= hi || r <= lo ? " ext" : ""}">${fmt(r, d)}</span>`;
};
const fl = (r) => r.flow || {};
// A pivot / stop still held from the rules before 11/10/2026, and what the current rules read (tester R3-04).
const legacyNote = (r) => {
  if (!r.legacy_hold) return "";
  const n = r.legacy_new || {};
  const now = `Theo cách tính mới: ${BASE_TEXT[n.base_kind] || n.base_kind || "–"}, pivot ${fmt(n.pivot)}, stop ${n.stop ? fmt(n.stop) : "–"}`;
  return `<span class="stale" title="${esc(now)}. Số cũ được giữ tới hết 5 phiên sau sự kiện để trạng thái không đổi chỉ vì đổi cách tính.">giữ số cũ${r.legacy_until ? " tới " + dm(r.legacy_until) : ""}</span>`;
};
// "5 phiên" counts the ticker's last five sessions; a session DNSE has no foreign figures for stays out (R3-03).
const flowGap = (f) => (f.net5_of && f.net5_sessions < f.net5_of
  ? ` <span class="stale" title="DNSE không có số liệu khối ngoại cho ${f.net5_of - f.net5_sessions} trong ${f.net5_of} phiên gần nhất (thường là không có giao dịch khối ngoại)">${f.net5_sessions}/${f.net5_of} phiên có số liệu</span>` : "");
// A recent listing has only TT7 measurable: "1/8" would read as weak (tester B23).
// A stock that trades on few days is not a new listing (tester N04: BHP); the snapshot says which.
const ttText = (r) => ((r.not_assessable || []).length >= 6
  ? (r.history === "SPARSE" ? "giao dịch thưa · chưa đủ phiên để đo" : "mới niêm yết · chưa đủ lịch sử")
  : `${r.tt_passed}/8 tiêu chí`);
// Domestic = matched volume - the foreign MATCHED side, from the server (server.flow_row): the foreign
// day totals include put-through deals and are not comparable with the matched volume (HDB 06/10).
const domBuy = (r) => fl(r).domestic_buy;
const domSell = (r) => fl(r).domestic_sell;
const ptCell = (v) => (v == null || Math.abs(v) < 0.5 ? '<span class="muted">–</span>' : `<span class="${tone(v)}">${sfmt(v)}</span>`);
const tv = (key) => (r) => { const x = techOf(r)[key]; return x && x.score != null ? x.score : null; };
const val = (key) => (r) => (techOf(r).values || {})[key];

const COLS = {
  overview: [
    { k: "ticker", label: "Mã", cell: tickerCell, sort: (r) => r.ticker, text: true },
    { k: "close", label: "Giá · %", num: true, title: "Giá đóng cửa phiên gần nhất và % so với giá tham chiếu, như bảng giá. Bấm để sắp xếp theo % thay đổi", cell: priceCell, sort: (r) => r.change_pct },
    { k: "rs", label: "RS", num: true, title: "Sức mạnh giá 1–99 so với mọi mã đủ dữ liệu trên thị trường (99 = mạnh nhất)", cell: (r) => `<b>${fmt(r.rs_rating)}</b>`, sort: (r) => r.rs_rating },
    { k: "tt", label: "Xu hướng", title: "8 tiêu chí xu hướng (Trend Template): xanh = đạt, đỏ = chưa đạt, xám = chưa đủ dữ liệu",
      cell: (r) => `<div class="two">${dotsOf(r)}<span class="muted">${ttText(r)}</span></div>`, sort: (r) => r.tt_passed },
    { k: "base", label: "Nền giá", title: "Dạng nền đang hình thành và khoảng cách tới pivot (điểm phá nền)",
      cell: (r) => `<div class="two"><span>${esc(BASE_TEXT[r.base_kind] || r.base_kind || "–")}</span>${r.pivot ? `<span class="muted">pivot ${fmt(r.pivot)} · <span class="${r.distance > 0 ? "pos" : ""}">${pct(r.distance)}</span></span>` : ""}${r.stop_breached
        ? `<span class="neg small" title="Giá đóng cửa ở hoặc dưới stop tham chiếu ${fmt(r.stop)}">thủng stop${r.stop_breached_on ? " " + dm(r.stop_breached_on) : ""}</span>` : ""}${legacyNote(r)}</div>`,
      sort: (r) => (r.distance == null ? null : -Math.abs(r.distance)) },
    { k: "status", label: "Trạng thái", title: "Vị trí trong quy trình SEPA — không phải tín hiệu mua", cell: (r) => stBadge((r.setup || {}).status), sort: (r) => STATUS_ORDER[(r.setup || {}).status] ?? 9, text: true },
    { k: "fnet", label: "Khối ngoại", num: true, title: "Khối ngoại mua − bán (cổ phiếu), phiên gần nhất và cộng dồn 5 phiên",
      cell: (r) => fl(r).net_volume == null ? '<span class="muted">–</span>'
        : `<div class="two"><span class="${tone(fl(r).net_volume)}">${sfmt(fl(r).net_volume)}</span>${r.trade_date && fl(r).date < r.trade_date
          ? `<span class="stale" title="Chưa có dữ liệu khối ngoại của phiên ${dm(r.trade_date)}">phiên ${dm(fl(r).date)}</span>`
          : `<span class="muted">5 phiên <span class="${tone(fl(r).net5_volume)}">${vol(fl(r).net5_volume, true)}</span>${flowGap(fl(r))}</span>`}</div>`,
      sort: (r) => fl(r).net_volume },
    { k: "liq", label: "Thanh khoản", title: "Giá trị giao dịch trung bình 20 phiên", cell: (r) => `<div class="two">${liqBadge(liqOf(r))}<span class="muted">${r.liquidity_vnd != null ? fmt(r.liquidity_vnd / 1e9, 1) + " tỷ" : ""}</span></div>`, sort: (r) => r.liquidity_vnd },
    { k: "fund", label: "Cơ bản", title: "Hạng tăng trưởng từ báo cáo tài chính quý (CafeF, chưa đối chiếu báo cáo gốc)",
      cell: (r) => { const f = r.fund || {}; const y = (f.profit_yoy || [])[0]; return `<div class="two">${fundBadge(f)}${y != null ? `<span class="muted" title="Lợi nhuận quý gần nhất so với cùng kỳ">LN <span class="${tone(y)}">${pct(y, 0)}</span></span>` : ""}</div>`; },
      sort: (r) => FUND_ORDER[(r.fund || {}).grade] ?? 9, text: true },
  ],
  tech: [
    { k: "ticker", label: "Mã", cell: tickerCell, sort: (r) => r.ticker, text: true },
    { k: "close", label: "Giá", num: true, cell: priceCell, sort: (r) => r.change_pct },
    { k: "tv_overall", label: "Xếp hạng chung", title: "Trung bình xếp hạng đường MA và chỉ báo dao động", cell: (r) => rtBadge(techOf(r).overall), sort: tv("overall") },
    { k: "tv_ma", label: "Đường MA", title: "15 đường trung bình: SMA/EMA 10–200, Ichimoku, VWMA, Hull", cell: (r) => rtBadge(techOf(r).ma), sort: tv("ma") },
    { k: "tv_osc", label: "Dao động", title: "11 chỉ báo: RSI, Stoch, CCI, ADX, AO, Mom, MACD, Stoch RSI, W%R, BBP, UO", cell: (r) => rtBadge(techOf(r).osc), sort: tv("osc") },
    { k: "rsi", label: "RSI 14", num: true, title: "Xanh > 50, đỏ < 50, vàng = 50; chữ đậm: ≥ 70 (tăng nóng) hoặc ≤ 30 (giảm sâu)", cell: (r) => midCell(val("rsi")(r), 2, 30, 70), sort: val("rsi") },
    { k: "mom", label: "Mom 10", num: true, title: "Giá hôm nay − giá 10 phiên trước (đồng): xanh = cao hơn, đỏ = thấp hơn, vàng = bằng", cell: (r) => signCell(val("mom")(r), 0), sort: val("mom") },
    { k: "ao", label: "AO", num: true, title: "Awesome Oscillator (đồng): xanh = dương, đỏ = âm, vàng = 0", cell: (r) => signCell(val("ao")(r), 0), sort: val("ao") },
    { k: "cci", label: "CCI 20", num: true, title: "Xanh = trên trung bình 20 phiên, đỏ = dưới, vàng = 0; chữ đậm: vượt ±100", cell: (r) => signCell(val("cci")(r), 2, 100), sort: val("cci") },
    { k: "stoch_k", label: "Stoch %K", num: true, title: "Stochastic 14, 3, 3 — xanh > 50, đỏ < 50, vàng = 50; chữ đậm: ≥ 80 (sát đỉnh biên) hoặc ≤ 20 (sát đáy)", cell: (r) => midCell(val("stoch_k")(r), 2, 20, 80), sort: val("stoch_k") },
    { k: "stoch_d", label: "%D", num: true, title: "Trung bình 3 phiên của %K — cùng cách tô màu", cell: (r) => midCell(val("stoch_d")(r), 2, 20, 80), sort: val("stoch_d") },
    { k: "pattern", label: "Mẫu nến", title: "Mẫu nến phiên gần nhất — cần phiên sau xác nhận (xem tab Học)", cell: (r) => patBadge(techOf(r).pattern), sort: (r) => (techOf(r).pattern || {}).name || null, text: true },
  ],
  flow: [
    { k: "ticker", label: "Mã", cell: tickerCell, sort: (r) => r.ticker, text: true },
    { k: "close", label: "Giá", num: true, cell: priceCell, sort: (r) => r.change_pct },
    { k: "fdate", label: "Phiên", cell: (r) => `<span class="${r.trade_date && fl(r).date < r.trade_date ? "stale" : "muted"}">${dm(fl(r).date)}</span>`, sort: (r) => fl(r).date, text: true },
    { k: "fb", label: "Ngoại mua khớp", num: true, title: "Khối ngoại mua qua khớp lệnh (cổ phiếu)", cell: (r) => fmt(fl(r).buy_volume_matched), sort: (r) => fl(r).buy_volume_matched },
    { k: "fs", label: "Ngoại bán khớp", num: true, title: "Khối ngoại bán qua khớp lệnh (cổ phiếu)", cell: (r) => fmt(fl(r).sell_volume_matched), sort: (r) => fl(r).sell_volume_matched },
    { k: "fpt", label: "Thỏa thuận", num: true, title: "Khối ngoại mua − bán qua giao dịch thỏa thuận (và lô lẻ), cổ phiếu", cell: (r) => ptCell(fl(r).net_volume_put_through), sort: (r) => fl(r).net_volume_put_through },
    { k: "fn", label: "Ngoại ròng", num: true, title: "Tổng khối ngoại mua − bán: khớp lệnh + thỏa thuận, như số sàn công bố (cổ phiếu)", cell: (r) => `<b class="${tone(fl(r).net_volume)}">${sfmt(fl(r).net_volume)}</b>`, sort: (r) => fl(r).net_volume },
    { k: "fv", label: "Ròng (tỷ đ)", num: true, title: "Tổng giá trị khối ngoại mua − bán (khớp lệnh + thỏa thuận)", cell: (r) => `<span class="${tone(fl(r).net_value)}">${fl(r).net_value == null ? "–" : sfmt(fl(r).net_value / 1e9, 2)}</span>`, sort: (r) => fl(r).net_value },
    { k: "f5", label: "Ngoại ròng 5 phiên", num: true, title: "Tổng 5 phiên giao dịch gần nhất (khớp lệnh + thỏa thuận); phiên không có số liệu khối ngoại không được cộng",
      cell: (r) => `<div class="two"><span class="${tone(fl(r).net5_volume)}">${sfmt(fl(r).net5_volume)}</span>${flowGap(fl(r))}</div>`, sort: (r) => fl(r).net5_volume },
    { k: "db", label: "Nội mua", num: true, title: "Khối lượng khớp − khối ngoại mua khớp lệnh", cell: (r) => fmt(domBuy(r)), sort: domBuy },
    { k: "ds", label: "Nội bán", num: true, title: "Khối lượng khớp − khối ngoại bán khớp lệnh", cell: (r) => fmt(domSell(r)), sort: domSell },
  ],
};
const maChips = (r) => `<span class="ma-chips" title="Giá so với MA50 / MA150 / MA200 (xanh = trên, đỏ = dưới)">${["50", "150", "200"].map((n) => {
  const v = (r.above_ma || {})[n];
  return `<i class="${v === true ? "ma-up" : v === false ? "ma-dn" : "ma-na"}">${n}</i>`;
}).join("")}</span>`;
COLS.market = [
  { k: "ticker", label: "Mã", cell: tickerCell, sort: (r) => r.ticker, text: true },
  { k: "close", label: "Giá", num: true, title: "Giá đóng cửa phiên gần nhất của mã và % so với giá tham chiếu (gạch chấm: so với giá đóng cửa phiên trước)", cell: priceCell, sort: (r) => r.change_pct },
  { k: "date", label: "Phiên", title: "Phiên giá gần nhất của mã; màu vàng = cũ hơn phiên mới nhất của thị trường",
    cell: (r) => `<span class="${r.trade_date < state.marketAsOf ? "stale" : "muted"}">${dm(r.trade_date)}</span>`, sort: (r) => r.trade_date, text: true },
  { k: "rs", label: "RS", num: true, title: "RS theo lượt lọc gần nhất; chỉ có ở mã đủ thanh khoản", cell: (r) => `<b>${fmt(r.rs_rating)}</b>`, sort: (r) => r.rs_rating },
  { k: "tt", label: "Xu hướng", title: "8 tiêu chí xu hướng: xanh = đạt, đỏ = chưa đạt, xám = chưa đo được (mã mới / chưa có RS)",
    cell: (r) => (r.flagged ? '<span class="muted">–</span>' : `<div class="two">${dotsOf(r)}<span class="muted">${ttText(r)}</span></div>`),
    sort: (r) => (r.flagged ? null : r.tt_passed) },
  { k: "ma", label: "Trên MA", title: "Giá so với MA50 / MA150 / MA200", cell: (r) => (r.flagged ? '<span class="muted">–</span>' : maChips(r)),
    sort: (r) => (r.flagged ? null : Object.values(r.above_ma || {}).filter(Boolean).length) },
  { k: "high", label: "Cách đỉnh", num: true, title: "% giá hiện tại so với đỉnh cao nhất 52 tuần",
    cell: (r) => (r.pct_from_high == null ? '<span class="muted">–</span>' : `<span class="${r.pct_from_high > -10 ? "pos" : r.pct_from_high < -40 ? "neg" : ""}">${pct(r.pct_from_high)}</span>`),
    sort: (r) => r.pct_from_high },
  { ...COLS.overview.find((c) => c.k === "base"), cell: (r) => (r.flagged ? '<span class="muted">–</span>' : COLS.overview.find((c) => c.k === "base").cell(r)) },
  COLS.overview.find((c) => c.k === "liq"),
  COLS.overview.find((c) => c.k === "fund"),
];
const NOTES = {
  overview: "Bấm một mã để xem biểu đồ và phân tích. Nền giá và pivot là cách đo triển khai [TRIỂN KHAI], chưa backtest trên dữ liệu Việt Nam.",
  tech: "Chỉ báo ngắn hạn theo công thức TradingView, nhãn trung tính (\"Tích cực mạnh\" ≈ \"Mua mạnh\" trên TradingView). Không phải quy tắc SEPA, chưa kiểm định trên dữ liệu Việt Nam, không phải tín hiệu mua bán.",
  market: "",
  flow: "Khối ngoại: DNSE, phiên gần nhất có dữ liệu. Mua / bán khớp = qua khớp lệnh; Thỏa thuận = khối ngoại ròng qua giao dịch thỏa thuận (và lô lẻ); Ngoại ròng = tổng, như số sàn công bố. Khối nội = khối lượng khớp − khối ngoại khớp (DNSE không tách cá nhân / tổ chức trong nước). Bấm một mã, tab \"Dòng tiền\" để xem 20 phiên.",
};
// A star before every ticker, in every list: one click adds it to (or takes it off) "Danh sách của tôi".
const starBtn = (t) => `<button type="button" class="star${state.mine.has(t) ? " on" : ""}" data-star="${esc(t)}" title="${state.mine.has(t) ? "Bỏ khỏi" : "Thêm vào"} Danh sách của tôi" aria-label="${state.mine.has(t) ? "Bỏ" : "Thêm"} ${esc(t)}">${state.mine.has(t) ? "★" : "☆"}</button>`;
const starCell = (t) => `<td class="star-col">${starBtn(t)}</td>`;
function syncStars() {
  $$("[data-star]").forEach((b) => {
    const on = state.mine.has(b.dataset.star);
    b.classList.toggle("on", on);
    b.textContent = on ? "★" : "☆";
    b.title = `${on ? "Bỏ khỏi" : "Thêm vào"} Danh sách của tôi`;
  });
}

// The columns on screen: the whole-market table has its own; every other group uses the chosen view.
const shownCols = () => (state.group === "market" ? COLS.market : COLS[state.view]);

function renderTable(keepPage = false) {
  const market = state.group === "market";
  if (market && !state.market) {
    $("#screen-table tbody").innerHTML = `<tr><td class="empty">Đang tải toàn thị trường…</td></tr>`;
    loadMarket();
    return;
  }
  if (!keepPage) state.marketShown = MARKET_PAGE;
  const base = groupRows();
  const rows = base.filter((r) => passes(r));
  renderFilters(base, rows.length);
  $(".view-seg").hidden = market;
  const cols = shownCols();
  const col = cols.find((c) => c.k === state.sortKey);
  if (col) {
    rows.sort((a, b) => {
      const x = col.sort(a), y = col.sort(b);
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * (state.asc ? 1 : -1);
    });
  } else if (state.group === "leaders") {
    // by industry (strongest first), then traded value
    const rs = (r) => ((r.industry || {}).name === r.leader_of ? (r.industry || {}).rs_percentile : null) ?? -1;
    rows.sort((a, b) => (a.leader_of === b.leader_of ? (b.liquidity_vnd || 0) - (a.liquidity_vnd || 0)
      : rs(b) - rs(a) || a.leader_of.localeCompare(b.leader_of, "vi")));
  }
  $("#screen-table thead").innerHTML = `<tr><th class="star-col"></th>${cols.map((c) =>
    `<th data-k="${c.k}" class="${c.num ? "num" : ""}${c.k === state.sortKey ? " sorted" + (state.asc ? " asc" : "") : ""}" title="${esc(c.title || "Bấm để sắp xếp")}">${esc(c.label)}</th>`).join("")}</tr>`;
  $("#list-note").textContent = market ? state.marketNote : NOTES[state.view];
  const tbody = $("#screen-table tbody");
  if (!rows.length) {
    const empty = state.group === "mine" && !base.length ? "Chưa có mã nào. Gõ mã vào ô phía trên (VD: PHP) hoặc bấm ☆ cạnh bất kỳ mã nào."
      : base.length ? "Không có mã khớp bộ lọc."
      : market ? "Chưa có ảnh chụp toàn thị trường. Chạy: python -m src.main market" : "Chưa có dữ liệu. Chạy: python -m src.main screen";
    tbody.innerHTML = `<tr><td colspan="${cols.length + 1}" class="empty">${esc(empty)}</td></tr>`;
    return;
  }
  let head = null;
  const drawn = market ? rows.slice(0, state.marketShown) : rows;
  tbody.innerHTML = drawn.map((r) => {
    let h = "";
    if (state.group === "leaders" && !col && r.leader_of !== head) {
      head = r.leader_of;
      const rs = (r.industry || {}).name === head ? (r.industry || {}).rs_percentile : null;
      h = `<tr class="ind-head"><td colspan="${cols.length + 1}">${esc(head)}${rs != null ? ` <span class="muted">· RS ngành ${fmt(rs)}</span>` : ""}</td></tr>`;
    }
    return `${h}<tr data-t="${esc(r.ticker)}"${r.close == null ? ' class="nodata"' : ""}>${starCell(r.ticker)}${cols.map((c) =>
      `<td class="${c.num ? "num" : ""}">${c.cell(r)}</td>`).join("")}</tr>`;
  }).join("") + (drawn.length < rows.length ? `<tr class="more-row"><td colspan="${cols.length + 1}">
      <span class="muted">Đang hiện ${drawn.length} / ${rows.length} mã</span>
      <button class="btn" type="button" data-more="${MARKET_PAGE}">Hiện thêm ${Math.min(MARKET_PAGE, rows.length - drawn.length)} mã</button>
      <button class="linkbtn" type="button" data-more="all">Hiện tất cả</button></td></tr>` : "");
}

async function loadMarket(force = false) {
  if (state.marketPromise) return state.marketPromise;
  if (state.market && !force) return;
  state.marketPromise = (async () => {
  try {
    const data = await get("/api/market");
    state.market = data.rows;
    state.marketAsOf = data.as_of;
    state.marketNote = data.as_of
      ? `Giá tới phiên ${dmy(data.as_of)}${data.rs_from ? ` · RS theo lượt lọc ${dmy(data.rs_from)}` : ""} · ${data.note}`
      : data.note;
    $("#n-market").textContent = data.rows.length || "";
  } catch (err) {
    state.market = [];
    state.marketNote = err.message;
  } finally {
    state.marketPromise = null;
  }
  if (state.group === "market" || state.group === "mine") { renderMinePending(); renderTable(); }
  })();
  return state.marketPromise;
}

function setCounts() {
  for (const [id, g] of [["n-main", "main"], ["n-ext", "ext"], ["n-leaders", "leaders"]]) {
    const n = state.rows.filter(GROUPS[g]).length;
    $(`#${id}`).textContent = n || "";
  }
  $("#n-mine").textContent = state.mine.size || "";
}

async function loadStocks() {
  const runId = state.runId;
  state.rows = await get(`/api/stocks${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`);
  state.lastSession = state.rows.reduce((m, r) => (r.trade_date && r.trade_date > m ? r.trade_date : m), "");
  state.mine = new Set(state.rows.filter((r) => r.mine).map((r) => r.ticker));
  if (SITE) await loadLiveList();
  if ([...state.mine].some((t) => !state.rows.some((r) => r.ticker === t))) await loadMarket();
  setCounts();
  renderMinePending();
  renderTable(true);
}

// The web copy: each logged-in account's own list lives on Cloudflare (cloudflare/functions/api/mylist.js),
// newer than the exported rows. Without the function (a local preview) the export's flags stand.
async function loadLiveList() {
  try {
    const r = await fetch("/api/mylist", { cache: "no-store" });
    if (r.status === 401) toLogin();
    if (!r.ok) return;
    const d = await r.json();
    acceptMineList(d.items || []);
    state.canEdit = !!d.can_edit;
    document.body.classList.toggle("can-edit", state.canEdit);
    if (d.user) $("#sidebar-logout").hidden = false;
  } catch (e) { if (/đăng nhập/.test(e.message)) throw e; /* no function: read-only */ }
}

function acceptMineList(items) {
  state.mine = new Set(items.map((i) => i.ticker));
  state.mineNotes = new Map(items.map((i) => [i.ticker, i.note]));
}

// Suggestions for the add box: the code first (exact, then its start), then the company name; the most
// traded first. Read from the whole-market table, i.e. every listed share with prices.
const suggest = { hits: [], at: -1 };
async function renderSuggest() {
  const box = $("#mine-suggest"), raw = $("#mine-ticker").value.trim(), q = fold(raw);
  if (!q) { box.hidden = true; suggest.hits = []; return; }
  if (!state.market) await loadMarket();
  if (fold($("#mine-ticker").value.trim()) !== q) return;        // typed on while the table loaded
  suggest.hits = (state.market || []).map((r) => {
    const t = fold(r.ticker), n = fold(r.name || "");
    return [t === q ? 0 : t.startsWith(q) ? 1 : n.includes(q) ? 2 : 9, r];
  }).filter(([k]) => k < 9).sort((a, b) => a[0] - b[0] || (b[1].liquidity_vnd || 0) - (a[1].liquidity_vnd || 0))
    .slice(0, 8).map(([, r]) => r);
  suggest.at = suggest.hits.length ? 0 : -1;
  box.innerHTML = suggest.hits.length ? suggest.hits.map((r, i) => `<div class="sg${i === suggest.at ? " on" : ""}" role="option" data-add="${esc(r.ticker)}">
      ${logoTile(r.ticker, " sm")}<span class="sg-t"><b>${esc(r.ticker)}</b><small>${esc(r.exchange || "")} · ${esc(r.name || "")}</small></span>
      <span class="sg-p">${fmt(r.close)}</span>${state.mine.has(r.ticker) ? '<span class="sg-in">★ Đã có</span>' : ""}</div>`).join("")
    : `<div class="sg-empty">Không tìm thấy mã hoặc công ty "${esc(raw)}".</div>`;
  box.hidden = false;
}

// Tickers of the list still waiting for their data: on the web, not yet in the published rows; on this
// machine, added but not yet given a state (technicals) by mylist-refresh.
function renderMinePending() {
  const rows = mineRows();
  const missing = rows.filter((r) => r.close == null).map((r) => r.ticker);
  const pending = rows.filter((r) => !r.flagged && (!Object.keys(r.tech || {}).length || r.state_date)).map((r) => r.ticker);
  const el = $("#mine-pending");
  el.hidden = state.group !== "mine" || !(pending.length || missing.length);
  el.innerHTML = (missing.length ? `Chưa có giá trong bản hiện tại: <b>${missing.map(esc).join(", ")}</b>. ` : "")
    + (pending.length ? `Chờ bổ sung phân tích: <b>${pending.map(esc).join(", ")}</b>. Giá và biểu đồ đã có vẫn xem được; phần còn thiếu được bổ sung ở lượt cập nhật tiếp theo.` : "");
}

async function loadRun(runId) {
  const data = await get(`/api/screen${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`);
  state.run = data.run;
  if (data.run) renderMarket(data.run);
  await loadStocks();
}

// ---------------------------------------------------------------- detail: SEPA panes
function criteriaHtml(criteria) {
  if (!criteria) return `<p class="muted">Chưa có dữ liệu.</p>`;
  return ["TT1", "TT2", "TT3", "TT4", "TT5", "TT6", "TT7", "TT8"].filter((k) => criteria[k]).map((k) => {
    const c = criteria[k];
    const mark = c.pass === null ? ["na", "–"] : c.pass ? ["ok", "✓"] : ["ko", "✗"];
    return `<div class="crit"><span class="${mark[0]}">${mark[1]} ${k}</span>
      <span>${esc(c.text)}</span><b>${c.value === null || c.value === undefined ? "–" : fmt(c.value, 1)}</b>
      <small>ngưỡng ${esc(c.threshold)}</small></div>`;
  }).join("");
}

// The RS line (close / VN-Index), display only: rising, and best when it makes a new high before the price.
function rsLineHtml(r) {
  const s = r && r.summary;
  if (!s) return "";
  const unit = { 250: "52 tuần", 52: "52 tuần", 12: "12 tháng" }[s.lookback_bars] || `${s.lookback_bars} phiên`;
  const where = !s.full_window ? `chưa đủ ${unit} dữ liệu`
    : s.at_new_high ? `<b class="pos">đang ở đỉnh ${unit}</b>`
    : `cách đỉnh ${unit} <b>${pct(s.from_high_pct, 1)}</b> (đỉnh ${dmy(s.high_on)})`;
  const lead = s.leads_on ? ` · lập đỉnh mới trước giá gần nhất <b>${dmy(s.leads_on)}</b>` : "";
  return `<p class="sub" title="Đường RS = giá đóng cửa / ${esc(r.index || "VN-Index")}, bằng 100 ở điểm đầu biểu đồ. Khác RS Rating (xếp hạng trên toàn thị trường). Chỉ để tham khảo, không tính vào điểm hay nhóm.">
    Đường RS (giá / ${esc(r.index || "VN-Index")}): ${where} · 3 tháng <b class="${tone(s.change_pct)}">${pct(s.change_pct, 1)}</b>${lead}</p>`;
}

const HOLD_TEXT = "5 phiên";
function baseHtml(s) {
  if (!s) return `<p class="muted">Chưa có dữ liệu theo dõi. Chạy: python -m src.main monitor</p>`;
  const legs = (s.contractions || []);
  return `<div class="kv col">
    <span>Dạng nền <b>${esc(BASE_TEXT[s.base_kind] || s.base_kind)}</b></span>
    <span>Pivot <b>${fmt(s.pivot)}</b> · giá cách pivot <b>${pct(s.distance_to_pivot_pct)}</b>${s.held_since
      ? ` <small class="muted" title="Sau breakout hoặc khi thủng stop, pivot và stop của nền đó được giữ ${HOLD_TEXT} để nhãn trạng thái và con số luôn khớp nhau">· giữ từ ${s.held_by === "STOP" ? "phiên thủng stop" : "breakout"} ${dmy(s.held_since)}</small>` : ""}</span>
    <span>Stop tham chiếu <b>${s.stop ? fmt(s.stop) : "–"}</b>${s.stop_breached ? ` · <b class="neg">đã thủng stop${s.stop_breached_on ? " " + dmy(s.stop_breached_on) : ""}</b>` : ""}${s.stop_pending
      ? ' · <span class="muted" title="Giá đã ở dưới mức này từ khi nền đang hình thành; stop chỉ áp dụng cho lệnh mua ở pivot">stop dự kiến nếu mua ở pivot — giá đang dưới mức này</span>' : ""}${s.stop
      ? "" : ' <small class="muted">chỉ có khi đang có nền đạt chuẩn (có điểm mua ở pivot)</small>'}</span>
    ${s.legacy_hold ? `<span class="muted small">Pivot và stop giữ theo cách tính trước ngày 11/10/2026 tới ${s.legacy_until ? dmy(s.legacy_until) : `hết ${HOLD_TEXT}`}${s.legacy_new
      ? `; theo cách tính mới: ${esc(BASE_TEXT[s.legacy_new.base_kind] || s.legacy_new.base_kind || "–")}, pivot ${fmt(s.legacy_new.pivot)}, stop ${s.legacy_new.stop ? fmt(s.legacy_new.stop) : "–"}` : ""}</span>` : ""}
    <span>Các nhịp co hẹp chính <b>${legs.length ? `${legs.length} nhịp: ${legs.slice(-4).map((x) => fmt(x, 0)).join(" → ")}%` : "–"}</b></span>
    <span>Khối lượng cạn <b>${s.volume_dryup === true ? "có" : s.volume_dryup === false ? "chưa" : "–"}</b> · hôm nay <b>${s.volume_ratio ? fmt(s.volume_ratio, 2) + " lần trung bình" : "–"}</b></span>
    <span>So với VN-Index 63 phiên <b>${s.rs_vs_index_63d == null ? "–" : (s.rs_vs_index_63d > 0 ? "+" : "") + fmt(s.rs_vs_index_63d, 1) + " điểm %"}</b></span>
    <span class="muted">Đánh giá theo phiên ${dmy(s.trade_date)}</span></div>`;
}

function baseChecksHtml(base) {
  if (!base) return "";
  const icon = (ok) => ok === true ? '<span class="ok">✓</span>' : ok === false ? '<span class="ko">✗</span>' : '<span class="na">?</span>';
  const over = base.overhead;
  // Supply overhead: the left-side high above the pivot (tester M04). Information from 5%, a warning from 10%.
  const overHtml = over ? `<p class="${over.level === "warn" ? "warn" : "muted"} small">${over.level === "warn" ? "⚠ " : ""}Đỉnh nền bên trái ${fmt(over.top)} cao hơn pivot ${fmt(base.pivot)} ${fmt(over.pct, 1)}%: vùng cung treo — giá có thể gặp lực bán khi lên tới đó.</p>` : "";
  const spikes = (base.spike_dates || []).length ? `<p class="muted small">Đã bỏ phiên giật ${base.spike_dates.map(dmy).join(", ")} (mở cửa ở giá cao nhất rồi đảo chiều) khi tìm đỉnh nền.</p>` : "";
  const span = base.start_date ? (base.top_date && base.top_date !== base.start_date
    ? `Nền tính từ đỉnh trái ${dmy(base.start_date)} (${base.sessions} phiên, sâu ${fmt(base.depth_pct, 1)}%); nhịp co hẹp đo từ đỉnh cao nhất ${dmy(base.top_date)} (${fmt(base.top)}).`
    : `Nền tính từ đỉnh cao nhất ${dmy(base.start_date)} (${fmt(base.top)}) tới nay: ${base.sessions} phiên, sâu ${fmt(base.depth_pct, 1)}%.`) : "";
  if (!(base.checks || []).length) return overHtml + spikes;
  return `<div class="checks"><h3 class="sub-h">${esc(base.checks_title || `Vì sao là "${BASE_TEXT[base.kind] || base.kind}"`)}</h3>
    ${base.checks.map((c) => `<div class="check">${icon(c.ok)} <span>${esc(c.text)}</span></div>`).join("")}
    <p class="sub">Các ngưỡng hình dạng (chữ U, xu hướng trước nền, tay cầm) là cách đo triển khai [TRIỂN KHAI] — vẫn nên tự xem biểu đồ. ${span}</p>
    ${overHtml}${spikes}</div>`;
}

function questionsHtml(q, baseKind) {
  if (!q || !q.status) return `<p class="muted">Chưa có đánh giá. Chạy: python -m src.main monitor</p>`;
  const mark = (v) => v === "PASS" ? '<span class="ok">✓</span>' : v === "NEAR" ? '<span class="na">~</span>' : '<span class="ko">✗</span>';
  const trendText = { PASS: "Đạt đủ 8/8 tiêu chí (Stage 2)", NEAR: "Gần đạt (nhóm B)", NO: "Chưa đạt" };
  return `<div class="checks">
    <div class="check">${mark(q.trend)} <span><b>Xu hướng:</b> ${esc(trendText[q.trend] || q.trend)}</span></div>
    <div class="check">${mark(q.setup === "PASS" && baseKind === "BASE" ? "NEAR" : q.setup)} <span><b>Nền giá:</b> ${q.setup !== "PASS" ? "chưa có nền đạt chuẩn"
      : baseKind === "BASE" ? "có vùng tích lũy, chưa đạt mẫu hình chuẩn (cốc, VCP, nền phẳng)" : "đang tạo nền"}</span></div>
    <div class="check">${mark(q.entry === "IN_ZONE" ? "PASS" : q.entry === "APPROACHING" ? "NEAR" : "NO")} <span><b>Điểm vào:</b> ${esc(q.entry_text || "")}</span></div>
    <p class="q-status">${stBadge(q.status)}
      ${q.decided_by ? `<span class="muted"> theo sự kiện ${esc(EVENT_TEXT[q.decided_by[1]] || q.decided_by[1])} ngày ${dmy(q.decided_by[0])}</span>` : ""}</p>
    <p class="mkt mkt-${esc(q.market_level || "")}">${esc(q.market_note || "")}</p>
    <p class="sub">Trạng thái mô tả vị trí của cổ phiếu trong quy trình SEPA — không phải tín hiệu mua.</p></div>`;
}

function techHtml(t) {
  if (!t || !t.overall) return `<p class="muted">Chưa có dữ liệu. Chạy: python -m src.main monitor</p>`;
  const voteIcon = (v) => v == null ? '<span class="muted">không đủ dữ liệu</span>' : v > 0 ? '<span class="ok">tích cực</span>' : v < 0 ? '<span class="ko">tiêu cực</span>' : '<span class="muted">trung tính</span>';
  const table = (r) => `<table class="qtable vote-table"><tbody>${r.votes.map((v) =>
    `<tr><td>${esc(v.name)}</td><td>${v.value == null ? "–" : fmt(v.value, 2)}</td><td>${voteIcon(v.vote)}</td></tr>`).join("")}</tbody></table>`;
  const p = t.pattern;
  return `<div class="tech-sum">
      <div><span class="muted small">Xếp hạng chung</span>${rtBadge(t.overall)}</div>
      <div><span class="muted small">Đường MA</span>${rtBadge(t.ma)}</div>
      <div><span class="muted small">Dao động</span>${rtBadge(t.osc)}</div>
      <div><span class="muted small">Mẫu nến phiên gần nhất</span>${patBadge(p)}</div>
    </div>
    ${p ? `<p class="small">${esc(p.name)}: thiên hướng ${esc(p.bias)}, ${esc(p.note)}${p.stat ? `; Bulkowski (thị trường Mỹ): ${esc(p.stat)}` : ""}. Xem hình minh họa ở tab Học.</p>` : ""}
    <details class="votes"><summary>Xem từng chỉ báo (${t.ma.positive + t.osc.positive} tích cực / ${t.ma.negative + t.osc.negative} tiêu cực)</summary>
    <div class="grid2">
      <div><h3 class="small muted">Đường trung bình (${t.ma.positive} / ${t.ma.negative} / ${t.ma.neutral})</h3>${table(t.ma)}</div>
      <div><h3 class="small muted">Chỉ báo dao động (${t.osc.positive} / ${t.osc.negative} / ${t.osc.neutral})</h3>${table(t.osc)}</div>
    </div></details>
    <p class="muted small">Chỉ báo ngắn hạn theo công thức TradingView. "Tích cực mạnh" tương ứng "Mua mạnh" trên TradingView; đây không phải quy tắc SEPA,
      chưa kiểm định trên dữ liệu Việt Nam và không phải tín hiệu mua bán.</p>`;
}

function fundHtml(f, demoted) {
  if (!f) return `<p class="muted">Chưa có dữ liệu.</p>`;
  const yoy = (arr) => (arr || []).map((v) => `<span class="${tone(v)}">${pct(v, 0)}</span>`).join(" · ") || "–";
  const bn = (v) => (v === null || v === undefined ? "–" : fmt(v / 1e9, 0));
  const q = (f.quarters || []).map((r) => `<tr><td>${esc(r.period_end)}</td><td>${bn(r.revenue_net)}</td><td>${bn(r.net_income_parent)}</td></tr>`).join("");
  // Banks and securities firms: their own template (VNDirect); "revenue" is that template's top line.
  const rev = f.revenue_text || "doanh thu thuần", Rev = rev.charAt(0).toUpperCase() + rev.slice(1);
  return `${demoted ? `<p class="warn">${esc(demoted)}</p>` : ""}
    <p>${fundBadge(f)} <b>${esc(f.grade_text || "")}</b>${f.code33 ? ' · <span class="lead">Code 33</span>' : ""}${f.template_text
      ? ` · <span class="muted">mẫu BCTC ${esc(f.template_text)}</span>` : ""}</p>
    ${(f.reasons || []).length ? `<p class="sub">${esc(f.reasons.join("; "))}</p>` : ""}
    <div class="kv col">
      <span>Lợi nhuận sau thuế công ty mẹ so với cùng kỳ (3 quý, mới nhất trước) <b>${yoy(f.profit_yoy)}</b></span>
      <span>${esc(Rev)} so với cùng kỳ <b>${yoy(f.revenue_yoy)}</b></span>
      <span>Biên lợi nhuận so với cùng kỳ <b>${f.margin_change == null ? "–" : (f.margin_change > 0 ? "+" : "") + fmt(f.margin_change, 1) + " điểm %"}</b></span>
      ${(f.notes || []).map((n) => `<span>${esc(n)}</span>`).join("")}</div>
    ${q ? `<table class="qtable"><thead><tr><th>Quý</th><th>${esc(Rev)} (tỷ đ)</th><th>LNST công ty mẹ (tỷ đ)</th></tr></thead><tbody>${q}</tbody></table>` : ""}
    <p class="sub">Nguồn: ${esc(f.source || "")}. ${esc(f.eps_proxy || "")} thay cho EPS.</p>`;
}

function industryHtml(row, stat) {
  if (!row) return `<p class="muted">Chưa phân ngành. Chạy: python -m src.main industry</p>`;
  const path = [row.sector, row.industry_group, row.industry].filter(Boolean).map(esc).join(" › ");
  if (!stat) return `<p>${path}</p><p class="sub">Ngành chưa được xếp hạng trong lượt lọc này.</p>`;
  return `<p>${path}</p>
    <div class="kv col">
      <span>RS ngành <b class="${stat.leading ? "lead" : ""}">${stat.rs_percentile == null ? "chưa xếp hạng (ít mã)" : fmt(stat.rs_percentile)}</b>${stat.leading ? " · ngành dẫn dắt" : ""}</span>
      <span>Số mã đủ thanh khoản <b>${stat.count}</b> · trên MA50 <b>${pct(stat.above_ma50_pct, 0, false)}</b> · trên MA200 <b>${pct(stat.above_ma200_pct, 0, false)}</b></span>
      <span>Trong danh sách chính <b>${esc((stat.leaders || []).join(", ") || "–")}</b></span></div>
    <p class="sub">Nguồn phân ngành: Vietstock (chưa đối chiếu). Chỉ hiển thị, không tính vào điểm.</p>`;
}

// News labels in Vietnamese (the stored codes stay English).
const NEWS_TYPE_TEXT = { CAPITAL_RAISING: "Tăng vốn", DIVIDEND: "Cổ tức", EARNINGS: "Kết quả kinh doanh",
  GOVERNANCE: "Quản trị", INSIDER: "Giao dịch nội bộ", LEGAL: "Pháp lý", LISTING: "Niêm yết", MNA: "Mua bán sáp nhập",
  SHAREHOLDER_MEETING: "Đại hội cổ đông", OTHER: "Khác" };
const ASSERTION_TEXT = { FACT: "Sự kiện đã xác nhận", INFERENCE: "Chưa đối chiếu văn bản gốc", ESTIMATE: "Ước tính", RUMOUR: "Tin đồn" };
const SOURCE_TEXT = { cafef: "CafeF", cafef_rss: "CafeF", vietstock: "Vietstock" };
const newsType = (t) => NEWS_TYPE_TEXT[t] || t;
const mentionTag = (e) => (e.role === "mention" ? '<span class="badge muted-badge" title="Mã chỉ được nhắc tới trong bài, tin không nói về mã này">nhắc tới</span>' : "");

// Event texts stored before 2026-10-11 used English formats ("39,020", "1.62×", "2026-10-09") and the
// base codes ("nền BASE", "CUP_HANDLE"): show them the Vietnamese way (tester B14, B15).
const lowerFirst = (t) => (t.length > 1 && t[1] === t[1].toLowerCase() ? t[0].toLowerCase() + t.slice(1) : t);
const BASE_CODES = /([Nn])ền (VCP|FLAT_BASE|CUP_HANDLE|CUP|BASE|NEAR_HIGH|SHARP_PULLBACK|EXTENDED|TOO_DEEP|BROKEN)\b/g;
function vnText(t) {
  return String(t || "")
    .replace(/(\d{4})-(\d{2})-(\d{2})/g, "$3/$2/$1")
    .replace(/(\d),(\d{3})(?!\d)/g, "$1.$2").replace(/(\d),(\d{3})(?!\d)/g, "$1.$2")
    .replace(/(\d)\.(\d{1,2})(×|%| lần)/g, "$1,$2$3")
    .replace(BASE_CODES, (_, n, k) => (k === "BASE" ? `${n}ền` : n === "N" ? BASE_TEXT[k] : lowerFirst(BASE_TEXT[k])))
    .replace(/\b(FLAT_BASE|CUP_HANDLE|NEAR_HIGH|SHARP_PULLBACK|TOO_DEEP)\b/g, (k) => lowerFirst(BASE_TEXT[k]));
}
function newsItem(e) {
  const seen = new Set();
  const arts = (e.articles || []).filter((a) => {
    const source = SOURCE_TEXT[a.source_id] || a.source_id;
    return !seen.has(source) && seen.add(source);
  }).map((a) => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(SOURCE_TEXT[a.source_id] || a.source_id)} ↗</a>`).join("");
  const main = (e.articles || []).find((a) => String(a.source_id).startsWith("cafef")) || (e.articles || [])[0];
  return `<article class="stock-news-card${e.role === "mention" ? " mention" : ""}">
    <h4 class="stock-news-title">${main ? `<a href="${esc(main.url)}" target="_blank" rel="noopener">${esc(e.headline)}</a>` : esc(e.headline)}</h4>
    ${e.what_happened ? `<p class="stock-news-description">${esc(e.what_happened)}</p>` : ""}
    <div class="stock-news-meta"><time${e.occurred_at ? ` datetime="${esc(e.occurred_at)}"` : ""}>${dmy(e.occurred_at)}${e.occurred_at ? ` · ${esc(String(e.occurred_at).slice(11, 16))}` : ""}</time><span class="stock-news-sources">${arts || "Chưa rõ nguồn"}</span><span>${esc(newsType(e.event_type) || "Khác")}</span>${mentionTag(e)}${Number(e.article_count) > 1 ? `<span>${Number(e.article_count)} bài liên quan</span>` : ""}
    <details class="stock-news-provenance"><summary>Chi tiết nguồn</summary><p>${esc(ASSERTION_TEXT[e.assertion_type] || e.assertion_type || "Chưa đối chiếu văn bản gốc")}${e.best_source_level != null ? ` · Cấp nguồn ${esc(e.best_source_level)}` : ""}${e.direction ? ` · ${esc(e.direction)}` : ""}</p></details></div>
  </article>`;
}

function stockNewsHtml(ticker, news, filtered = false) {
  return `<div class="stock-news-heading"><div><h3>Tin tức <span>${esc(ticker)}</span></h3><p>${news.length ? `Mới nhất ${dmy(news[0].occurred_at)}` : "Thông tin và công bố liên quan đến doanh nghiệp"}</p></div><span class="stock-news-count">${news.length} tin</span></div>`
    + (news.length ? `<div class="stock-news-grid">${news.map(newsItem).join("")}</div>` : filtered ? '<div class="stock-news-empty"><strong>Không có tin trong ngày đã chọn</strong><p>Thử chọn ngày khác hoặc bấm Bỏ lọc để xem tất cả tin đang có.</p></div>' : '<div class="stock-news-empty"><strong>Chưa có tin cho mã này</strong><p>Tin mới sẽ xuất hiện sau lượt cập nhật tiếp theo.</p></div>');
}

// ---------------------------------------------------------------- chart
const MA_COLORS = { MA20: "#a855f7", MA50: "#2563eb", MA150: "#f59e0b", MA200: "#ef4444" };
const OSC_COLORS = { RSI: "#14b8a6", MACD: "#0ea5e9", SIGNAL: "#f97316" };
const LAYERS = [
  { group: "Đường trung bình" },
  { k: "MA20", label: "MA20", color: MA_COLORS.MA20 }, { k: "MA50", label: "MA50", color: MA_COLORS.MA50 },
  { k: "MA150", label: "MA150", color: MA_COLORS.MA150 }, { k: "MA200", label: "MA200", color: MA_COLORS.MA200 },
  { group: "Mức giá" },
  { k: "pivot", label: "Pivot và stop tham chiếu", color: "#0ea5e9", kind: "stock" },
  { k: "dist", label: "Ngày phân phối và FTD", color: "#ef4444", kind: "index" },
  { k: "zones", label: "Hỗ trợ / kháng cự gần nhất", color: "#10b981" },
  { k: "channel", label: "Trendline và kênh giá", color: "#ec4899" },
  { k: "base", label: "Nền giá (đỉnh, giữa, đáy)", color: "#667085", kind: "stock" },
  { group: "Khung phụ" },
  { k: "volume", label: "Khối lượng", color: "#94a3b8" },
  { k: "rsi", label: "RSI (14)", color: OSC_COLORS.RSI },
  { k: "macd", label: "MACD (12, 26, 9)", color: OSC_COLORS.MACD },
  { k: "rsline", label: "Đường RS (so với VN-Index)", color: "#334155", kind: "stock" },
];
// Chart timeframe: daily bars come with the ticker / index payload; weekly and monthly
// bars (and their MAs, RSI, MACD) from /api/bars, cached for the page's life.
const TF_TEXT = { D: "ngày", W: "tuần", M: "tháng" };
// The timeframe is kept apart for the index and for stocks: a month chosen on VN-Index no longer carries into a stock (R3-12).
const tfKey = () => (state.detailKind === "index" ? "sepa.tf.index.v1" : "sepa.tf.v1");
const loadTf = () => { const v = store.get(tfKey(), "D"); return ["D", "W", "M"].includes(v) ? v : "D"; };
state.tf = loadTf();
state.tfCache = {};
state.maPeriods = { MA20: 20, MA50: 50, MA150: 150, MA200: 200 };
const DAILY_EQUIV = { MA20: 20, MA50: 50, MA150: 150, MA200: 200 };
const maName = (k) => `MA${state.maPeriods[k] ?? k.slice(2)}`;
const maLabel = (k) => (state.tfNow && state.tfNow !== "D"
  ? `${maName(k)} ${TF_TEXT[state.tfNow]}${state.tfNow === "W" ? ` (≈ MA${DAILY_EQUIV[k]} ngày)` : ""}` : maName(k));

async function drawCurrentChart() {
  const d = state.detail;
  if (!d) return;
  $$(".tf-seg button").forEach((b) => b.classList.toggle("on", b.dataset.tf === state.tf));
  const tf = state.tf;
  if (tf === "D") { drawChart(d); return; }
  const key = `${state.detailKind}:${state.detailTicker}:${tf}`;
  let tfd = state.tfCache[key];
  if (!tfd) {
    try {
      tfd = await get(`/api/bars?${state.detailKind === "index" ? "index" : "ticker"}=${encodeURIComponent(state.detailTicker)}&tf=${tf}`);
    } catch (err) {
      if (state.detail === d && state.tf === tf) $("#chart").innerHTML = `<p class="empty">${esc(err.message)}</p>`;
      return;
    }
    state.tfCache[key] = tfd;
  }
  // A slower reply for another ticker or timeframe must not overwrite what was chosen since.
  if (state.detail !== d || state.tf !== tf) return;
  // Trendlines / channel come from the weekly or monthly candles; zones stay the daily chart's.
  drawChart({ ...d, bars: tfd.bars, ma: tfd.ma, osc: tfd.osc, ma_periods: tfd.ma_periods, tf, daily_count: d.bars.length, rs_line: tfd.rs_line,
    levels: { ...(d.levels || {}), ...(tfd.levels || { trendlines: {}, channel: null }) } });
}

const DEFAULT_LAYERS = { MA20: false, MA50: true, MA150: true, MA200: true, pivot: true, dist: true, zones: false, channel: false, base: false, volume: true, rsi: true, macd: false, rsline: false };
const LAYERS_KEY = "sepa.layers.v1";
state.layers = { ...DEFAULT_LAYERS, ...store.get(LAYERS_KEY, {}) };

function renderLayersMenu() {
  const kind = state.detailKind || "stock";
  const text = (l) => (MA_COLORS[l.k] ? maLabel(l.k) : l.label);
  $("#layers-menu").innerHTML = LAYERS.filter((l) => !l.kind || l.kind === kind).map((l) => (l.group ? `<div class="menu-h">${esc(l.group)}</div>`
    : `<label><input type="checkbox" data-layer="${l.k}"${state.layers[l.k] ? " checked" : ""}><i class="sw" style="background:${l.color}"></i>${esc(text(l))}</label>`)).join("")
    + `<button class="linkbtn menu-reset" data-layer-reset type="button">Về mặc định</button>`;
  $("#layers-n").textContent = LAYERS.filter((l) => l.k && (!l.kind || l.kind === kind) && state.layers[l.k]).length;
}

function chartOptions(dark, css, extra = {}) {
  return {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: css.getPropertyValue("--muted").trim(), fontSize: 11, fontFamily: "Inter, system-ui, sans-serif" },
    grid: { vertLines: { visible: false }, horzLines: { color: dark ? "#1f242c" : "#f1f2f4" } },
    rightPriceScale: { borderVisible: false, minimumWidth: 70 },
    timeScale: { borderVisible: false },
    crosshair: { mode: 0 },
    ...extra,
  };
}

// Price, RSI and MACD in up to three stacked charts whose time axes move
// together (lightweight-charts v4 has no native panes). Only the layers the
// user switched on are drawn; the bottom-most visible chart carries the dates.
function drawChart(d) {
  const L = state.layers;
  const tf = d.tf || "D";
  state.tfNow = tf;
  state.maPeriods = d.ma_periods || DAILY_EQUIV;
  // Markers and the base are dated by session; on a weekly / monthly chart they sit on the bar holding that session.
  const barOf = (date) => {
    if (!date || tf === "D") return date;
    let t = null;
    for (const b of d.bars) { if (b.trade_date <= date) t = b.trade_date; else break; }
    return t;
  };
  const panes = { price: $("#chart"), rsi: $("#rsi-chart"), macd: $("#macd-chart") };
  (state.charts || []).forEach((c) => c.remove());
  state.charts = [];
  Object.values(panes).forEach((el) => { el.innerHTML = ""; });
  $("#rsi-chart").hidden = $("#rsi-label").hidden = !L.rsi;
  $("#macd-chart").hidden = $("#macd-label").hidden = !L.macd;
  renderLayersMenu();
  if (!window.LightweightCharts) { panes.price.innerHTML = `<p class="empty">Không tải được thư viện biểu đồ (cần internet).</p>`; return; }
  if (!d.bars.length) { panes.price.innerHTML = `<p class="empty">Chưa có dữ liệu giá.</p>`; return; }
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const css = getComputedStyle(document.documentElement);
  const up = css.getPropertyValue("--up").trim(), down = css.getPropertyValue("--down").trim();
  const bottom = L.macd ? "macd" : L.rsi ? "rsi" : "price";
  const dec = d.decimals || 0;                      // VND: whole dong; an index: points with 2 decimals
  const px = (v) => fmt(v, dec);
  // MACD is in price units: a 4,000-dong stock or an index needs decimals a blue chip does not.
  const macdFmt = (v) => fmt(v, Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2);
  // Every pane gets one point per session, blank where an indicator has no value yet (RSI's
  // first 14 sessions). The panes scroll together by bar position, so a pane missing those
  // sessions would drift 14 bars from the candles (found 2026-10-04).
  const onBars = (pts) => { const at = new Map((pts || []).map((p) => [p.time, p])); return d.bars.map((b) => at.get(b.trade_date) || { time: b.trade_date }); };

  const price = LightweightCharts.createChart(panes.price, chartOptions(dark, css, {
    localization: { priceFormatter: px },
    timeScale: { borderVisible: false, visible: bottom === "price" },
  }));
  const candles = price.addCandlestickSeries({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false });
  candles.setData(d.bars.map((b) => ({ time: b.trade_date, open: b.open, high: b.high, low: b.low, close: b.close })));
  if (L.volume) {
    const vol = price.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    price.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    vol.setData(d.bars.map((b, i) => ({
      time: b.trade_date, value: b.volume,
      color: i && b.close < d.bars[i - 1].close ? (dark ? "#4a2626" : "#f5d0cc") : (dark ? "#1f4034" : "#c9e9d6"),
    })));
  }
  for (const [k, pts] of Object.entries(d.ma)) {
    if (!L[k]) continue;
    price.addLineSeries({ color: MA_COLORS[k], lineWidth: k === "MA20" ? 1 : 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(pts);
  }
  // RS line: in the lower part of the price pane on its own hidden scale, as MarketSmith draws it;
  // a dot where the line set a new high while the price had not (it leads the price).
  const rsLine = L.rsline && state.detailKind !== "index" && d.rs_line && d.rs_line.points.length ? d.rs_line : null;
  if (rsLine) {
    const rsColor = dark ? "#cbd5e1" : "#334155";
    const rsSeries = price.addLineSeries({ priceScaleId: "rs", color: rsColor, lineWidth: 1, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false });
    price.priceScale("rs").applyOptions({ scaleMargins: { top: 0.58, bottom: L.volume ? 0.17 : 0.02 } });
    rsSeries.setData(rsLine.points);
    const lead = new Set(rsLine.leads || []);
    rsSeries.setMarkers(rsLine.points.filter((p) => lead.has(p.time))
      .map((p) => ({ time: p.time, position: "inBar", color: "#2563eb", shape: "circle", size: 0.4 })));
  }
  const base = d.base || {};
  const markers = [];
  const pivot = d.base ? base.pivot : (d.state || {}).pivot, stop = d.base ? base.stop : (d.state || {}).stop;
  const lastBar = d.bars[d.bars.length - 1].trade_date;
  const fromBase = barOf(base.start_date) || d.bars[0].trade_date;
  // Price levels run from the base's start, not across the whole chart (tester M04 note). Only pivot and
  // stop carry a label on the price axis; the other levels are named in the legend (M12: eight labels
  // pushed each other off their lines).
  const segment = (value, color, style, title) => price.addLineSeries({ color, lineWidth: 1, lineStyle: style,
    priceLineVisible: false, lastValueVisible: !!title, title: title || "", crosshairMarkerVisible: false })
    .setData(fromBase === lastBar ? [{ time: lastBar, value }] : [{ time: fromBase, value }, { time: lastBar, value }]);
  const breached = stop && base.stop_breached, pending = stop && !breached && base.stop_pending;
  const stopTitle = breached ? `stop đã thủng${base.stop_breached_on ? " " + dm(base.stop_breached_on) : ""}`
    : pending ? "stop dự kiến (nếu mua ở pivot)" : "stop";
  if (L.pivot && pivot) segment(pivot, "#0ea5e9", 2, "pivot");
  // A broken stop is no longer a level under the price (tester M01): grey and dotted, and it says so.
  if (L.pivot && stop) segment(stop, breached || pending ? "#9aa4b2" : "#f43f5e", breached ? 1 : 2, stopTitle);
  const nearKey = (v) => [pivot, stop].some((k) => k && Math.abs(v / k - 1) < 0.005);   // merged into pivot / stop
  // "Sát đỉnh" and similar reads have a top but no measured base (no low): nothing to draw.
  if (L.base && base.top && base.start_date && base.low != null && base.low_date) {
    const guide = dark ? "#9aa4b2" : "#667085";
    if (!(L.pivot && nearKey(base.top))) segment(base.top, guide, 0, "");
    if (!(L.pivot && nearKey(base.low))) segment(base.low, guide, 0, "");
    if (base.mid && !(L.pivot && nearKey(base.mid))) segment(base.mid, guide, 3, "");
    if ((base.swings || []).length > 1) {
      price.addLineSeries({ color: dark ? "#e5e7eb" : "#111827", lineWidth: 1, lineStyle: 2, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false })
        .setData([...new Map(base.swings.map((p) => [barOf(p.time), { time: barOf(p.time), value: p.value }])).values()]);
    }
    markers.push(
      { time: base.start_date, position: "aboveBar", color: guide, shape: "arrowDown", text: "Bắt đầu nền" },
      { time: base.low_date, position: "belowBar", color: guide, shape: "arrowUp", text: `Đáy −${fmt(base.depth_pct, 1)}%` },
    );
    if (base.right_lip_date && (base.kind === "CUP_HANDLE" || base.kind === "CUP"))
      markers.push({ time: base.right_lip_date, position: "aboveBar", color: "#0ea5e9", shape: "arrowDown", text: "Miệng cốc phải" });
    if (base.handle_low_date && base.kind === "CUP_HANDLE")
      markers.push({ time: base.handle_low_date, position: "belowBar", color: "#0ea5e9", shape: "arrowUp", text: "Đáy tay cầm" });
  }
  // The index: IBD distribution days of the current window and the follow-through day.
  if (L.dist && state.detailKind === "index") {
    for (const t of d.distribution_dates || []) markers.push({ time: t, position: "aboveBar", color: down, shape: "arrowDown", text: "PP" });
    const ftd = (d.market || {}).ftd || {};
    if (ftd.date) markers.push({ time: ftd.date, position: "belowBar", color: up, shape: "arrowUp", text: "FTD" });
    else if (ftd.last_ftd) markers.push({ time: ftd.last_ftd.date, position: "belowBar", color: "#9aa4b2", shape: "arrowUp",
      text: ftd.last_ftd.outcome === "failed" ? "FTD (thất bại)" : "FTD (hết hiệu lực)" });
  }
  // On a weekly / monthly bar, markers of the same kind merge into one ("PP×3").
  const merged = new Map();
  for (const m of markers) {
    const t = barOf(m.time);
    if (!t) continue;
    const key = `${t}|${m.text}`;
    if (merged.has(key)) merged.get(key).n += 1; else merged.set(key, { ...m, time: t, n: 1 });
  }
  const placed = [...merged.values()].map(({ n, ...m }) => (n > 1 ? { ...m, text: `${m.text}×${n}` } : m));
  if (placed.length) {
    const seen = new Set();          // one marker per bar and side: the first wins
    candles.setMarkers(placed.sort((a, b) => (a.time < b.time ? -1 : 1))
      .filter((m) => !seen.has(`${m.time}|${m.position}`) && seen.add(`${m.time}|${m.position}`)));
  }
  drawLevels(price, candles, d);
  const charts = [price];

  if (L.rsi) {
    const rsi = LightweightCharts.createChart(panes.rsi, chartOptions(dark, css, {
      timeScale: { borderVisible: false, visible: bottom === "rsi" },
      rightPriceScale: { borderVisible: false, minimumWidth: 70, scaleMargins: { top: 0.06, bottom: 0.06 } },
      localization: { priceFormatter: (p) => fmt(p, 0) },
    }));
    const line = rsi.addLineSeries({ color: OSC_COLORS.RSI, lineWidth: 2, priceLineVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    line.setData(onBars(d.osc && d.osc.RSI));
    for (const [level, style] of [[70, 2], [50, 3], [30, 2]]) line.createPriceLine({ price: level, color: dark ? "#555" : "#c4c4c4", lineWidth: 1, lineStyle: style, axisLabelVisible: false });
    charts.push(rsi);
  }
  if (L.macd) {
    // Prices are in VND, so MACD is too: no decimals.
    const macd = LightweightCharts.createChart(panes.macd, chartOptions(dark, css, { localization: { priceFormatter: macdFmt } }));
    macd.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false })
      .setData(onBars(d.osc && d.osc.HIST).map((p) => (p.value == null ? p : { ...p, color: p.value >= 0 ? (dark ? "#2f6f55" : "#86c9a8") : (dark ? "#7a3a3a" : "#eda39c") })));
    macd.addLineSeries({ color: OSC_COLORS.MACD, lineWidth: 2, priceLineVisible: false }).setData(onBars(d.osc && d.osc.MACD));
    macd.addLineSeries({ color: OSC_COLORS.SIGNAL, lineWidth: 1, priceLineVisible: false }).setData(onBars(d.osc && d.osc.SIGNAL));
    charts.push(macd);
  }
  // All panes need the same trading-bar grid. A single future endpoint compresses
  // a 20-session ray into one slot and moves the drawn line away from its anchors.
  const future = L.channel ? ((d.levels || {}).projection_dates || []) : [];
  if (future.length) {
    seedProjectionGrid(charts, d.bars, future);
  }
  let syncing = false;
  charts.forEach((c) => c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (syncing || !range) return;
    syncing = true;
    charts.forEach((o) => { if (o !== c) o.timeScale().setVisibleLogicalRange(range); });
    syncing = false;
  }));
  // Last ~150 sessions (3 years of weeks, 10 years of months), widened to include the whole base.
  let fromIdx = Math.max(0, d.bars.length - { D: 150, W: 156, M: 120 }[tf]);
  const baseIdx = base.start_date ? d.bars.findIndex((b) => b.trade_date === barOf(base.start_date)) : -1;
  if (baseIdx >= 0) fromIdx = Math.max(0, Math.min(fromIdx, baseIdx - 15));
  const lastDate = d.bars[d.bars.length - 1].trade_date;
  const to = state.projectedEnd && state.projectedEnd > lastDate ? state.projectedEnd : lastDate;
  price.timeScale().setVisibleRange({ from: d.bars[fromIdx].trade_date, to });
  state.charts = charts;

  // Legend follows the crosshair; it shows the last session otherwise.
  const index = new Map(d.bars.map((b, i) => [b.trade_date, i]));
  const maAt = Object.fromEntries(Object.entries(d.ma).map(([k, pts]) => [k, new Map(pts.map((p) => [p.time, p.value]))]));
  const rsiAt = new Map(((d.osc && d.osc.RSI) || []).map((p) => [p.time, p.value]));
  const rsLineAt = new Map(((rsLine && rsLine.points) || []).map((p) => [p.time, p.value]));
  const oscAt = (k) => new Map(((d.osc && d.osc[k]) || []).map((p) => [p.time, p.value]));
  const macdAt = oscAt("MACD"), sigAt = oscAt("SIGNAL"), histAt = oscAt("HIST");
  const baseLegend = L.base && base.top && base.low != null
    ? `<span class="muted">nền: đỉnh <b>${px(base.top)}</b> · giữa <b>${px(base.mid)}</b> · đáy <b>${px(base.low)}</b></span>` : "";
  const legend = (i) => {
    const b = d.bars[i], prev = d.bars[i - 1];
    // Daily candles: against that session's reference price when it was read, like the boards.
    const refPct = tf === "D" && d.ref_pct ? d.ref_pct[b.trade_date] : null;
    const chg = refPct ?? (prev ? (b.close / prev.close - 1) * 100 : null);
    const mas = Object.keys(MA_COLORS).filter((k) => L[k]).map((k) => `<span><i style="background:${MA_COLORS[k]}"></i>${maName(k)} <b>${px(maAt[k] && maAt[k].get(b.trade_date))}</b></span>`).join("");
    const when = tf === "W" ? `Tuần ${dm(b.trade_date)}–${dmy(b.period_end)}`
      : tf === "M" ? `Tháng ${b.trade_date.slice(5, 7)}/${b.trade_date.slice(0, 4)}` : dmy(b.trade_date);
    const mv = (m) => { const v = m.get(b.trade_date); return v == null ? "–" : macdFmt(v); };
    $("#legend").innerHTML = `<div class="lg-row"><b>${when}</b>${b.partial ? ' <span class="stale" title="Nến chưa kết thúc: giá đóng là giá phiên gần nhất">chưa kết thúc</span>' : ""}
        <span>Mở <b>${px(b.open)}</b></span><span>Cao <b>${px(b.high)}</b></span><span>Thấp <b>${px(b.low)}</b></span>
        <span>Đóng <b>${px(b.close)}</b> <span class="${tone(chg)}">${pct(chg, 2)}</span></span><span>KL <b>${fmt(b.volume)}</b></span></div>
      <div class="lg-row">${mas}${L.rsi ? `<span><i style="background:${OSC_COLORS.RSI}"></i>RSI <b>${fmt(rsiAt.get(b.trade_date), 1)}</b></span>` : ""}${rsLine
        ? `<span title="Giá / ${esc(rsLine.index || "VN-Index")}, 100 = điểm đầu biểu đồ"><i style="background:${dark ? "#cbd5e1" : "#334155"}"></i>Đường RS <b>${fmt(rsLineAt.get(b.trade_date), 1)}</b></span>` : ""}
        ${L.macd ? `<span><i style="background:${OSC_COLORS.MACD}"></i>MACD <b>${mv(macdAt)}</b></span><span><i style="background:${OSC_COLORS.SIGNAL}"></i>Signal <b>${mv(sigAt)}</b></span><span>Hist <b>${mv(histAt)}</b></span>` : ""}
        ${L.pivot && pivot ? `<span><i style="background:#0ea5e9"></i>pivot <b>${px(pivot)}</b></span>` : ""}${L.pivot && stop ? `<span><i style="background:${breached || pending ? "#9aa4b2" : "#f43f5e"}"></i>${esc(stopTitle)} <b>${px(stop)}</b></span>` : ""}${baseLegend}</div>`;
  };
  legend(d.bars.length - 1);
  price.subscribeCrosshairMove((p) => {
    const i = p && p.time ? index.get(typeof p.time === "string" ? p.time : `${p.time.year}-${String(p.time.month).padStart(2, "0")}-${String(p.time.day).padStart(2, "0")}`) : undefined;
    legend(i === undefined ? d.bars.length - 1 : i);
  });
}

// Filled shapes behind the candles (channel band, zones): lightweight-charts
// has no filled drawing tools, so this is a series primitive painting on its canvas.
function seedProjectionGrid(charts, bars, future) {
  const grid = [...bars.map((b) => ({ time: b.trade_date })), ...future.map((time) => ({ time }))];
  charts.forEach((c) => c.addLineSeries({ visible: false, priceLineVisible: false, lastValueVisible: false }).setData(grid));
}

class FillPrimitive {
  constructor(shapes) {
    this.shapes = shapes;
    this._view = { zOrder: () => "bottom", renderer: () => ({ draw: (target) => this._draw(target) }) };
  }
  attached({ chart, series, requestUpdate }) {
    this.chart = chart; this.series = series;
    // The first paint can happen before the chart has its final size and range;
    // ask again once layout has settled, and whenever the visible range moves.
    this._repaint = () => requestUpdate();
    requestAnimationFrame(() => requestAnimationFrame(this._repaint));
    chart.timeScale().subscribeVisibleLogicalRangeChange(this._repaint);
  }
  detached() { if (this.chart && this._repaint) this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this._repaint); }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  _draw(target) {
    if (!this.chart) return;
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
      const ts = this.chart.timeScale();
      for (const s of this.shapes) {
        ctx.fillStyle = s.fill;
        if (s.type === "band") {
          const y1 = this.series.priceToCoordinate(s.high), y2 = this.series.priceToCoordinate(s.low);
          if (y1 == null || y2 == null) continue;
          ctx.fillRect(0, y1 * vr, scope.bitmapSize.width, Math.max(1, (y2 - y1) * vr));
        } else {
          const pts = s.points.map(([t, v]) => [ts.timeToCoordinate(t), this.series.priceToCoordinate(v)]);
          if (pts.some(([x, y]) => x == null || y == null)) continue;
          ctx.beginPath();
          pts.forEach(([x, y], k) => (k ? ctx.lineTo(x * hr, y * vr) : ctx.moveTo(x * hr, y * vr)));
          ctx.closePath();
          ctx.fill();
        }
      }
    });
  }
}

const LEVEL_COLORS = {
  channel: ["#ec4899", "rgba(236,72,153,0.16)"],
  SUPPORT: ["#10b981", "rgba(16,185,129,0.18)"],
  RESISTANCE: ["#ef4444", "rgba(239,68,68,0.16)"],
  trendline: "#84cc16",
};

const ZONE_SOURCE = { SWING_HIGH: "đỉnh swing", SWING_LOW: "đáy swing", BREAKOUT_LEVEL: "mức giá từng vượt lên", BREAKDOWN_LEVEL: "mức giá từng thủng xuống" };

function drawLevels(price, candles, d) {
  const lv = d.levels || {};
  const px = (v) => fmt(v, d.decimals || 0);
  const L = state.layers;
  state.projectedEnd = null;
  const texts = [], shapes = [];
  // Channel and trendlines never stretch the price axis (tester M08: ABB's axis ran to 26,000 for a 17,500 price).
  const lineOpts = { lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => null };
  const pts = (rail) => rail.map(([time, value]) => ({ time, value }));
  const later = (a, b) => (!a || b > a ? b : a);
  // Anchor / break dates name the bar: a session, a week (its first session) or a month.
  const tf = d.tf || "D";
  const at = (t) => (tf === "W" ? `tuần ${dmy(t)}` : tf === "M" ? `tháng ${t.slice(5, 7)}/${t.slice(0, 4)}` : dmy(t));
  const on = (t) => (tf === "D" ? `ngày ${dmy(t)}` : at(t));

  if (L.zones) {
    for (const z of lv.zones || []) {
      const sup = z.side === "SUPPORT";
      const [line, fill] = LEVEL_COLORS[z.side];
      shapes.push({ type: "band", low: z.zone_low, high: z.zone_high, fill });
      // No axis label: the zone is named in the text under the chart (M12).
      candles.createPriceLine({ price: z.center, color: line, lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "" });
      // touch_count is the number of sessions whose range crossed the zone, not the number of
      // reversals at it (audit 2026-10-04): say so, and name what the zone was built from.
      const kinds = [...new Set((z.sources || []).map((s) => ZONE_SOURCE[s]).filter(Boolean))].join(", ");
      texts.push(`${sup ? "Hỗ trợ" : "Kháng cự"} gần nhất <b>${px(z.zone_low)}–${px(z.zone_high)}</b> (${pct(z.distance_from_price)})`
        + `${kinds ? ` — hình thành từ ${kinds}` : ""}; giá đi qua vùng này trong ${z.touch_count} / ${d.daily_count || d.bars.length} phiên, gần nhất ${dmy(z.last_tested)}`);
    }
    if (lv.at_zone) texts.push(`Giá đang nằm trong vùng <b>${px(lv.at_zone.zone_low)}–${px(lv.at_zone.zone_high)}</b> (${lv.at_zone.side === "SUPPORT" ? "từng là hỗ trợ" : "từng là kháng cự"}).`);
    if (!(lv.zones || []).length) texts.push("Chưa xác định được vùng hỗ trợ / kháng cự gần giá.");
  }
  if (L.channel) {
    const ch = lv.channel, tl = lv.trendlines || {};
    if (ch) {
      // A broken channel is history, drawn faint and dashed (tester M07).
      const [line, fill] = ch.broken_on ? ["rgba(236,72,153,0.45)", "rgba(236,72,153,0.05)"] : LEVEL_COLORS.channel;
      const rail = ch.broken_on ? { lineWidth: 1, lineStyle: 2 } : {};
      shapes.push({ type: "poly", points: [...ch.upper, ...[...ch.lower].reverse()], fill });
      price.addLineSeries({ ...lineOpts, ...rail, color: line }).setData(pts(ch.upper));
      price.addLineSeries({ ...lineOpts, color: line, lineWidth: 1, lineStyle: 2 }).setData(pts(ch.middle));
      price.addLineSeries({ ...lineOpts, ...rail, color: line }).setData(pts(ch.lower));
      state.projectedEnd = later(state.projectedEnd, ch.upper[ch.upper.length - 1][0]);
      const b = tl[ch.base];
      texts.push(`Kênh giá dựng trên ${ch.base === "SUPPORT" ? "trendline nối 2 đáy" : "trendline nối 2 đỉnh"} ${at(b.anchors[0][0])} → ${at(b.anchors[1][0])}, dốc ${pct(b.slope_pct_month)}/tháng, rộng ${fmt(ch.width_pct, 1)}%: `
        + (ch.broken_on ? `<b>giá đã phá kênh ${on(ch.broken_on)}</b>.` : `giá đang ở <b>${fmt(ch.position_pct, 0)}%</b> chiều cao kênh (0 = cạnh dưới, 100 = cạnh trên).`));
    }
    for (const [kind, t] of Object.entries(tl)) {
      if (ch && ch.base === kind) continue;
      price.addLineSeries({ ...lineOpts, color: t.broken_on ? "rgba(132,204,22,0.5)" : LEVEL_COLORS.trendline,
        ...(t.broken_on ? { lineWidth: 1, lineStyle: 2 } : {}) }).setData(pts([t.start, t.end]));
      if (!t.broken_on) state.projectedEnd = later(state.projectedEnd, t.end[0]);
      // A close beyond the line by less than its tolerance has not broken it yet: say so (HDB 22,400 over 22,228).
      const beyond = t.close_vs_line_pct != null && (kind === "SUPPORT" ? t.close_vs_line_pct < 0 : t.close_vs_line_pct > 0);
      texts.push(`Trendline ${kind === "SUPPORT" ? "nối 2 đáy" : "nối 2 đỉnh"} ${at(t.anchors[0][0])} → ${at(t.anchors[1][0])}, dốc ${pct(t.slope_pct_month)}/tháng, ${t.touches} lần chạm: `
        + (t.broken_on ? `<b>đã bị phá ${on(t.broken_on)}</b>.`
          : `${tf === "D" ? "hôm nay" : "hiện"} ở ${px(t.value_today)}` + (beyond
            ? ` — giá đang ${kind === "SUPPORT" ? "dưới" : "trên"} đường ${fmt(Math.abs(t.close_vs_line_pct), 1)}%, chưa tới ngưỡng xác nhận phá ${fmt(t.tolerance_pct, 1)}%.` : ".")));
    }
    if (!ch && !Object.keys(tl).length) texts.push("Chưa tìm được trendline hợp lệ (cần 2 đỉnh / 2 đáy mà giá không đóng cửa xuyên qua ở giữa).");
  }
  if (shapes.length) candles.attachPrimitive(new FillPrimitive(shapes));
  if (texts.length) texts.push(`<span class="muted">${esc(lv.note || "")}</span>`);
  $("#levels-text").innerHTML = texts.join("<br>");
  $("#levels-text").hidden = !texts.length;
}

// ---------------------------------------------------------------- TradingView widget
const TV_EXCHANGE = { HOSE: "HOSE", HNX: "HNX", UPCOM: "UPCOM" };
function tvSymbol(ticker, exchange) {
  const ex = TV_EXCHANGE[(exchange || "").toUpperCase()];
  return ex ? `${ex}:${ticker}` : ticker;
}

function drawTradingView(ticker, exchange) {
  const symbol = tvSymbol(ticker, exchange);
  const el = $("#tv-chart");
  el.innerHTML = "";
  if ((exchange || "").toUpperCase() === "HOSE") {
    // Observed 2026-09-27: the free embed answers HOSE symbols with
    // "Mã giao dịch này chỉ có trên TradingView"; HNX and UPCoM work.
    el.innerHTML = `<p class="empty">TradingView không cho hiển thị dữ liệu sàn HOSE trong biểu đồ nhúng.<br>
      Dùng tab "Biểu đồ" (cùng số liệu, đã đối chiếu với TradingView) hoặc
      <a href="${esc($("#tv-link").href)}" target="_blank" rel="noopener">mở ${esc(symbol)} trên TradingView ↗</a>.</p>`;
    return;
  }
  if (!window.TradingView) { el.innerHTML = `<p class="empty">Không tải được widget TradingView (cần internet).</p>`; return; }
  const id = `tv_${ticker}_${Date.now()}`;
  el.innerHTML = `<div id="${id}" style="height:100%"></div>`;
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  new TradingView.widget({
    container_id: id, symbol, interval: "D", timezone: "Asia/Ho_Chi_Minh", theme: dark ? "dark" : "light", style: "1",
    locale: "vi_VN", autosize: true, allow_symbol_change: true, hide_side_toolbar: false, withdateranges: true, save_image: true,
    studies: [
      { id: "MASimple@tv-basicstudies", inputs: { length: 50 } },
      { id: "MASimple@tv-basicstudies", inputs: { length: 150 } },
      { id: "MASimple@tv-basicstudies", inputs: { length: 200 } },
      { id: "RSI@tv-basicstudies", inputs: { length: 14 } },
    ],
  });
}

// TradingView's free embed refuses HOSE symbols (and so VN-Index): the switch is shown only for HNX / UPCoM;
// "Mở trên TradingView" stays for every ticker (tester R3-14).
const tvEmbeddable = () => state.detailKind !== "index" && ["HNX", "UPCOM"].includes((state.detailExchange || "").toUpperCase());
function showChart(which) {
  if (which === "tv" && !tvEmbeddable()) which = "own";
  $(".chart-tabs").hidden = !tvEmbeddable();
  state.chartSource = which;
  $$(".chart-tabs button").forEach((c) => c.classList.toggle("on", c.dataset.chart === which));
  $("#tv-chart").hidden = which !== "tv";
  $("#own-chart").hidden = which !== "own";
  $(".layers").hidden = which !== "own";
  $(".tf-seg").hidden = which !== "own";
}

// ---------------------------------------------------------------- detail: flow pane
async function loadFlow(ticker) {
  const box = $("#d-flow");
  box.innerHTML = `<p class="muted">Đang tải…</p>`;
  let data;
  try { data = await get(`/api/flow?ticker=${encodeURIComponent(ticker)}&sessions=30`); }
  catch (err) { box.innerHTML = `<p class="warn">${esc(err.message)}</p>`; return; }
  if (state.detailTicker !== ticker) return;
  // One row per session; a session DNSE has no foreign figures for stays, blank (tester R3-03).
  const all = data.rows, rows = all.filter((r) => r.foreign_net != null);
  if (!rows.length) {
    box.innerHTML = `<div class="card"><p class="muted">Chưa có dữ liệu khối ngoại theo khối lượng cho ${esc(ticker)}. Chạy: python -m src.main flow --tickers ${esc(ticker)}</p></div>`;
    return;
  }
  const span = (n) => all.slice(0, n).filter((r) => r.foreign_net != null);
  const card = (label, n) => {
    const got = span(n), m = Math.min(n, all.length);
    const v = got.reduce((s, r) => s + r.foreign_net, 0), money = got.reduce((s, r) => s + (r.foreign_net_value || 0), 0);
    const pt = got.reduce((s, r) => s + (r.foreign_net_put_through || 0), 0);
    const buys = got.filter((r) => r.foreign_net > 0).length;
    return `<div class="stat"><span class="muted small">${label}</span><b class="${tone(v)}">${got.length ? sfmt(v) + " cp" : "–"}</b>
      <span class="small"><span class="${tone(money)}">${sfmt(money / 1e9, 2)} tỷ đ</span> · ${buys}/${got.length} phiên mua ròng${Math.abs(pt) >= 0.5
        ? ` · <span title="Phần khối ngoại ròng qua giao dịch thỏa thuận (và lô lẻ), đã nằm trong con số trên">thỏa thuận ${sfmt(pt)} cp</span>` : ""}${got.length < m
        ? ` · <span class="stale" title="DNSE không có số liệu khối ngoại cho các phiên còn lại (thường là không có giao dịch khối ngoại)">${got.length}/${m} phiên có số liệu</span>` : ""}</span></div>`;
  };
  const ordered = [...rows].reverse();
  const max = Math.max(...ordered.map((r) => Math.abs(r.foreign_net)), 1);
  const bw = 100 / ordered.length;
  const bars = ordered.map((r, i) => {
    const h = Math.abs(r.foreign_net) / max * 44;
    return `<rect class="${r.foreign_net >= 0 ? "b-up" : "b-down"}" x="${(i * bw + bw * 0.15).toFixed(2)}" width="${(bw * 0.7).toFixed(2)}" y="${r.foreign_net >= 0 ? 50 - h : 50}" height="${Math.max(h, 0.6)}"><title>${dmy(r.date)}: ${sfmt(r.foreign_net)} cp</title></rect>`;
  }).join("");
  box.innerHTML = `
    <div class="stats">${card("Phiên gần nhất · " + dm(all[0].date), 1)}${card("5 phiên", 5)}${card("10 phiên", 10)}${card("20 phiên", 20)}</div>
    <div class="card"><div class="card-h"><h3>Khối ngoại mua ròng / bán ròng từng phiên (cổ phiếu)</h3><span class="muted small">${dm(ordered[0].date)} → ${dm(rows[0].date)}</span></div>
      <svg class="flowbars" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Biểu đồ khối ngoại ròng"><line x1="0" x2="100" y1="50" y2="50"/>${bars}</svg></div>
    <div class="card"><div class="card-h"><h3>Chi tiết từng phiên</h3></div>
      <div class="table-wrap flat"><table class="flow-table"><thead><tr><th>Phiên</th><th class="num" title="Giá đóng cửa đã điều chỉnh theo quyền (chia/thưởng cổ phiếu), nên có thể lệch bước giá và khác bảng giá ngày đó">Giá*</th><th class="num" title="Khối lượng khớp lệnh của phiên">KL khớp</th>
        <th class="num" title="Khối ngoại mua qua khớp lệnh">Ngoại mua khớp</th><th class="num" title="Khối ngoại bán qua khớp lệnh">Ngoại bán khớp</th>
        <th class="num" title="KL khớp − ngoại mua khớp">Nội mua</th><th class="num" title="KL khớp − ngoại bán khớp">Nội bán</th>
        <th class="num" title="Khối ngoại mua − bán qua giao dịch thỏa thuận (và lô lẻ)">Thỏa thuận</th>
        <th class="num" title="Tổng khối ngoại mua − bán: khớp lệnh + thỏa thuận, như số sàn công bố">Ngoại ròng</th><th class="num">Ròng (tỷ đ)</th></tr></thead>
      <tbody>${all.slice(0, 20).map((r) => { const a = r.adjusted ? '<span title="Khối lượng quy về số cổ phiếu hiện nay (trước ngày giao dịch không hưởng quyền)">*</span>' : "";
        return r.foreign_net == null
        ? `<tr class="muted"><td>${dmy(r.date)}</td><td class="num">${fmt(r.close)}</td><td class="num">${fmt(r.volume)}${a}</td>
          <td colspan="7" class="small">không có số liệu khối ngoại (DNSE)</td></tr>`
        : `<tr><td>${dmy(r.date)}</td><td class="num">${fmt(r.close)}</td><td class="num">${fmt(r.volume)}${a}</td>
        <td class="num">${fmt(r.foreign_buy_matched)}</td><td class="num">${fmt(r.foreign_sell_matched)}</td>
        <td class="num">${fmt(r.domestic_buy)}</td><td class="num">${fmt(r.domestic_sell)}</td>
        <td class="num">${ptCell(r.foreign_net_put_through)}</td>
        <td class="num"><b class="${tone(r.foreign_net)}">${sfmt(r.foreign_net)}</b></td>
        <td class="num ${tone(r.foreign_net_value)}">${r.foreign_net_value == null ? "–" : sfmt(r.foreign_net_value / 1e9, 2)}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="sub">${esc(data.note)} Mỗi dòng: KL khớp = ngoại mua khớp + nội mua = ngoại bán khớp + nội bán; ngoại ròng =
        (ngoại mua khớp − ngoại bán khớp) + thỏa thuận. * Giá, và khối lượng các phiên trước ngày giao dịch không hưởng quyền,
        đã quy về thang hiện nay (chia / thưởng cổ phiếu).</p></div>`;
}

// ---------------------------------------------------------------- detail: position check
// The user's cost checked against the SEPA rules on the server. Facts with rule
// tags only -- no buy / sell verdict. Cost and quantity stay in this browser.
const POS_KEY = "sepa.positions.v1";
const POS_ICON = { good: ["ok", "✓"], warn: ["na", "!"], bad: ["ko", "✗"], info: ["muted", "•"] };

// Numbers typed the Vietnamese way: "68.500" and "1.000" use the dot for thousands,
// "68,5" uses the comma for decimals; "68,500" (English) and "68500" also work.
// parseFloat would read "68.500" as 68.5.
function parseVnd(text) {
  const s = String(text ?? "").trim().replace(/\s|đ|vnd/gi, "");
  if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) return Number(s.replace(/\./g, "").replace(",", "."));
  if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) return Number(s.replace(/,/g, ""));
  if (/^\d+([.,]\d+)?$/.test(s)) return Number(s.replace(",", "."));
  return NaN;
}
const num = (v) => Number(v).toLocaleString("vi-VN", { maximumFractionDigits: 2 });   // 68,5 not 68,50
const showVnd = (v) => (v > 0 ? num(v) : "");

function setupPosition(ticker) {
  const saved = store.get(POS_KEY, {})[ticker] || {};
  $("#p-cost").value = showVnd(saved.cost);
  $("#p-qty").value = showVnd(saved.qty);
  $("#p-out").innerHTML = "";
  if (saved.cost) checkPosition();
}

async function checkPosition(confirm = false) {
  const t = state.detailTicker;
  const cost = parseVnd($("#p-cost").value), qtyText = $("#p-qty").value.trim(), qty = parseVnd(qtyText);
  if (!t) return;
  if (!(cost > 0)) {
    $("#p-out").innerHTML = `<div class="card"><p class="${$("#p-cost").value.trim() ? "warn" : "muted"}">${$("#p-cost").value.trim()
      ? "Không đọc được giá vốn. Nhập theo đồng, ví dụ 68.500 hoặc 68500." : "Nhập giá vốn trung bình."}</p></div>`;
    return;
  }
  if (qtyText && !(qty > 0)) { $("#p-out").innerHTML = `<div class="card"><p class="warn">Không đọc được số lượng, ví dụ 1.000 hoặc 1000.</p></div>`; return; }
  $("#p-cost").value = showVnd(cost);
  if (qty > 0) $("#p-qty").value = showVnd(qty);
  const all = store.get(POS_KEY, {});
  all[t] = { cost, qty: qty > 0 ? qty : undefined };
  store.set(POS_KEY, all);
  try {
    const r = await get(`/api/position?ticker=${encodeURIComponent(t)}&cost=${cost}${qty > 0 ? `&qty=${qty}` : ""}${confirm ? "&confirm=1" : ""}`);
    if (r.check === "unit") {
      // The cost is far from the price: almost always a unit slip. Ask, never guess.
      const times = r.cost < r.close ? r.close / r.cost : r.cost / r.close;
      $("#p-out").innerHTML = `<div class="card unit-check"><p><b>Giá vốn ${esc(num(r.cost))} đ chênh rất xa giá hiện tại
          ${esc(fmt(r.close))} đ</b> (${r.cost < r.close ? "thấp" : "cao"} hơn khoảng ${esc(fmt(times, 0))} lần), nên có thể đã nhập nhầm đơn vị.</p>
        <p class="muted small">Ô giá vốn tính theo <b>đồng</b>: 68.500 hoặc 68500 là 68.500 đ; 68,5 là 68,5 đ.</p>
        <div class="unit-actions">${r.suggest_cost ? `<button class="btn primary" type="button" data-fix="${r.suggest_cost}">Dùng ${esc(fmt(r.suggest_cost))} đ (nhập theo nghìn đồng)</button>` : ""}
          <button class="btn" type="button" data-confirm>Giữ ${esc(num(r.cost))} đ và kiểm tra</button></div></div>`;
      return;
    }
    const c = r.counts;
    const ladder = r.levels.map((l) => {
      const cls = l.name === "Giá hiện tại" ? "now" : l.name === "Giá vốn của bạn" ? "cost" : "";
      return `<tr class="${cls}"><td>${esc(l.name)}</td><td class="num"><b>${fmt(l.price)}</b></td>
        <td class="num ${tone(l.vs_cost_pct)}">${cls === "cost" ? "–" : pct(l.vs_cost_pct)}</td>
        <td class="num ${tone(l.vs_close_pct)}">${cls === "now" ? "–" : pct(l.vs_close_pct)}</td><td class="muted">${esc(l.note || "")}</td></tr>`;
    }).join("");
    const sections = r.sections.map((s) => `<div class="card"><div class="card-h"><h3>${esc(s.title)}</h3></div>
      <div class="pos-list">${s.items.map((x) => `<div class="pos-row pos-${esc(x.level)}"><span class="${POS_ICON[x.level][0]} pos-ico">${POS_ICON[x.level][1]}</span>
        <div><div>${esc(x.text)}</div>${x.rule ? `<div class="muted small">${esc(x.rule)}</div>` : ""}</div></div>`).join("")}</div></div>`).join("");
    $("#p-out").innerHTML = `
      <div class="pos-head sit-${esc(r.situation)}"><b>${esc(r.headline)}</b>
        <span class="pos-sum"><span class="pill" style="--c:var(--up)">✓ ${c.good}</span><span class="pill" style="--c:var(--warn)">! ${c.warn}</span><span class="pill" style="--c:var(--down)">✗ ${c.bad}</span></span></div>
      <div class="card"><div class="card-h"><h3>Các mốc giá</h3><span class="muted small">xếp từ cao xuống thấp</span></div>
        <div class="table-wrap flat"><table class="ladder"><thead><tr><th>Mốc</th><th class="num">Giá</th><th class="num">So với giá vốn</th><th class="num">So với giá hiện tại</th><th>Ghi chú</th></tr></thead>
        <tbody>${ladder}</tbody></table></div></div>
      ${sections}
      <p class="muted small">${esc(r.note)}</p>`;
  } catch (err) {
    $("#p-out").innerHTML = `<div class="card"><p class="warn">${esc(err.message)}</p></div>`;
  }
}

// ---------------------------------------------------------------- detail drawer
function detailHeader(ticker, d) {
  const i = d.info || {};
  const row = state.rows.find((r) => r.ticker === ticker) || (state.market || []).find((r) => r.ticker === ticker) || {};
  const sc = d.screen, bj = (sc && sc.base_json) || {};
  const setup = (d.state && d.state.setup && d.state.setup.status) ? d.state.setup : bj.setup || {};
  $("#d-title").textContent = ticker;
  $("#d-logo").innerHTML = logoTile(ticker, " lg");
  $("#d-sub").textContent = `${i.exchange || ""}${i.name ? " · " + i.name : ""}`;
  // Group, tier and RS come from the run on screen only: d.screen may be an older run.
  const grp = row.grp;
  const badges = [];
  if (grp) badges.push(`<span class="g g-${esc(grp)}" title="${esc(GROUP_TEXT[grp])}">${grp === "C" ? "Theo dõi mở rộng" : "Danh sách chính · " + grp}</span>`);
  else badges.push('<span class="tag">Không trong danh sách lọc</span>');
  if (row.leader_of) badges.push(`<span class="tag">Đầu ngành ${esc(row.leader_of)}</span>`);
  if (grp && row.rs_tier) badges.push(`<span class="tag">${esc(TIER_LABEL[row.rs_tier] || row.rs_tier)}</span>`);
  const rs = row.rs_rating;
  if (rs != null) badges.push(`<span class="tag" title="Sức mạnh giá so với toàn thị trường">RS ${fmt(rs)}</span>`);
  if (setup.status) badges.push(stBadge(setup.status));
  if (row.ticker) badges.push(liqBadge(liqOf(row)));
  const analysisDate = (d.state || {}).trade_date, priceDate = (d.bars || []).at(-1)?.trade_date;
  if (analysisDate && priceDate && analysisDate < priceDate) badges.push(`<span class="stale">Phân tích phiên ${dmy(analysisDate)} · chờ bổ sung</span>`);
  $("#d-badges").innerHTML = badges.join("");
  const bars = d.bars || [];
  const last = bars[bars.length - 1], prev = bars[bars.length - 2];
  const c = d.change || {};
  const chg = c.pct ?? (last && prev ? (last.close / prev.close - 1) * 100 : null);
  const basis = c.basis === "ref" ? `so với tham chiếu ${fmt(c.reference)}` : "so với đóng cửa phiên trước";
  $("#d-price").innerHTML = last ? `<b>${fmt(last.close)}</b><span class="${tone(chg)}" title="${esc(basisTitle({ ...c, change_basis: c.basis, exchange: (d.info || {}).exchange }))}">${pct(chg, 2)}</span>
    <div class="muted small">phiên ${dmy(last.trade_date)} · ${basis}</div>` : "";
}

function showDetailPane(name) {
  $$(".dtab").forEach((b) => b.classList.toggle("on", b.dataset.pane === name));
  $$(".dpane").forEach((p) => p.classList.toggle("on", p.dataset.pane === name));
  if (!state.detail) return;
  // A chart can only be drawn into a visible box, so the chart pane redraws when shown.
  if (name === "chart") { if (state.chartSource === "tv") drawTradingView(state.detailTicker, state.detailExchange); else drawCurrentChart(); }
  if (name === "flow" && state.flowFor !== state.detailTicker) { state.flowFor = state.detailTicker; loadFlow(state.detailTicker); }
}

async function openDetail(ticker) {
  state.detailKind = "stock";
  state.tf = loadTf();
  $("#detail").classList.remove("index-mode");
  let d;
  try { d = await get(`/api/ticker?ticker=${encodeURIComponent(ticker)}`); }
  catch (err) { alert(err.message); return; }
  const i = d.info || {}, sc = d.screen;
  state.detail = d; state.detailExchange = i.exchange; state.detailTicker = ticker; state.flowFor = null;
  detailHeader(ticker, d);
  syncDetailStar();
  $("#detail").classList.add("open"); $("#scrim").classList.add("open");
  $("#detail").setAttribute("aria-hidden", "false");
  $("#detail").scrollTop = 0;
  $("#detail").style.setProperty("--dhead", `${$(".d-head").offsetHeight}px`);
  $("#tv-link").href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol(ticker, i.exchange))}`;
  showChart("own");
  showDetailPane("chart");
  const crit = (d.state && d.state.criteria) || (sc && sc.criteria_json);
  $("#d-criteria").innerHTML = criteriaHtml(crit) + (d.state ? `<p class="sub">TT1–TT7 theo phiên ${dmy(d.state.trade_date)}; TT8 (RS) theo lần lọc.</p>` : "")
    + rsLineHtml(d.rs_line);
  $("#d-base").innerHTML = baseHtml(d.state) + baseChecksHtml(d.base);
  $("#d-tech").innerHTML = techHtml((d.state || {}).tech);
  $("#d-questions").innerHTML = questionsHtml((d.state && d.state.setup && d.state.setup.status) ? d.state.setup : ((sc && sc.base_json) || {}).setup,
    (d.state || {}).base_kind || (d.base || {}).kind);
  const bj = (sc && sc.base_json) || {};
  $("#d-fund").innerHTML = fundHtml(d.fundamentals || bj.fundamentals, bj.demoted);
  $("#d-industry").innerHTML = industryHtml(d.industry, bj.industry && bj.industry.name ? bj.industry : null);
  renderStockNews();
  setupPosition(ticker);
}

// The VN-Index card opens the index chart in the same drawer, chart pane only.
// FTD state (congthuc.md §11): live, ended (failed / worn out by distribution), or a rally attempt in progress.
function ftdTag(ftd) {
  if (ftd.date) return `<span class="tag" title="Follow-through day: ngày xác nhận nhịp hồi">FTD ${dmy(ftd.date)}${ftd.distribution_since != null ? ` · ${ftd.distribution_since} ngày phân phối từ đó` : ""}</span>`;
  const parts = [];
  const l = ftd.last_ftd;
  if (l) parts.push(l.outcome === "failed"
    ? `<span class="tag" title="Chỉ số thủng đáy của nhịp hồi phục">FTD ${dmy(l.date)} thất bại ${dmy(l.on)}</span>`
    : `<span class="tag" title="Đủ ${l.distribution_since} ngày phân phối kể từ FTD">FTD ${dmy(l.date)} hết hiệu lực ${dmy(l.on)}</span>`);
  parts.push(ftd.state === "RALLY_ATTEMPT" && ftd.rally_start
    ? `<span class="tag" title="Ngày 1 = phiên đóng cửa tăng đầu tiên sau đáy; FTD từ ngày 4">Nỗ lực hồi phục ngày ${ftd.rally_day} (từ ${dmy(ftd.rally_start)})</span>`
    : '<span class="tag">Chưa có FTD</span>');
  return parts.join("");
}

async function openIndex() {
  let d;
  try { d = await get("/api/index"); } catch (err) { alert(err.message); return; }
  state.detail = d; state.detailKind = "index"; state.detailTicker = d.info.ticker; state.detailExchange = null; state.flowFor = null;
  state.tf = loadTf();
  const m = d.market || {}, ftd = m.ftd || {};
  $("#detail").classList.add("index-mode");
  $("#d-logo").innerHTML = '<span class="clogo mono lg idx-logo" aria-hidden="true">VNI</span>';
  $("#d-title").textContent = d.info.name;
  $("#d-sub").textContent = "Chỉ số giá cổ phiếu sàn HOSE · đơn vị: điểm";
  $("#d-badges").innerHTML = [
    `<span class="mk mk-${esc(d.market_level || "")}" title="${esc(d.market_note || "")}">${esc(MARKET_TEXT[m.status] || "–")}</span>`,
    m.distribution_days != null ? `<span class="tag" title="Phiên giảm ≥ ${fmt(d.distribution_min_drop_pct, 1)}% với khối lượng cao hơn phiên trước; còn tính trong ${m.window} phiên, trừ khi chỉ số đã tăng 5% so với giá đóng cửa ngày đó">${m.distribution_days}/${m.window} ngày phân phối</span>` : "",
    ftdTag(ftd),
    volumeSplitTag(d.session),
    d.session && d.session.advancers != null ? `<span class="tag" title="Mã tăng gồm cả mã trần; mã giảm gồm cả mã sàn">`
      + `Tăng ${d.session.advancers} (${d.session.ceilings ?? 0} trần) · Đứng ${d.session.unchanged ?? 0} · `
      + `Giảm ${d.session.decliners} (${d.session.floors ?? 0} sàn)</span>` : "",
  ].join("");
  const bars = d.bars || [], last = bars[bars.length - 1], prev = bars[bars.length - 2];
  const chg = last && prev ? (last.close / prev.close - 1) * 100 : null;
  $("#d-price").innerHTML = last ? `<b>${fmt(last.close, 2)}</b><span class="${tone(chg)}">${pct(chg, 2)}</span>
    <div class="muted small">phiên ${dmy(last.trade_date)}</div>` : "";
  $("#detail").classList.add("open"); $("#scrim").classList.add("open");
  $("#detail").setAttribute("aria-hidden", "false");
  $("#detail").scrollTop = 0;
  $("#detail").style.setProperty("--dhead", `${$(".d-head").offsetHeight}px`);
  $("#tv-link").href = "https://www.tradingview.com/chart/?symbol=HOSE%3AVNINDEX";
  showChart("own");
  showDetailPane("chart");
}

function closeDetail() {
  $("#detail").classList.remove("open"); $("#scrim").classList.remove("open");
  $("#detail").setAttribute("aria-hidden", "true");
}

function syncDetailStar() {
  const t = state.detailTicker, on = t && state.mine.has(t);
  $("#d-star").textContent = on ? "★" : "☆";
  $("#d-star").classList.toggle("on", !!on);
  $("#d-star").title = on ? "Bỏ khỏi Danh sách của tôi" : "Thêm vào Danh sách của tôi";
}

// A short popup in the corner for what just happened (add / remove / an error); it goes by itself.
function toast(html, kind = "ok", ms = 3500) {
  const box = $("#toasts");
  while (box.children.length >= 3) box.firstElementChild.remove();
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.setAttribute("role", kind === "err" ? "alert" : "status");
  t.innerHTML = html;
  box.appendChild(t);
  requestAnimationFrame(() => t.classList.add("in"));
  setTimeout(() => { t.classList.remove("in"); setTimeout(() => t.remove(), 300); }, ms);
}

async function toggleMine(ticker) {
  const on = state.mine.has(ticker);
  let res;
  try {
    res = await post("/api/mylist", { action: on ? "remove" : "add", ticker });
  } catch (err) {
    toast(`Không ${on ? "bỏ" : "thêm"} được <b>${esc(ticker)}</b>: ${esc(err.message)}`, "err", 6000);
    return false;
  }
  toast(on ? `★ Đã bỏ <b>${esc(ticker)}</b> khỏi Danh sách của tôi`
    : `★ Đã thêm <b>${esc(ticker)}</b> vào Danh sách của tôi${res && res.refreshing ? " · đang lấy kỹ thuật, dòng tiền" : ""}`,
  on ? "info" : "ok");
  if (SITE && Array.isArray(res.items)) {
    // Use the successful write response; a KV read immediately afterwards may still be cached.
    acceptMineList(res.items);
    await loadMarket();
    setCounts();
    renderMinePending();
    renderTable(true);
  } else {
    await loadStocks();
  }
  syncDetailStar();
  syncStars();
  // This machine fetches the new ticker's state, flow and news in the background (mylist-refresh).
  if (!on && res && res.refreshing) [60000, 150000].forEach((ms) => setTimeout(() => loadStocks(), ms));
  return true;
}

// ---------------------------------------------------------------- other tabs
const SECTOR_LABEL = {leading:'Đang dẫn dắt',outperforming:'Mạnh hơn thị trường',improving:'Đang cải thiện',weakening:'Đang suy yếu',mixed:'Chưa đồng thuận',insufficient:'Ít dữ liệu'};
const sectorState = {data:null,selected:null,request:0,mapRows:[],zoom:false,detail:null};
function sectorGroups(groups) {
 const byName=new Map();
 for(const g of groups){const key=fold(g.name).trim();if(!byName.has(key)||g.kind==='industry')byName.set(key,g);}
 return [...byName.values()];
}
function wireSectorDropdowns() {
 const labels={'sector-map-exchange':'Sàn','sector-map-weight':'Diện tích','sector-map-mode':'Hiển thị','sector-status':'Trạng thái','sector-sort':'Sắp xếp'};
 for(const [id,label] of Object.entries(labels)){
  const select=$('#'+id);select.hidden=true;
  select.insertAdjacentHTML('afterend',ddButton(`data-sector-select="${id}"`,label,select.selectedOptions[0].textContent,false));
 }
 document.addEventListener('click',e=>{
  const button=e.target.closest('[data-sector-select]');if(!button)return;
  const select=$('#'+button.dataset.sectorSelect);
  const items=[...select.options].map(o=>{
   let count;
   if(select.id==='sector-map-exchange'){
    const groups=sectorState.mapRows.filter(g=>!sectorState.zoom||g.id===sectorState.selected);
    count=new Set(groups.flatMap(g=>g.members).filter(r=>!o.value||r.exchange===o.value).map(r=>r.ticker)).size;
   }else if(select.id==='sector-status'){
    const q=fold($('#sector-search').value);
    count=sectorGroups((sectorState.data||{}).groups||[]).filter(g=>(!q||fold(g.name).includes(q))&&(!o.value||g.status===o.value)).length;
   }
   return {value:o.value,text:o.textContent,count};
  });
  openDropdown(button,{value:select.value,searchable:false,items,onPick:value=>{
   select.value=value;$('.dd-value',button).textContent=select.selectedOptions[0].textContent;
   button.classList.toggle('active',value!==select.options[0].value);select.dispatchEvent(new Event('change',{bubbles:true}));
  }});
 });
}
function sectorTrendChart(history) {
 const values=history.map(x=>x.relative).filter(v=>Number.isFinite(v));if(values.length<2)return '<p class="muted">Chưa đủ lịch sử để vẽ diễn biến.</p>';
 const lo=Math.min(-.1,...values),hi=Math.max(.1,...values),span=hi-lo;
 const y=v=>140-(v-lo)/span*116, x=i=>24+i/Math.max(1,history.length-1)*592;
 let pen=false;const path=history.map((r,i)=>{if(!Number.isFinite(r.relative)){pen=false;return '';}const part=`${pen?'L':'M'}${x(i).toFixed(1)},${y(r.relative).toFixed(1)}`;pen=true;return part;}).join(' ');
 return `<svg class="sector-trend-chart" viewBox="0 0 640 164" role="img" aria-label="Sức mạnh tương đối so với VN-Index trong 21 phiên"><path d="M24,${y(0)} H616" class="sector-chart-zero"/><path d="${path}" class="sector-chart-line"/></svg><div class="sector-chart-dates"><span>${dmy(history[0].date)}</span><span>Đường đứt: ngang VN-Index</span><span>${dmy(history[history.length-1].date)}</span></div>`;
}
function openSectorDrawer(id) {
 const g=sectorGroups((sectorState.data||{}).groups||[]).find(x=>x.id===id);if(!g)return;
 sectorState.detail=id;
 $('#sector-drawer-title').textContent=g.name;
 $('#sector-drawer-subtitle').textContent=`Phiên ${dmy(sectorState.data.as_of)} · ${g.members.length} cổ phiếu`;
 const m=g.metrics,valid=g.members.filter(sectorMapCurrent);
 const up=valid.filter(r=>r.change>0).length,down=valid.filter(r=>r.change<0).length,flat=valid.length-up-down;
 const segment=(n,cls)=>n?`<i class="${cls}" style="width:${n/Math.max(1,valid.length)*100}%"></i>`:'';
 $('#sector-drawer-content').innerHTML=`<div class="sector-drawer-summary"><span class="sector-badge ${g.status}">${SECTOR_LABEL[g.status]}</span><p>${esc(sectorReason(g))}</p></div>
  ${sectorLeadershipHtml(g)}
  <div class="sector-drawer-returns">${[['5','1 tuần'],['21','1 tháng'],['63','3 tháng']].map(([key,title])=>`<div><span>${title}</span><b class="${tone(m.returns[key])}">${pct(m.returns[key])}</b></div>`).join('')}<div><span>Thanh khoản tương đối</span><b>${m.liquidity==null?'–':fmt(m.liquidity,2)+'×'}</b></div></div>
  <section class="sector-drawer-chart"><div class="sector-chart-title"><div><h3>Sức mạnh so với VN-Index</h3><p>Chênh lệch hiệu suất 1 tháng, theo từng phiên · điểm %</p></div><b class="${tone(m.relative)}">${sfmt(m.relative,1)} đ.%</b></div>${sectorTrendChart(g.history)}</section>
  <section class="sector-drawer-breadth"><h3>Độ rộng trong phiên</h3><div class="sector-breadth-bar">${segment(up,'rising')}${segment(flat,'unchanged')}${segment(down,'falling')}</div><div class="sector-breadth-labels"><span class="pos">↑ ${up} tăng</span><span class="zero">${flat} đứng giá</span><span class="neg">↓ ${down} giảm</span><span>${g.members.length-valid.length} mã giá cũ / thiếu</span></div><p>${pct(m.breadth,0,false)} mã đủ điều kiện đang trên MA50 · ${m.count}/${g.members.length} mã tham gia đo sức mạnh ngành.</p></section>
  <section class="sector-drawer-members"><div class="sector-members-heading"><h3>Cổ phiếu trong ngành</h3><span class="muted">Bấm mã để mở biểu đồ</span></div><input id="sector-member-search" type="search" aria-label="Tìm cổ phiếu trong ngành" placeholder="Tìm mã hoặc doanh nghiệp…"><div class="sector-member-columns"><span>Mã / doanh nghiệp</span><span>Biến động phiên</span><span>GTGD 20P</span></div><div id="sector-drawer-members"></div></section>
  <p class="sector-drawer-note">Hiệu suất ngành là trung vị của các mã đủ thanh khoản, đủ lịch sử và có giá hợp lệ. GTGD: bình quân 20 phiên, tỷ đồng/ngày. Dữ liệu mô tả diễn biến đã xảy ra; không phải dự báo sóng sắp tới.</p>`;
 renderSectorDrawerMembers();const dialog=$('#sector-drawer');if(!dialog.open)dialog.showModal();dialog.scrollTop=0;
}
function renderSectorDrawerMembers() {
 const g=sectorGroups((sectorState.data||{}).groups||[]).find(x=>x.id===sectorState.detail);if(!g)return;
 const q=fold($('#sector-member-search').value),rows=g.members.filter(r=>!q||fold(r.ticker+' '+r.name).includes(q)).slice().sort((a,b)=>(b.value||0)-(a.value||0));
 const max=Math.max(1,...g.members.filter(sectorMapCurrent).map(r=>Math.abs(r.change)));
 $('#sector-drawer-members').innerHTML=rows.map(r=>{const valid=sectorMapCurrent(r),width=valid?Math.min(50,Math.abs(r.change)/max*50):0;
  return `<button type="button" class="sector-member-row" data-t="${esc(r.ticker)}" aria-label="Mở biểu đồ ${esc(r.ticker)}"><span><b>${esc(r.ticker)}</b><small>${esc(r.name)}</small>${!valid?'<small class="muted">Giá cũ / cần kiểm tra</small>':g.metrics.breakout_tickers?.includes(r.ticker)?'<small class="pos">Vượt đỉnh 20P · có KL xác nhận</small>':''}</span><span class="sector-member-move"><b class="${valid?tone(r.change):''}">${valid?pct(r.change,2):'–'}</b><span class="sector-move-track"><i class="${r.change<0?'falling':'rising'}" style="width:${width}%;${r.change<0?'transform:translateX(-100%)':''}"></i></span></span><span>${r.value==null?'–':fmt(r.value/1e9,1)}<small>${fmt(r.close)} đ</small></span></button>`;
 }).join('')||'<p class="empty">Không có mã phù hợp.</p>';
}
function sectorSpark(g) {
 const vs=g.history.map(x=>x.relative), ok=vs.filter(x=>x!=null);if(ok.length<2)return '–';
 const lo=Math.min(...ok),hi=Math.max(...ok), points=vs.map((v,i)=>v==null?null:`${i/(vs.length-1)*110},${30-(v-lo)/(hi-lo||1)*26}`).filter(Boolean).join(' ');
 return `<svg class="sector-spark" viewBox="0 0 110 34" aria-label="Sức mạnh tương đối 21 phiên"><polyline points="${points}"/></svg>`;
}
async function loadIndustries() {
 const request=++sectorState.request;
 $('#sector-list').innerHTML='<p class="muted">Đang đọc dữ liệu ngành…</p>';
 sectorState.data=null;sectorState.mapRows=[];$('#sector-map').innerHTML='<p class="empty">Đang đọc dữ liệu ngành…</p>';$('#sector-map-count').textContent='';$('#sector-date').textContent='';$('#sector-watch-date').textContent='';$('#sector-map-tip').hidden=true;
 try {const data=await get(`/api/industries${state.runId?`?run_id=${encodeURIComponent(state.runId)}`:''}`);if(request!==sectorState.request)return;
 sectorState.data=data.explorer||{groups:[]};$('#sector-date').textContent=$('#sector-watch-date').textContent=`Phiên ${dmy(sectorState.data.as_of)}`;
 $('#sector-method').textContent=sectorState.data.method||'Chưa có dữ liệu ngành.';renderSectors();
 }catch(e){if(request===sectorState.request){$('#sector-list').textContent=e.message;$('#sector-map').textContent=e.message;}}
}
function sectorLeadershipHtml(g) {
 const m=g.metrics,lead=g.leadership;if(!lead)return '';
 return `<section class="sector-drawer-breadth sector-leadership"><h3>Vì sao có nhãn này?</h3><p>So với 5 phiên trước: mức vượt VN-Index ở kỳ 20 phiên thay đổi ${sfmt(g.relative_change,2)} điểm %; tỷ lệ mã trên MA50 thay đổi ${sfmt(g.breadth_change,1)} điểm %.</p><div class="sector-signal-grid">${[['Vượt VN-Index · 20 phiên',m.excess?.['20']==null?'–':sfmt(m.excess['20'],2)+' đ.%'],['Vượt VN-Index · 60 phiên',m.excess?.['60']==null?'–':sfmt(m.excess['60'],2)+' đ.%'],['Phân vị RS20 / RS60',m.ranks?.['20']==null?'Chưa đủ nhóm':`${fmt(m.ranks['20'],0)} / ${fmt(m.ranks['60'],0)}`],['Trên MA20 / MA50',`${pct(m.breadth20,0,false)} / ${pct(m.breadth,0,false)}`],['Vượt đỉnh 20 / 60 phiên',`${m.breakout_count} / ${m.breakout60_count} mã`],['Top 25% RS20 gần đây',`${lead.persistence}/5 phiên`]].map(([title,value])=>`<div><span>${title}</span><b>${value}</b></div>`).join('')}</div><ul class="sector-signal-checks">${lead.checks.map(c=>`<li><span class="${c.passed?'pos':'muted'}">${c.passed?'✓ Đạt':'○ Chưa đạt'}</span> ${esc(c.label)}</li>`).join('')}</ul><p>Vượt đỉnh trong 5 phiên gần nhất, khối lượng ≥1,2× bình quân 20 phiên trước và giá hiện còn trên vùng vượt. ${m.breakout_tickers?.length?'Mã xác nhận: '+m.breakout_tickers.map(esc).join(', ')+'.':''}</p><p>Thứ hạng 0–100 so với ${m.peer_counts?.['20']||0} nhóm cùng loại đủ dữ liệu; 100 là mạnh nhất. Năm điều kiện cần cùng đạt để gắn “Đang dẫn dắt”. Lợi nhuận, EPS và câu chuyện hỗ trợ chưa được chấm trong nhãn này; đây không phải xác suất tăng giá.</p></section>`;
}
function sectorReason(g) {
 const m=g.metrics;
 if(g.status==='insufficient')return `Cần ít nhất 3 mã có dữ liệu so sánh đủ 20/60 phiên; hiện ${m.count} mã hợp lệ.`;
 return `Vượt VN-Index 20P ${sfmt(m.excess?.['20'],1)} đ.% · trên MA50 ${pct(m.breadth,0,false)} · thanh khoản ${m.liquidity==null?'–':fmt(m.liquidity,2)+'×'} · ${m.breakout_count??0} mã vượt đỉnh.`;
}
function renderSectors() {
 const groups=sectorGroups((sectorState.data||{}).groups||[]), q=fold($('#sector-search').value), status=$('#sector-status').value;
 const sort=$('#sector-sort').value;
 const value=g=>sort==='relative_change'?g.relative_change:g.metrics[sort];
 const rows=groups.filter(g=>(!q||fold(g.name).includes(q))&&(!status||g.status===status)).sort((a,b)=>Number(a.status==='insufficient')-Number(b.status==='insufficient')||(value(b)??-Infinity)-(value(a)??-Infinity)||a.name.localeCompare(b.name));
 if(!groups.some(g=>g.id===sectorState.selected)){sectorState.selected=null;sectorState.zoom=false;}
 $('#sector-list').innerHTML=rows.length?rows.map(g=>`<article class="sector-row ${g.id===sectorState.selected?'selected':''}"><button class="sector-open" data-sector="${esc(g.id)}"><span class="sector-row-head"><b>${esc(g.name)}</b><span class="sector-badge ${g.status}">${SECTOR_LABEL[g.status]}</span></span><span class="sector-row-summary">${esc(sectorReason(g))}</span><span class="sector-row-stats"><span>1 tuần <b class="${tone(g.metrics.returns['5'])}">${pct(g.metrics.returns['5'])}</b></span><span>1 tháng <b class="${tone(g.metrics.returns['21'])}">${pct(g.metrics.returns['21'])}</b></span><span>3 tháng <b class="${tone(g.metrics.returns['63'])}">${pct(g.metrics.returns['63'])}</b></span>${sectorSpark(g)}</span></button></article>`).join(''):'<p class="empty">Không có ngành phù hợp. Thử đổi bộ lọc.</p>';
 sectorState.mapRows=groups;renderSectorMap();
}
// Balanced weighted partition: every positive weight owns a disjoint rectangle.
function sectorMapRects(items,x,y,w,h) {
  const rows=items.filter(r=>Number.isFinite(r.weight)&&r.weight>0).slice().sort((a,b)=>b.weight-a.weight);
  const out=[];
  function split(rs,left,top,width,height) {
    if(!rs.length || width<=0 || height<=0)return;
    if(rs.length===1){out.push({...rs[0],x:left,y:top,w:width,h:height});return;}
    const total=rs.reduce((n,r)=>n+r.weight,0);
    let sum=rs[0].weight, cut=1;
    while(cut<rs.length-1 && Math.abs(sum+rs[cut].weight-total/2)<Math.abs(sum-total/2))sum+=rs[cut++].weight;
    const f=sum/total;
    if(width>=height){split(rs.slice(0,cut),left,top,width*f,height);split(rs.slice(cut),left+width*f,top,width*(1-f),height);}
    else{split(rs.slice(0,cut),left,top,width,height*f);split(rs.slice(cut),left,top+height*f,width,height*(1-f));}
  }
  split(rows,x,y,w,h);return out;
}
function sectorMapCurrent(r) {
  return r.date===sectorState.data.as_of && !r.flagged && Number.isFinite(r.change);
}
function sectorMapColor(change,limit=null) {
  return change==null?'#465164':limit==='ceiling'?'#6747bf':limit==='floor'?'#00a8cf':change<0?'#c93d36':change===0?'#f1ae55':'#009f66';
}
function sectorMapChange(members) {
  const valid=members.filter(sectorMapCurrent), total=valid.reduce((n,r)=>n+Math.max(0,r.value||0),0);
  return total ? valid.reduce((n,r)=>n+r.change*Math.max(0,r.value||0),0)/total : null;
}
function renderSectorMap() {
  const map=$('#sector-map'), width=map.clientWidth;if(!width)return;
  const exchange=$('#sector-map-exchange').value, equal=$('#sector-map-weight').value==='equal';
  const groups=sectorState.mapRows.filter(g=>!sectorState.zoom||g.id===sectorState.selected).map(g=>{
    const members=g.members.filter(r=>!exchange||r.exchange===exchange);
    const stocks=members.map(r=>({r,weight:equal?1:Math.max(0,r.value||0)})).filter(r=>Number.isFinite(r.weight)&&r.weight>0);
    return {g,members,stocks,weight:stocks.reduce((n,r)=>n+r.weight,0)};
  });
  const count=groups.reduce((n,g)=>n+g.stocks.length,0), unique=new Set(groups.flatMap(g=>g.stocks.map(x=>x.r.ticker))).size;
  $('#sector-map-count').textContent=`${sectorState.zoom?(sectorState.mapRows.find(g=>g.id===sectorState.selected)?.name||'')+' · ':''}${groups.filter(g=>g.weight>0).length} ngành · ${unique} mã`;
  $('#sector-map-back').hidden=!sectorState.zoom;
  const missing=groups.reduce((n,g)=>n+g.members.length-g.stocks.length,0);
  $('#sector-map-note').textContent=`${equal?'Mỗi mã có diện tích bằng nhau.':'Diện tích theo giá trị giao dịch bình quân 20 phiên, không phải vốn hóa.'} Tím / xanh lam chỉ dùng khi có giá trần / sàn của phiên để đối chiếu. Biến động theo tham chiếu khi có dữ liệu, nếu thiếu thì so đóng cửa trước. Màu ngành: trung bình biến động các mã có giá hợp lệ, gia quyền theo GTGD 20 phiên. Ô xám: thiếu giá phiên đang xem hoặc cần kiểm tra.${missing?' '+missing+' mã thiếu GTGD không có ô; chọn diện tích đều để xem.':''}${count!==unique?' Một mã có thể thuộc nhiều ngành theo cách nhóm dữ liệu.':''} Rê chuột hoặc dùng phím Tab để xem số liệu.`;
  $('#sector-map-tip').hidden=true;
  const height=map.clientHeight, mode=$('#sector-map-mode').value;
  const rectStyle=r=>`left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;`;
  const html=sectorMapRects(groups,0,0,width,height).map(box=>{
    const {g,members,stocks}=box, change=sectorMapChange(members), tiny=box.w<70||box.h<40;
    if(mode==='groups')return `<button type="button" data-map-key="${esc(g.id)}" class="sector-map-group-summary ${g.id===sectorState.selected?'selected':''}" data-map-zoom="${esc(g.id)}" data-map-group="${esc(g.id)}" style="${rectStyle(box)}background:${sectorMapColor(change)}" aria-label="Xem riêng ngành ${esc(g.name)}, ${pct(change)}"><b style="font-size:${Math.max(11,Math.min(38,box.w/11,box.h/4))}px">${tiny?'':esc(g.name)}</b>${tiny?'':`<span>${pct(change)}</span>`}</button>`;
    const header=tiny?0:Math.min(26,box.h*.23), padding=0;
    const tiles=sectorMapRects(stocks,padding,header+padding,Math.max(0,box.w-2*padding),Math.max(0,box.h-header-2*padding));
    return `<div data-map-key="${esc(g.id)}" class="sector-map-group ${g.id===sectorState.selected?'selected':''}" style="${rectStyle(box)}" role="group" aria-label="${esc(g.name)}">
      ${header?`<button type="button" data-map-key="heading" class="sector-map-group-name" data-map-zoom="${esc(g.id)}" data-map-group="${esc(g.id)}" style="height:${header}px" title="${esc(g.name)} · bấm để xem riêng ngành"><b><i aria-hidden="true">⌄</i> ${esc(g.name)}</b><span>${pct(change,2)}</span></button>`:''}
      ${tiles.map(t=>{const r=t.r, valid=sectorMapCurrent(r), label=t.w>=30&&t.h>=22, sub=t.w>=46&&t.h>=44;
        return `<button type="button" data-map-key="${esc(r.ticker)}" class="sector-map-stock" data-t="${esc(r.ticker)}" data-map-stock="${esc(r.ticker)}" data-map-parent="${esc(g.id)}" style="${rectStyle(t)}background:${sectorMapColor(valid?r.change:null,valid?r.limit:null)};font-size:${Math.max(10,Math.min(40,t.w/4,t.h/3))}px" aria-label="Mở biểu đồ ${esc(r.ticker)}, ${valid?pct(r.change,2):'Giá cũ hoặc thiếu dữ liệu'}">${label?`<b>${esc(r.ticker)}</b>`:''}${sub?`<span>${valid?pct(r.change,2):'Giá cũ / thiếu'}</span>`:''}</button>`;
      }).join('')}</div>`;
  }).join('')||'<p class="empty">Không có dữ liệu phù hợp bộ lọc. Thử chọn tất cả sàn hoặc diện tích đều theo mã.</p>';
  commitSectorMap(map,html);
}
// Reuse keyed cells so selection, keyboard focus and resize stay continuous.
function commitSectorMap(map,html) {
  const template=document.createElement('template');template.innerHTML=html;
  function sync(parent,next) {
    const old=new Map([...parent.children].map(e=>[e.dataset.mapKey,e]));
    for(const fresh of [...next.children]) {
      const key=fresh.dataset.mapKey, existing=key?old.get(key):null;
      if(!existing||existing.tagName!==fresh.tagName){parent.appendChild(fresh);continue;}
      old.delete(key);
      for(const a of [...existing.attributes])if(!fresh.hasAttribute(a.name))existing.removeAttribute(a.name);
      for(const a of [...fresh.attributes])if(existing.getAttribute(a.name)!==a.value)existing.setAttribute(a.name,a.value);
      if(fresh.classList.contains('sector-map-group'))sync(existing,fresh);
      else if(existing.innerHTML!==fresh.innerHTML)existing.innerHTML=fresh.innerHTML;
    }
    for(const element of old.values())element.remove();
  }
  sync(map,template.content);
}
function showSectorMapTip(target) {
  const id=target.dataset.mapGroup||target.dataset.mapParent, g=sectorState.mapRows.find(g=>g.id===id);if(!g)return;
  const tip=$('#sector-map-tip'), exchange=$('#sector-map-exchange').value;
  const members=g.members.filter(r=>!exchange||r.exchange===exchange), r=members.find(r=>r.ticker===target.dataset.mapStock);
  const sorted=members.slice().sort((a,b)=>(b.value||0)-(a.value||0)).slice(0,10);
  tip.innerHTML=`<div class="map-tip-heading"><b>${esc(g.name)}</b><span>${members.length} mã</span></div>${r?`<div class="map-tip-stock"><b>${esc(r.ticker)}</b> · ${esc(r.name)}</div>`:''}<table><thead><tr><th>Mã</th><th>Giá</th><th>% đổi</th><th>GTGD 20P</th></tr></thead><tbody>${(r?[r,...sorted.filter(x=>x.ticker!==r.ticker).slice(0,4)]:sorted).map(x=>`<tr class="${r&&x.ticker===r.ticker?'map-tip-active':''}"><td>${esc(x.ticker)}</td><td>${fmt(x.close)}</td><td class="${tone(sectorMapCurrent(x)?x.change:null)}">${sectorMapCurrent(x)?pct(x.change,2):'–'}</td><td>${x.value==null?'–':fmt(x.value/1e9,1)}</td></tr>`).join('')}</tbody></table><p>GTGD bình quân 20 phiên · tỷ đồng/ngày. ${r?'Bấm mã để mở biểu đồ.':'Bấm tên ngành để xem riêng ngành.'}</p>`;
  tip.hidden=false;
  const rect=target.getBoundingClientRect(), w=tip.offsetWidth,h=tip.offsetHeight;
  const left=rect.right+w+16<window.innerWidth?rect.right+12:rect.left-w-12;
  tip.style.left=`${Math.max(8,Math.min(window.innerWidth-w-8,left))}px`;
  tip.style.top=`${Math.max(8,Math.min(window.innerHeight-h-8,rect.top))}px`;
}
function wireSectors() {
 const tip=document.createElement('div');tip.id='sector-map-tip';tip.className='sector-map-tip';tip.setAttribute('role','tooltip');tip.hidden=true;document.body.appendChild(tip);
 const map=$('#sector-map');
 ['sector-map-exchange','sector-map-weight','sector-map-mode'].forEach(id=>$('#'+id).addEventListener('change',renderSectorMap));
 $('#sector-map-back').addEventListener('click',()=>{sectorState.zoom=false;sectorState.selected=null;renderSectorMap();});
 wireSectorDropdowns();
 $('#sector-drawer-close').addEventListener('click',()=>$('#sector-drawer').close());
 $('#sector-drawer').addEventListener('click',e=>{if(e.target===$('#sector-drawer')||e.target.closest('[data-t]'))$('#sector-drawer').close();});
 $('#sector-drawer').addEventListener('close',()=>{sectorState.detail=null;});
 $('#sector-drawer').addEventListener('input',e=>{if(e.target.id==='sector-member-search')renderSectorDrawerMembers();});
 let tipTimer;
 const hideTip=()=>{clearTimeout(tipTimer);tip.hidden=true;};
 map.addEventListener('pointerover',e=>{const t=e.target.closest('[data-map-stock],[data-map-group]');if(!t||t.contains(e.relatedTarget))return;clearTimeout(tipTimer);tipTimer=setTimeout(()=>{if(t.isConnected)showSectorMapTip(t);},110);});
 map.addEventListener('pointerout',e=>{const t=e.target.closest('[data-map-stock],[data-map-group]');if(t&&!t.contains(e.relatedTarget))hideTip();});
 map.addEventListener('focusin',e=>{const t=e.target.closest('[data-map-stock],[data-map-group]');if(t){clearTimeout(tipTimer);showSectorMapTip(t);}});
 ['pointerleave','focusout','click'].forEach(event=>map.addEventListener(event,hideTip));
 document.addEventListener('scroll',hideTip,true);
 let resizeFrame;
 new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{if(sectorState.data)renderSectorMap();});}).observe(map);

 ['sector-search','sector-status','sector-sort'].forEach(id=>$('#'+id).addEventListener(id==='sector-search'?'input':'change',renderSectors));
 document.addEventListener('click',e=>{
 const open=e.target.closest('[data-sector]'),zoom=e.target.closest('[data-map-zoom]');
 if(zoom){sectorState.selected=zoom.dataset.mapZoom;sectorState.zoom=true;renderSectorMap();return;}
 if(open){openSectorDrawer(open.dataset.sector);}

 });
}
wireSectors();

// News: a card opens its article (CafeF first) in a new tab; the ticker chip opens the stock.
// One search box: the ticker ("PNJ", "pn") or the company's name ("phu nhuan", accents optional).
const NEWS_PAGE_SIZE = 15;
let newsRows = [], newsPage = 1;
function newsPageData(rows, requestedPage) {
  const pages = Math.max(1, Math.ceil(rows.length / NEWS_PAGE_SIZE));
  const page = Math.min(pages, Math.max(1, Math.trunc(Number(requestedPage)) || 1));
  return { page, pages, items: rows.slice((page - 1) * NEWS_PAGE_SIZE, page * NEWS_PAGE_SIZE) };
}
function renderNewsPager(page, pages) {
  const pager = $("#news-pagination");
  pager.hidden = pages <= 1;
  if (pager.hidden) { pager.innerHTML = ""; return; }
  const candidates = [...new Set([1, page - 1, page, page + 1, pages])].filter(n => n >= 1 && n <= pages).sort((a,b) => a-b);
  const button = (n, text, label, disabled = false) => `<button type="button" class="btn" data-news-page="${n}" aria-label="${label}" ${disabled ? 'disabled' : ''} ${n === page && /^\d+$/.test(text) ? 'aria-current="page"' : ''}>${text}</button>`;
  pager.innerHTML = `<span class="news-page-status" role="status">Trang ${page} / ${pages}</span><div class="news-page-buttons">${button(page - 1, '‹', 'Trang trước', page === 1)}${candidates.map((n,i) => (i && n > candidates[i-1]+1 ? '<span class="news-page-gap" aria-hidden="true">…</span>' : '') + button(n, String(n), 'Trang '+n)).join('')}${button(page + 1, '›', 'Trang sau', page === pages)}</div>`;
}
let newsDateFilter = null, stockNewsDateFilter = null;
function renderStockNews() {
  if (!state.detail || !state.detailTicker) return;
  const rows = (state.detail.news || []).filter(n => !stockNewsDateFilter || stockNewsDateFilter.matches(n.occurred_at));
  $("#stock-news-results").innerHTML = stockNewsHtml(state.detailTicker, rows, !!stockNewsDateFilter?.value.start);
}
async function loadNews() {
  try { newsRows = await get("/api/news"); }
  catch (err) { $("#news-list").innerHTML = `<p class="empty">${esc(err.message)}</p>`; return; }
  renderNews();
}
function renderNews(reset = true) {
  if (reset) newsPage = 1;
  const q = fold($("#news-search").value.trim());
  const rows = newsRows.filter((n) => (!q || fold(n.ticker).startsWith(q) || fold(n.name || "").includes(q))
    && (!newsDateFilter || newsDateFilter.matches(n.occurred_at)));
  const page = newsPageData(rows, newsPage); newsPage = page.page;
  renderNewsPager(page.page, page.pages);
  const source = (s) => SOURCE_TEXT[s] || s || "";
  $("#news-list").innerHTML = rows.length ? page.items.map((e) => `<div class="card news"${e.url ? ` data-url="${esc(e.url)}"` : ""}>
      <div class="tk-inline">${starBtn(e.ticker)}<span class="news-tk" data-t="${esc(e.ticker)}" title="Mở chi tiết ${esc(e.ticker)}">${logoTile(e.ticker, " sm")}<span class="tk">${esc(e.ticker)}</span></span> <span class="badge">${esc(newsType(e.event_type))}</span>${mentionTag(e)}</div>
      ${e.url ? `<a class="hl" href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.headline)}</a>` : `<b>${esc(e.headline)}</b>`}
      <div class="sub">${dmy(e.occurred_at)} ${esc(String(e.occurred_at).slice(11, 16))} · ${e.article_count} bài · nguồn cấp ${e.best_source_level} · ${esc(ASSERTION_TEXT[e.assertion_type] || e.assertion_type)}${e.url ? ` · đọc trên ${esc(source(e.url_source))} ↗` : ""}</div></div>`).join("")
    : `<p class="empty">${newsRows.length ? "Không có tin phù hợp bộ lọc." : "Chưa có tin. Chạy: python -m src.main news"}</p>`;
}

// ---------------------------------------------------------------- wiring
let navigationReady = false;
function pagePath(pathname) {
  return (pathname.replace(/\/+$/, "").replace(/\.html$/, "") || "/");
}
function pageTab() {
  const path = pagePath(location.pathname);
  return $$(".side .tab[href]").find(t => t.getAttribute("href") === path) || $(".side .tab[href]");
}
function navigatePage(t, mode = "push") {
  closeDetail();
  const drawer = $("#sector-drawer"); if (drawer.open) drawer.close();
  const href = t.getAttribute("href");
  if (mode === "replace") history.replaceState(null, "", href + location.search + location.hash);
  else if (mode === "push" && location.pathname !== href) history.pushState(null, "", href);
  showTab(t, navigationReady);
  window.scrollTo({ top: 0, behavior: "auto" });
}
function showTab(t, load = true) {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
  document.title = `${t.dataset.title || "SEPA Việt Nam"} · SEPA Việt Nam`;
  $$(".side .tab[href]").forEach(x => { if (x === t) x.setAttribute("aria-current", "page"); else x.removeAttribute("aria-current"); });
  $("#page-title").textContent = t.dataset.title || "";
  $("#page-desc").textContent = t.dataset.desc || "";
  $$(".panel").forEach((p) => p.classList.remove("active"));
  $(`#tab-${t.dataset.tab}`).classList.add("active");
  if (t.dataset.group) {
    // A filter or a search belongs to the page it was set on (tester B25: "hai phong" followed to "Của tôi").
    if (state.group !== t.dataset.group) { FILTERS.forEach((f) => { state[f.key] = ""; }); state.q = ""; $("#search").value = ""; }
    state.group = t.dataset.group;
    state.grp = ""; state.sortKey = "";
    $("#mine-form").hidden = state.group !== "mine";
    if (load) { renderMinePending(); renderTable(); }
  }
  if (!load) return;
  if (["industries", "sector-watch"].includes(t.dataset.tab)) loadIndustries();
  if (t.dataset.tab === "news") loadNews();
  if (t.dataset.tab === "learn" && window.renderLearn) window.renderLearn();
}

function syncView() {
  $$(".view-seg button").forEach((b) => b.classList.toggle("on", b.dataset.view === state.view));
}

async function init() {
  // Wired before the first request: a click on the menu while the page loads used to be lost (R3-12).
  $$(".side .tab[href]").forEach(t => t.addEventListener("click", e => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault(); navigatePage(t);
  }));
  window.addEventListener("popstate", () => navigatePage(pageTab(), "none"));
  navigatePage(pageTab(), "replace");
  await detectSite();
  try { state.logos = new Set(await get("/api/logos")); } catch { /* tiles with the code instead */ }
  const runs = await get("/api/runs");
  const runText = (r) => `${dmy(r.as_of_date)} · ${r.members} mã${r === runs[0] ? " (mới nhất)" : ""}`;
  state.runId = runs[0] && runs[0].run_id;
  if (runs[0]) $("#run-text").textContent = runText(runs[0]);
  $("#run-btn").addEventListener("click", () => openDropdown($("#run-btn"), {
    items: runs.map((r, i) => ({ value: r.run_id, text: dmy(r.as_of_date), sub: `${r.members} mã${i === 0 ? " · mới nhất" : ""}` })),
    value: state.runId, searchable: false,
    onPick: (v, t) => {
      state.runId = v;
      $("#run-text").textContent = runText(runs.find((r) => r.run_id === v));
      t.focus();
      loadRun(v);
      if ($("#tab-industries").classList.contains("active") || $("#tab-sector-watch").classList.contains("active")) loadIndustries();
    },
  }));
  document.addEventListener("mousedown", (e) => { if (dd.pop && !dd.pop.contains(e.target) && !dd.trigger.contains(e.target)) closeDropdown(); });
  document.addEventListener("scroll", (e) => { if (dd.pop && !dd.pop.contains(e.target)) closeDropdown(); }, true);
  window.addEventListener("resize", () => closeDropdown());
  syncView();
  $("#page-desc").textContent = $(".tab.active").dataset.desc;

  $$(".view-seg button").forEach((b) => b.addEventListener("click", () => {
    state.view = b.dataset.view; store.set("sepa.view.v2", state.view);
    if (!COLS[state.view].some((c) => c.k === state.sortKey)) state.sortKey = "";
    syncView(); renderTable();
  }));
  $("#screen-table thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-k]");
    if (!th) return;
    // Look the column up in the table on screen: the whole-market columns (Xu hướng, Thanh khoản,
    // Cơ bản...) are not in the views', and looking there made those headers do nothing (2026-10-06).
    const k = th.dataset.k, c = shownCols().find((x) => x.k === k);
    if (!c) return;
    if (state.sortKey === k) { if (state.asc === !!c.text) state.asc = !state.asc; else state.sortKey = ""; }
    else { state.sortKey = k; state.asc = !!c.text; }
    renderTable();
  });
  $("#search").addEventListener("input", (e) => { state.q = e.target.value; renderTable(); });
  $("#filter-selects").addEventListener("click", (e) => {
    const b = e.target.closest(".dd[data-f]");
    if (!b) return;
    const f = FILTERS.find((x) => x.key === b.dataset.f);
    openDropdown(b, {
      items: filterItems(f, groupRows()), value: state[f.key],
      placeholder: `Tìm ${f.label.toLowerCase()}…`,
      onPick: (v) => { state[f.key] = v; renderTable(); $(`.dd[data-f="${f.key}"]`)?.focus(); },
    });
  });
  $("#screen-table tbody").addEventListener("click", (e) => {
    const more = e.target.closest("[data-more]");
    if (!more) return;
    e.stopPropagation();
    state.marketShown = more.dataset.more === "all" ? Infinity : state.marketShown + MARKET_PAGE;
    renderTable(true);
  });
  $("#filter-summary").addEventListener("click", (e) => {
    const b = e.target.closest("[data-clear]");
    if (!b) return;
    const k = b.dataset.clear;
    if (k === "all") { FILTERS.forEach((f) => { state[f.key] = ""; }); state.q = ""; $("#search").value = ""; }
    else if (k === "q") { state.q = ""; $("#search").value = ""; }
    else state[k] = "";
    renderTable();
  });

  // stars, rows, ticker links (also the "Xem biểu đồ" buttons in Học)
  document.addEventListener("click", (e) => {
    const star = e.target.closest("[data-star]");
    if (star) { e.stopPropagation(); toggleMine(star.dataset.star); return; }
    const row = e.target.closest("[data-t]");
    if (row && !e.target.closest("a") && !e.target.closest(".detail .dpane")) openDetail(row.dataset.t);
    // A news card opens its article, except on the ticker chip (the stock) or a link of its own.
    const card = e.target.closest(".news[data-url]");
    if (card && !row && !e.target.closest("a")) window.open(card.dataset.url, "_blank", "noopener");
  });
  $("#d-star").addEventListener("click", () => { if (state.detailTicker) toggleMine(state.detailTicker); });
  // "Danh sách của tôi": find a stock by code or company name (accents optional), add it in one click.
  const addMine = async (pick) => {
    const shown = !$("#mine-suggest").hidden && suggest.hits.length;
    const t = (pick || (shown && suggest.at >= 0 ? suggest.hits[suggest.at].ticker : $("#mine-ticker").value.trim())).toUpperCase();
    if (!t) return;
    if (state.mine.has(t)) { toast(`<b>${esc(t)}</b> đã có trong Danh sách của tôi`, "info"); return; }
    if (await toggleMine(t)) { $("#mine-ticker").value = ""; $("#mine-suggest").hidden = true; }
  };
  $("#mine-add").addEventListener("click", () => addMine());
  $("#mine-ticker").addEventListener("input", renderSuggest);
  $("#mine-ticker").addEventListener("focus", renderSuggest);
  $("#mine-ticker").addEventListener("blur", () => setTimeout(() => { $("#mine-suggest").hidden = true; }, 150));
  $("#mine-ticker").addEventListener("keydown", (e) => {
    const box = $("#mine-suggest");
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !box.hidden && suggest.hits.length) {
      e.preventDefault();
      suggest.at = (suggest.at + (e.key === "ArrowDown" ? 1 : -1) + suggest.hits.length) % suggest.hits.length;
      $$(".sg", box).forEach((el, i) => el.classList.toggle("on", i === suggest.at));
    } else if (e.key === "Enter") { e.preventDefault(); addMine(); }
    else if (e.key === "Escape") box.hidden = true;
  });
  // mousedown, not click: the input would blur and hide the list first
  $("#mine-suggest").addEventListener("mousedown", (e) => {
    const it = e.target.closest("[data-add]");
    if (!it) return;
    e.preventDefault();
    addMine(it.dataset.add);
  });
  $("#news-search").addEventListener("input", renderNews);
  $("#news-pagination").addEventListener("click", e => {
    const button = e.target.closest("[data-news-page]");
    if (!button || button.disabled || Number(button.dataset.newsPage) === newsPage) return;
    newsPage = Number(button.dataset.newsPage); renderNews(false);
    $("#tab-news .toolbar").scrollIntoView({ block: "start", behavior: "auto" });
    $(`#news-pagination [data-news-page="${newsPage}"][aria-current="page"]`)?.focus({ preventScroll: true });
  });
  newsDateFilter = NewsDates.create($("#news-date-filter"), renderNews);
  stockNewsDateFilter = NewsDates.create($("#stock-news-date-filter"), renderStockNews);

  // chart controls
  $$(".chart-tabs button").forEach((c) => c.addEventListener("click", () => {
    showChart(c.dataset.chart);
    if (c.dataset.chart === "own" && state.detail) drawCurrentChart();
    if (c.dataset.chart === "tv" && state.detailTicker) drawTradingView(state.detailTicker, state.detailExchange);
  }));
  $("#layers-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    const open = $("#layers-menu").hidden;
    $("#layers-menu").hidden = !open;
    $("#layers-btn").setAttribute("aria-expanded", String(open));
  });
  $("#layers-menu").addEventListener("click", (e) => {
    e.stopPropagation();
    if (e.target.closest("[data-layer-reset]")) {
      state.layers = { ...DEFAULT_LAYERS }; store.set(LAYERS_KEY, state.layers);
      if (state.detail) drawCurrentChart(); else renderLayersMenu();
    }
  });
  $("#layers-menu").addEventListener("change", (e) => {
    const k = e.target.dataset.layer;
    if (!k) return;
    state.layers[k] = e.target.checked; store.set(LAYERS_KEY, state.layers);
    if (state.detail) drawCurrentChart();
  });
  document.addEventListener("click", () => { $("#layers-menu").hidden = true; $("#layers-btn").setAttribute("aria-expanded", "false"); });
  renderLayersMenu();

  $$(".dtab").forEach((b) => b.addEventListener("click", () => showDetailPane(b.dataset.pane)));
  $("#p-check").addEventListener("click", () => checkPosition());
  $("#p-out").addEventListener("click", (e) => {
    const fix = e.target.closest("[data-fix]");
    if (fix) { $("#p-cost").value = showVnd(Number(fix.dataset.fix)); checkPosition(); return; }
    if (e.target.closest("[data-confirm]")) checkPosition(true);
  });
  ["#p-cost", "#p-qty"].forEach((id) => $(id).addEventListener("blur", (e) => {
    const v = parseVnd(e.target.value);
    if (v > 0) e.target.value = showVnd(v);
  }));
  ["#p-cost", "#p-qty"].forEach((id) => $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") checkPosition(); }));
  $("#p-clear").addEventListener("click", () => {
    const all = store.get(POS_KEY, {}); delete all[state.detailTicker]; store.set(POS_KEY, all);
    $("#p-cost").value = ""; $("#p-qty").value = ""; $("#p-out").innerHTML = "";
  });
  $("#d-close").addEventListener("click", closeDetail);
  $("#vni").addEventListener("click", openIndex);
  $$(".tf-seg button").forEach((b) => b.addEventListener("click", () => {
    state.tf = b.dataset.tf;
    store.set(tfKey(), state.tf);
    drawCurrentChart();
  }));
  $("#vni").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openIndex(); } });
  $("#scrim").addEventListener("click", closeDetail);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });
  await loadRun(runs[0] && runs[0].run_id);
  navigationReady = true; showTab(pageTab());
  endLoading();
  loadMarket();            // in the background: the sidebar count, and instant opening later
}

init().catch((err) => {
  clearTimeout(loadingTimer); loadingTimer = null;
  $("#page-loading").hidden = false;
  $("#page-loading").classList.add("failed");
  $("#page-loading-title").textContent = "Chưa tải được dữ liệu";
  $("#page-loading-message").textContent = err.message || "Kiểm tra kết nối mạng rồi thử lại.";
  $("#page-loading-retry").hidden = false;
});
