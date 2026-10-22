/* Vietnamese calendar shared by market news and individual stock news. */
(() => {
  "use strict";
  const vnDay = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit" });
  function dayKey(value) {
    if (!value) return "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const p = Object.fromEntries(vnDay.formatToParts(date).map(x => [x.type, x.value]));
    return `${p.year}-${p.month}-${p.day}`;
  }
  const iso = date => date.toISOString().slice(0, 10);
  const display = key => key ? key.split("-").reverse().join("/") : "Chưa chọn";
  function matches(value, range) {
    if (!range?.start && !range?.end) return true;
    const day = dayKey(value);
    return !!day && (!range.start || day >= range.start) && (!range.end || day <= range.end);
  }
  function monthCells(year, month) {
    const first = new Date(Date.UTC(year, month, 1));
    const offset = (first.getUTCDay() + 6) % 7;
    return Array.from({ length: 42 }, (_, i) => iso(new Date(Date.UTC(year, month, 1 - offset + i))));
  }
  function choose(draft, key) {
    if (draft.mode === "single") return { mode: "single", start: key, end: key };
    if (!draft.start || draft.end) return { mode: "range", start: key, end: "" };
    return { mode: "range", start: [draft.start, key].sort()[0], end: [draft.start, key].sort()[1] };
  }
  let counter = 0;
  function create(root, onChange) {
    const id = `news-calendar-${++counter}`;
    let value = { mode: "single", start: "", end: "" }, draft = { ...value };
    let month = dayKey(new Date()).slice(0, 7), focusDay = "";
    root.classList.add("news-date-filter");
    root.innerHTML = `<button type="button" class="nd-trigger" aria-haspopup="dialog" aria-expanded="false" popovertarget="${id}"><span aria-hidden="true">▦</span><span class="nd-label">Tất cả ngày</span><span aria-hidden="true">⌄</span></button><div id="${id}" class="nd-popup" popover="auto" role="dialog" aria-label="Lọc tin theo ngày"></div>`;
    const trigger = root.querySelector(".nd-trigger"), popup = root.querySelector(".nd-popup");
    function label() {
      root.querySelector(".nd-label").textContent = !value.start ? "Tất cả ngày" : value.start === value.end ? display(value.start) : `${display(value.start)} – ${display(value.end)}`;
      trigger.classList.toggle("filtered", !!value.start);
    }
    function close() { if (popup.matches(":popover-open")) popup.hidePopover(); trigger.focus({ preventScroll: true }); }
    function position() {
      const r = trigger.getBoundingClientRect(), width = Math.min(336, window.innerWidth - 24);
      popup.style.width = `${width}px`;
      popup.style.left = `${Math.max(12, Math.min(r.right - width, window.innerWidth - width - 12))}px`;
      const height = popup.getBoundingClientRect().height;
      popup.style.top = `${Math.max(12, Math.min(r.bottom + 8, window.innerHeight - height - 12))}px`;
    }
    function render() {
      const [y, m] = month.split("-").map(Number), today = dayKey(new Date());
      const cells = monthCells(y, m - 1);
      focusDay = focusDay || draft.start || today;
      if (!cells.includes(focusDay)) focusDay = `${month}-01`;
      popup.innerHTML = `<div class="nd-modes" role="group" aria-label="Kiểu chọn ngày"><button type="button" data-mode="single" aria-pressed="${draft.mode === "single"}">Một ngày</button><button type="button" data-mode="range" aria-pressed="${draft.mode === "range"}">Khoảng ngày</button></div>
        <p class="nd-selection" aria-live="polite">${draft.mode === "single" ? `Ngày: <b>${display(draft.start)}</b>` : `Từ <b>${display(draft.start)}</b> đến <b>${display(draft.end)}</b>`}</p>
        <div class="nd-month"><button type="button" data-month="-1" aria-label="Tháng trước">‹</button><strong aria-live="polite">Tháng ${m}, ${y}</strong><button type="button" data-month="1" aria-label="Tháng sau">›</button></div>
        <div class="nd-week" aria-hidden="true">${["T2", "T3", "T4", "T5", "T6", "T7", "CN"].map(d => `<span>${d}</span>`).join("")}</div>
        <div class="nd-days" role="group" aria-label="Ngày trong tháng">${cells.map(key => {
          const selected = key === draft.start || key === draft.end, between = draft.start && draft.end && key > draft.start && key < draft.end;
          return `<button type="button" data-day="${key}" class="${key.slice(0, 7) !== month ? "outside " : ""}${selected ? "selected " : ""}${between ? "between " : ""}${key === today ? "today" : ""}" aria-label="${display(key)}" aria-pressed="${selected}" ${key === today ? 'aria-current="date"' : ""} tabindex="${key === focusDay ? 0 : -1}">${Number(key.slice(-2))}</button>`;
        }).join("")}</div><p class="nd-hint">${draft.mode === "range" && draft.start && !draft.end ? "Chọn ngày kết thúc." : "Lọc theo ngày đăng tin (giờ Việt Nam)."}</p>
        <div class="nd-actions"><button type="button" data-clear>Bỏ lọc</button><button type="button" data-apply ${!draft.start || !draft.end ? "disabled" : ""}>Áp dụng</button></div>`;
    }
    popup.addEventListener("beforetoggle", e => {
      if (e.newState === "open") { draft = { ...value }; month = (value.start || dayKey(new Date())).slice(0, 7); focusDay = ""; render(); }
    });
    popup.addEventListener("toggle", e => { trigger.setAttribute("aria-expanded", String(e.newState === "open")); if (e.newState === "open") position(); });
    popup.addEventListener("click", e => {
      const button = e.target.closest("button");
      if (!button) return;
      if (button.dataset.mode) {
        draft = { mode: button.dataset.mode, start: button.dataset.mode === "single" ? draft.start : "", end: button.dataset.mode === "single" ? draft.start : "" };
        render(); popup.querySelector(`[data-mode="${draft.mode}"]`).focus();
      } else if (button.dataset.month) {
        const [y, m] = month.split("-").map(Number);
        month = iso(new Date(Date.UTC(y, m - 1 + Number(button.dataset.month), 1))).slice(0, 7);
        render(); popup.querySelector(`[data-month="${button.dataset.month}"]`).focus();
      } else if (button.dataset.day) {
        focusDay = button.dataset.day; draft = choose(draft, focusDay); render(); popup.querySelector(`[data-day="${focusDay}"]`).focus();
      } else if (button.hasAttribute("data-clear")) {
        value = { mode: draft.mode, start: "", end: "" }; label(); close(); onChange();
      } else if (button.hasAttribute("data-apply") && draft.start && draft.end) {
        value = { ...draft }; label(); close(); onChange();
      }
    });
    popup.addEventListener("keydown", e => {
      const day = e.target.dataset.day;
      if (!day) return;
      const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
      if (offsets[e.key] == null) return;
      e.preventDefault();
      const date = new Date(`${day}T00:00:00Z`); date.setUTCDate(date.getUTCDate() + offsets[e.key]);
      focusDay = iso(date); month = focusDay.slice(0, 7); render(); popup.querySelector(`[data-day="${focusDay}"]`).focus();
    });
    window.addEventListener("resize", () => { if (popup.matches(":popover-open")) position(); });
    return { get value() { return { ...value }; }, matches: timestamp => matches(timestamp, value) };
  }
  window.NewsDates = { dayKey, matches, monthCells, choose, create };
})();
