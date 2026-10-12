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

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || `${url}: ${r.status}`); }
  return r.json();
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
  ? `<span class="clogo${size}" style="--h:${hue(t)}"><img src="/logo/${esc(t)}" alt="" loading="lazy" onerror="this.parentNode.classList.add('mono');this.replaceWith('${esc(t)}')"></span>`
  : `<span class="clogo mono${size}" style="--h:${hue(t)}" aria-hidden="true">${esc(t)}</span>`);
// Above a million shares the exact count stops helping a scan: 55.332.492 -> 55,3 tr.
const vol = (v, signed = false) => (v == null ? "–" : Math.abs(v) >= 1e6 ? `${signed && v > 0 ? "+" : ""}${fmt(v / 1e6, 1)} tr` : signed ? sfmt(v) : fmt(v));
const themeBadges = (r) => (r.themes || []).map((t) =>
  `<span class="theme${t.hot ? " hot" : ""}" title="Sóng ngành ${esc(t.name)}${t.hot ? " · đang mạnh" : ""}">${esc(t.name)}</span>`).join("");

// ---------------------------------------------------------------- state
const state = {
  rows: [], run: null, group: "main", view: store.get("sepa.view.v2", "overview"),
  q: "", grp: "", status: "", trend: "", ind: "", ex: "", liq: "", sortKey: "", asc: true, mine: new Set(), logos: new Set(),
  market: null, marketAsOf: null, marketNote: "", marketShown: 0,
};
const GROUPS = {
  main: (r) => r.grp === "A" || r.grp === "B",
  ext: (r) => r.grp === "C",
  mine: (r) => r.mine,
  leaders: (r) => !!r.leader_of,
  all: () => true,
  market: () => true,
};
// "Toàn thị trường" reads the whole-market snapshot (/api/market); every other group the monitored rows.
const groupRows = () => (state.group === "market" ? state.market || [] : state.rows.filter(GROUPS[state.group]));
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

// The invariant check (python -m src.main check, src/web/checks.py): one line by the VN-Index card,
// the rows on click. Numbers still from the earlier base rules are counted apart ("đang chuyển").
async function loadChecks() {
  let c;
  try { c = await get("/api/checks"); } catch { return; }
  const el = $("#checks-line");
  if (!c || !el) return;
  el.hidden = false;
  el.className = `checks-line small ${c.violations ? "warn" : "muted"}`;
  el.innerHTML = `Kiểm tra dữ liệu ${dm(c.session)}: <b>${c.violations}</b> vi phạm${c.transition ? ` · ${c.transition} mã đang chuyển` : ""}`;
  const rows = [...c.rows.map((r) => ({ ...r, kind: "Vi phạm" })), ...c.transition_rows.map((r) => ({ ...r, kind: "Đang chuyển" })),
    ...c.info_rows.map((r) => ({ ...r, kind: "Ghi nhận" }))];
  $("#checks-pop").innerHTML = rows.length ? `<table><thead><tr><th>Mã</th><th>Loại</th><th>Quy tắc</th><th>Chi tiết</th></tr></thead><tbody>${rows.map((r) =>
    `<tr data-t="${esc(r.ticker)}"><td><span class="tk">${esc(r.ticker)}</span></td><td>${esc(r.kind)}</td><td>${esc(r.text)}</td><td class="muted">${esc(vnText(r.detail))}</td></tr>`).join("")}</tbody></table>
    <p class="sub">"Đang chuyển": pivot / stop còn tính theo cách trước ngày 11/10/2026, giữ tới hết thời gian giữ (5 phiên).</p>`
    : `<p class="muted">Không có vi phạm (${c.checked} mã theo dõi${c.market_checked ? `, quy tắc nền trên ${c.market_checked} mã toàn thị trường` : ""}).</p>`;
  // Placed inside the window from the line's position: anchored to the card it ran off the left edge (R3-10).
  el.onclick = (e) => {
    e.stopPropagation();
    const pop = $("#checks-pop"), open = pop.hidden;
    pop.hidden = !open;
    if (!open) return;
    const r = el.getBoundingClientRect(), w = Math.min(680, innerWidth - 16);
    pop.style.width = `${w}px`;
    pop.style.left = `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`;
    pop.style.top = `${r.bottom + 6}px`;
    pop.style.maxHeight = `${Math.max(160, innerHeight - r.bottom - 24)}px`;
  };
  $("#checks-pop").onclick = (e) => e.stopPropagation();
  document.addEventListener("click", () => { $("#checks-pop").hidden = true; });
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
  { key: "ind", label: "Ngành", test: (r, v) => (v.startsWith("t:") ? (r.themes || []).some((t) => t.name === v.slice(2)) : (r.industry || {}).name === v.slice(2)),
    options: (base) => {
      const by = (a, b) => a.localeCompare(b, "vi");
      const themes = [...new Set(base.flatMap((r) => (r.themes || []).map((t) => t.name)))].sort(by);
      const inds = [...new Set(base.map((r) => (r.industry || {}).name).filter(Boolean))].sort(by);
      return [{ group: "Sóng ngành", items: themes.map((n) => [`t:${n}`, n]) }, { group: "Ngành", items: inds.map((n) => [`i:${n}`, n]) }];
    } },
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
  + (r.leader_of && state.group !== "leaders" ? `<span class="tag" title="Đầu ngành ${esc(r.leader_of)}">đầu ngành</span>` : "")
  + (r.flagged ? `<span class="tag tag-flag" title="${esc(r.flag_note || "Chuỗi giá bị gắn cờ (vd chưa điều chỉnh quyền)")}. Không tính chỉ báo cho mã này.">dữ liệu cần kiểm tra</span>` : "");
const tickerCell = (r) => `<div class="tkw">${logoTile(r.ticker)}<div class="tkc"><div class="tk-line"><span class="tk">${esc(r.ticker)}</span>${srcTag(r)}${themeBadges(r)}</div>
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
// Foreign volume includes put-through deals: when it exceeds the matched volume the domestic side is unknown, not 0.
const domSide = (v, f) => (v == null || f == null || f > v ? null : v - f);
const domBuy = (r) => domSide(fl(r).volume, fl(r).buy_volume);
const domSell = (r) => domSide(fl(r).volume, fl(r).sell_volume);
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
    { k: "fb", label: "Ngoại mua", num: true, title: "Khối lượng khối ngoại mua (cổ phiếu)", cell: (r) => fmt(fl(r).buy_volume), sort: (r) => fl(r).buy_volume },
    { k: "fs", label: "Ngoại bán", num: true, cell: (r) => fmt(fl(r).sell_volume), sort: (r) => fl(r).sell_volume },
    { k: "fn", label: "Ngoại ròng", num: true, title: "Mua − bán (cổ phiếu)", cell: (r) => `<b class="${tone(fl(r).net_volume)}">${sfmt(fl(r).net_volume)}</b>`, sort: (r) => fl(r).net_volume },
    { k: "fv", label: "Ròng (tỷ đ)", num: true, cell: (r) => `<span class="${tone(fl(r).net_value)}">${fl(r).net_value == null ? "–" : sfmt(fl(r).net_value / 1e9, 2)}</span>`, sort: (r) => fl(r).net_value },
    { k: "f5", label: "Ngoại ròng 5 phiên", num: true, title: "Tổng 5 phiên giao dịch gần nhất; phiên không có số liệu khối ngoại không được cộng",
      cell: (r) => `<div class="two"><span class="${tone(fl(r).net5_volume)}">${sfmt(fl(r).net5_volume)}</span>${flowGap(fl(r))}</div>`, sort: (r) => fl(r).net5_volume },
    { k: "db", label: "Nội mua", num: true, title: "Tổng khối lượng khớp − phần khối ngoại mua", cell: (r) => fmt(domBuy(r)), sort: domBuy },
    { k: "ds", label: "Nội bán", num: true, title: "Tổng khối lượng khớp − phần khối ngoại bán", cell: (r) => fmt(domSell(r)), sort: domSell },
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
  flow: "Khối ngoại: DNSE, bảng khớp lệnh, phiên gần nhất có dữ liệu. Khối nội = tổng khối lượng khớp − phần khối ngoại (DNSE không tách cá nhân / tổ chức trong nước). Bấm một mã, tab \"Dòng tiền\" để xem 20 phiên.",
};
const starCell = (t) => `<td class="star-col"><button class="star${state.mine.has(t) ? " on" : ""}" data-star="${esc(t)}" title="${state.mine.has(t) ? "Bỏ khỏi" : "Thêm vào"} Của tôi">${state.mine.has(t) ? "★" : "☆"}</button></td>`;

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
  if (state.marketLoading || (state.market && !force)) return;
  state.marketLoading = true;
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
    state.marketLoading = false;
  }
  if (state.group === "market") renderTable();
}

function setCounts() {
  for (const [id, g] of [["n-main", "main"], ["n-ext", "ext"], ["n-mine", "mine"], ["n-leaders", "leaders"], ["n-all", "all"]]) {
    const n = state.rows.filter(GROUPS[g]).length;
    $(`#${id}`).textContent = n || "";
  }
}

async function loadStocks() {
  const runId = state.runId;
  state.rows = await get(`/api/stocks${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`);
  state.lastSession = state.rows.reduce((m, r) => (r.trade_date && r.trade_date > m ? r.trade_date : m), "");
  state.mine = new Set(state.rows.filter((r) => r.mine).map((r) => r.ticker));
  setCounts();
  renderTable(true);
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
  return `${demoted ? `<p class="warn">${esc(demoted)}</p>` : ""}
    <p>${fundBadge(f)} <b>${esc(f.grade_text || "")}</b>${f.code33 ? ' · <span class="lead">Code 33</span>' : ""}</p>
    ${(f.reasons || []).length ? `<p class="sub">${esc(f.reasons.join("; "))}</p>` : ""}
    <div class="kv col">
      <span>Lợi nhuận sau thuế công ty mẹ so với cùng kỳ (3 quý, mới nhất trước) <b>${yoy(f.profit_yoy)}</b></span>
      <span>Doanh thu thuần so với cùng kỳ <b>${yoy(f.revenue_yoy)}</b></span>
      <span>Biên lợi nhuận so với cùng kỳ <b>${f.margin_change == null ? "–" : (f.margin_change > 0 ? "+" : "") + fmt(f.margin_change, 1) + " điểm %"}</b></span></div>
    ${q ? `<table class="qtable"><thead><tr><th>Quý</th><th>Doanh thu (tỷ đ)</th><th>LNST công ty mẹ (tỷ đ)</th></tr></thead><tbody>${q}</tbody></table>` : ""}
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
// A stop breach recorded against an earlier base, whose stop is not the one shown today.
const oldStopTag = (a, stopNow) => (a.alert_type === "STOP_BREACHED" && (a.stale_stop ?? (() => {
  const m = /stop tham chiếu ([\d.,]+)/.exec(a.what_changed || "");
  return m && Number(m[1].replace(/[.,]/g, "")) !== Math.round(stopNow || 0);
})()) ? ' <span class="badge muted-badge" title="Mức stop của sự kiện này là của nền trước đó, khác stop đang hiển thị">stop của nền trước</span>' : "");
function alertText(w) {
  const m = /^\[(\d{4}-\d{2}-\d{2})\]\s*(.*)$/.exec(w || "");
  return m ? `${dmy(m[1])} · ${vnText(m[2])}` : vnText(w);
}

function newsItem(e) {
  // one link per source: several articles from the same site read "cafef · cafef"
  const seen = new Set();
  const arts = (e.articles || []).filter((a) => !seen.has(a.source_id) && seen.add(a.source_id))
    .map((a) => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(SOURCE_TEXT[a.source_id] || a.source_id)}</a>`).join(" · ");
  return `<div class="item${e.role === "mention" ? " mention" : ""}"><span class="badge">${esc(newsType(e.event_type))}</span>${mentionTag(e)}<b>${esc(e.headline)}</b>
    <div class="why">${dmy(e.occurred_at)} ${esc(String(e.occurred_at).slice(11, 16))} · ${e.article_count} bài · cấp nguồn ${e.best_source_level} · ${esc(ASSERTION_TEXT[e.assertion_type] || e.assertion_type)}${arts ? " · " + arts : ""}</div>
    ${e.what_happened ? `<div class="why">Diễn giải: ${esc(e.what_happened)} (${esc(e.direction || "?")})</div>` : ""}</div>`;
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
  drawChart({ ...d, bars: tfd.bars, ma: tfd.ma, osc: tfd.osc, ma_periods: tfd.ma_periods, tf, daily_count: d.bars.length,
    levels: { ...(d.levels || {}), ...(tfd.levels || { trendlines: {}, channel: null }) } });
}

const DEFAULT_LAYERS = { MA20: false, MA50: true, MA150: true, MA200: true, pivot: true, dist: true, zones: false, channel: false, base: false, volume: true, rsi: true, macd: false };
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
  const base = d.base || {};
  const markers = [];
  const pivot = base.pivot ?? (d.state || {}).pivot, stop = base.stop ?? (d.state || {}).stop;
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
      <div class="lg-row">${mas}${L.rsi ? `<span><i style="background:${OSC_COLORS.RSI}"></i>RSI <b>${fmt(rsiAt.get(b.trade_date), 1)}</b></span>` : ""}
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
    const buys = got.filter((r) => r.foreign_net > 0).length;
    return `<div class="stat"><span class="muted small">${label}</span><b class="${tone(v)}">${got.length ? sfmt(v) + " cp" : "–"}</b>
      <span class="small"><span class="${tone(money)}">${sfmt(money / 1e9, 2)} tỷ đ</span> · ${buys}/${got.length} phiên mua ròng${got.length < m
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
      <div class="table-wrap flat"><table class="flow-table"><thead><tr><th>Phiên</th><th class="num" title="Giá đóng cửa đã điều chỉnh theo quyền (chia/thưởng cổ phiếu), nên có thể lệch bước giá và khác bảng giá ngày đó">Giá*</th><th class="num">Tổng KL khớp</th>
        <th class="num">Ngoại mua</th><th class="num">Ngoại bán</th><th class="num">Ngoại ròng</th><th class="num">Ròng (tỷ đ)</th>
        <th class="num" title="Tổng khối lượng khớp − ngoại mua">Nội mua</th><th class="num" title="Tổng khối lượng khớp − ngoại bán">Nội bán</th></tr></thead>
      <tbody>${all.slice(0, 20).map((r) => r.foreign_net == null
        ? `<tr class="muted"><td>${dmy(r.date)}</td><td class="num">${fmt(r.close)}</td><td class="num">${fmt(r.volume)}</td>
          <td colspan="6" class="small">không có số liệu khối ngoại (DNSE)</td></tr>`
        : `<tr><td>${dmy(r.date)}</td><td class="num">${fmt(r.close)}</td><td class="num">${fmt(r.volume)}</td>
        <td class="num">${fmt(r.foreign_buy)}</td><td class="num">${fmt(r.foreign_sell)}</td>
        <td class="num"><b class="${tone(r.foreign_net)}">${sfmt(r.foreign_net)}</b></td>
        <td class="num ${tone(r.foreign_net_value)}">${r.foreign_net_value == null ? "–" : sfmt(r.foreign_net_value / 1e9, 2)}</td>
        <td class="num">${fmt(r.domestic_buy)}</td><td class="num">${fmt(r.domestic_sell)}</td></tr>`).join("")}</tbody></table></div>
      <p class="sub">${esc(data.note)} Khối nội ròng luôn bằng khối ngoại ròng đổi dấu. Khối nội để trống khi khối lượng ngoại
        (gồm cả thỏa thuận) lớn hơn khối lượng khớp. * Giá đã điều chỉnh theo quyền.</p></div>`;
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
  badges.push(themeBadges(row));
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
  $("#d-criteria").innerHTML = criteriaHtml(crit) + (d.state ? `<p class="sub">TT1–TT7 theo phiên ${dmy(d.state.trade_date)}; TT8 (RS) theo lần lọc.</p>` : "");
  $("#d-base").innerHTML = baseHtml(d.state) + baseChecksHtml(d.base);
  $("#d-tech").innerHTML = techHtml((d.state || {}).tech);
  $("#d-questions").innerHTML = questionsHtml((d.state && d.state.setup && d.state.setup.status) ? d.state.setup : ((sc && sc.base_json) || {}).setup,
    (d.state || {}).base_kind || (d.base || {}).kind);
  $("#d-alerts").innerHTML = d.alerts.length ? d.alerts.map((a) => `<div class="item">
      <span class="badge b-${esc(a.alert_type)}">${esc(EVENT_TEXT[a.alert_type] || a.alert_type)}</span>${esc(alertText(a.what_changed))}${oldStopTag(a, (d.state || {}).stop)}
      <div class="why">${esc(vnText(a.why_it_matters || ""))}</div></div>`).join("") : `<p class="muted">Chưa có sự kiện.</p>`;
  const bj = (sc && sc.base_json) || {};
  $("#d-fund").innerHTML = fundHtml(d.fundamentals || bj.fundamentals, bj.demoted);
  $("#d-industry").innerHTML = industryHtml(d.industry, bj.industry && bj.industry.name ? bj.industry : null);
  $("#d-news").innerHTML = d.news.length ? d.news.map(newsItem).join("") : `<p class="muted">Chưa có tin. Chạy: python -m src.main news</p>`;
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
  $("#d-star").title = on ? "Bỏ khỏi Của tôi" : "Thêm vào Của tôi";
}

async function toggleMine(ticker, note) {
  const on = state.mine.has(ticker);
  try {
    await post("/api/mylist", { action: on ? "remove" : "add", ticker, note });
    $("#mine-msg").innerHTML = on ? `Đã bỏ <b>${esc(ticker)}</b>.` : `Đã thêm <b>${esc(ticker)}</b> — được cập nhật từ lần chạy monitor / news tới.`;
  } catch (err) {
    $("#mine-msg").innerHTML = `<span class="warn">${esc(err.message)}</span>`;
    return false;
  }
  await loadStocks();
  syncDetailStar();
  return true;
}

// ---------------------------------------------------------------- other tabs
// Events: filtered by ticker, type and period, 50 rows at a time (tester B26: 200 rows, no filter).
const ALERT_PAGE = 50;
state.alerts = { rows: [], q: "", type: "", period: "20", shown: ALERT_PAGE };
const alertDay = (a) => ((/^\[(\d{4}-\d{2}-\d{2})\]/.exec(a.what_changed || "") || [])[1] || "");
async function loadAlerts() {
  state.alerts.rows = await get("/api/alerts?limit=1000");
  state.alerts.shown = ALERT_PAGE;
  renderAlerts();
}
function renderAlerts() {
  const A = state.alerts;
  const days = [...new Set(A.rows.map(alertDay))].sort().reverse();
  const since = A.period === "all" ? "" : days[Number(A.period) - 1] || days[days.length - 1] || "";
  const q = A.q.trim().toUpperCase();
  const rows = A.rows.filter((a) => (!q || a.ticker.includes(q)) && (!A.type || a.alert_type === A.type) && alertDay(a) >= since);
  $("#alerts-type").innerHTML = ddButton("", "Sự kiện", A.type ? EVENT_TEXT[A.type] || A.type : "Tất cả", !!A.type);
  $("#alerts-period").innerHTML = ddButton("", "Thời gian", { 5: "5 phiên", 20: "20 phiên", 60: "60 phiên", all: "Tất cả" }[A.period], A.period !== "20");
  $("#alerts-count").innerHTML = `<b>${rows.length}</b> sự kiện`;
  const tbody = $("#alerts-table tbody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty">${A.rows.length ? "Không có sự kiện phù hợp bộ lọc." : "Chưa có sự kiện. Chạy: python -m src.main monitor"}</td></tr>`;
    $("#alerts-more").hidden = true;
    return;
  }
  tbody.innerHTML = rows.slice(0, A.shown).map((a) => {
    const m = /^\[(\d{4}-\d{2}-\d{2})\]\s*(.*)$/.exec(a.what_changed || "");
    return `<tr data-t="${esc(a.ticker)}"><td class="muted">${m ? dmy(m[1]) : ""}</td><td><span class="tk-inline">${logoTile(a.ticker, " sm")}<span class="tk">${esc(a.ticker)}</span></span></td>
      <td><span class="badge b-${esc(a.alert_type)}">${esc(EVENT_TEXT[a.alert_type] || a.alert_type)}</span></td>
      <td class="wrap">${esc(vnText(m ? m[2] : a.what_changed))}${oldStopTag(a, a.current_stop)}<div class="muted small">${esc(vnText(a.why_it_matters || ""))}</div></td></tr>`;
  }).join("");
  $("#alerts-more").hidden = rows.length <= A.shown;
  $("#alerts-more").textContent = `Xem thêm (${Math.min(ALERT_PAGE, rows.length - A.shown)} / còn ${rows.length - A.shown})`;
}

async function loadIndustries() {
  const runId = state.runId;
  const data = await get(`/api/industries${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`);
  const themes = data.themes || [];
  const tks = (list) => (list || []).map((x) => `<span class="tk link" data-t="${esc(x)}">${esc(x)}</span>`).join(" ") || "–";
  $("#themes-table tbody").innerHTML = themes.length ? themes.map((t) => `<tr>
      <td class="num"><b class="${t.hot ? "lead" : ""}">${t.rs_percentile == null ? "–" : fmt(t.rs_percentile)}</b></td>
      <td><span class="theme${t.hot ? " hot" : ""}">${esc(t.name)}</span>${t.hot ? ' <span class="muted">đang mạnh</span>' : ""}</td>
      <td class="num">${t.count} / ${t.configured}</td>
      <td class="num ${tone(t.return_1m)}">${pct(t.return_1m)}</td><td class="num ${tone(t.return_3m)}">${pct(t.return_3m)}</td>
      <td class="num">${pct(t.above_ma50_pct, 0, false)}</td><td>${tks(t.leaders)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="empty">Chưa có sóng ngành nào trong lượt lọc này.</td></tr>`;
  const rows = data.industries || [];
  const tbody = $("#industries-table tbody");
  if (!rows.length) { tbody.innerHTML = `<tr><td colspan="6" class="empty">Chưa có phân ngành trong lượt lọc này. Chạy: python -m src.main industry, rồi screen.</td></tr>`; return; }
  tbody.innerHTML = rows.map((i) => `<tr>
      <td class="num"><b class="${i.leading ? "lead" : ""}">${i.rs_percentile == null ? "–" : fmt(i.rs_percentile)}</b></td>
      <td>${esc(i.name)}</td><td class="num">${i.count}</td>
      <td class="num">${pct(i.above_ma50_pct, 0, false)}</td><td class="num">${pct(i.above_ma200_pct, 0, false)}</td><td>${tks(i.leaders)}</td></tr>`).join("");
}

let newsRows = [];
async function loadNews() { newsRows = await get("/api/news?days=30"); renderNews(); }
function renderNews() {
  const q = $("#news-search").value.trim().toUpperCase();
  const rows = newsRows.filter((n) => !q || n.ticker.includes(q));
  $("#news-list").innerHTML = rows.length ? rows.map((e) => `<div class="card news" data-t="${esc(e.ticker)}">
      <div class="tk-inline">${logoTile(e.ticker, " sm")}<span class="tk">${esc(e.ticker)}</span> <span class="badge">${esc(newsType(e.event_type))}</span>${mentionTag(e)}</div><b>${esc(e.headline)}</b>
      <div class="sub">${dmy(e.occurred_at)} ${esc(String(e.occurred_at).slice(11, 16))} · ${e.article_count} bài · nguồn cấp ${e.best_source_level} · ${esc(ASSERTION_TEXT[e.assertion_type] || e.assertion_type)}</div></div>`).join("")
    : `<p class="empty">Chưa có tin. Chạy: python -m src.main news</p>`;
}

// ---------------------------------------------------------------- wiring
function showTab(t) {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
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
    $("#mine-msg").textContent = "";
    renderTable();
  }
  if (t.dataset.tab === "alerts") loadAlerts();
  if (t.dataset.tab === "industries") loadIndustries();
  if (t.dataset.tab === "news") loadNews();
  if (t.dataset.tab === "learn" && window.renderLearn) window.renderLearn();
}

function syncView() {
  $$(".view-seg button").forEach((b) => b.classList.toggle("on", b.dataset.view === state.view));
}

async function init() {
  // Wired before the first request: a click on the menu while the page loads used to be lost (R3-12).
  $$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t)));
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
      if ($("#tab-industries").classList.contains("active")) loadIndustries();
    },
  }));
  document.addEventListener("mousedown", (e) => { if (dd.pop && !dd.pop.contains(e.target) && !dd.trigger.contains(e.target)) closeDropdown(); });
  document.addEventListener("scroll", (e) => { if (dd.pop && !dd.pop.contains(e.target)) closeDropdown(); }, true);
  window.addEventListener("resize", () => closeDropdown());
  syncView();
  $("#page-desc").textContent = $(".tab.active").dataset.desc;

  $("#alerts-search").addEventListener("input", (e) => { state.alerts.q = e.target.value; state.alerts.shown = ALERT_PAGE; renderAlerts(); });
  $("#alerts-more").addEventListener("click", () => { state.alerts.shown += ALERT_PAGE; renderAlerts(); });
  $("#alerts-type").addEventListener("click", (e) => {
    const b = e.target.closest(".dd");
    if (!b) return;
    const types = [...new Set(state.alerts.rows.map((a) => a.alert_type))];
    openDropdown(b, { items: [{ value: "", text: "Tất cả" }, ...types.map((v) => ({ value: v, text: EVENT_TEXT[v] || v,
      count: state.alerts.rows.filter((a) => a.alert_type === v).length }))], value: state.alerts.type,
    onPick: (v) => { state.alerts.type = v; state.alerts.shown = ALERT_PAGE; renderAlerts(); } });
  });
  $("#alerts-period").addEventListener("click", (e) => {
    const b = e.target.closest(".dd");
    if (!b) return;
    openDropdown(b, { items: [["5", "5 phiên gần nhất"], ["20", "20 phiên gần nhất"], ["60", "60 phiên gần nhất"], ["all", "Tất cả"]]
      .map(([value, text]) => ({ value, text })), value: state.alerts.period, searchable: false,
    onPick: (v) => { state.alerts.period = v; state.alerts.shown = ALERT_PAGE; renderAlerts(); } });
  });
  loadChecks();
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
  });
  $("#d-star").addEventListener("click", () => { if (state.detailTicker) toggleMine(state.detailTicker); });
  const addMine = async () => {
    const t = $("#mine-ticker").value.trim().toUpperCase();
    if (!t) return;
    if (state.mine.has(t)) { $("#mine-msg").innerHTML = `<b>${esc(t)}</b> đã có trong Của tôi.`; return; }
    if (await toggleMine(t, $("#mine-note").value.trim())) { $("#mine-ticker").value = ""; $("#mine-note").value = ""; }
  };
  $("#mine-add").addEventListener("click", addMine);
  ["#mine-ticker", "#mine-note"].forEach((id) => $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") addMine(); }));
  $("#news-search").addEventListener("input", renderNews);

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
  loadMarket();            // in the background: the sidebar count, and instant opening later
}

init().catch((err) => {
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="empty">Lỗi tải dữ liệu: ${esc(err.message)}</p>`);
});
