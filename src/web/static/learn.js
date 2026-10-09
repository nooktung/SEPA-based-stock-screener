"use strict";
// "Học": candlestick patterns and chart patterns, each drawn as an SVG from a
// small data table. Drawings are schematic (not to scale). Bulkowski figures are
// the same US-market statistics src/quant/candles.py quotes; untested in Vietnam.

(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------------------------------------------------------- drawing
  // Candles: [open, high, low, close] in any price units; the drawing scales itself.
  // `ctx` candles (the trend before) are drawn faded, `pat` candles are the pattern.
  function candleSvg({ ctx = [], pat = [], lines = [] }) {
    const all = [...ctx, ...pat];
    const W = Math.max(150, all.length * 26 + 24), H = 124, pad = 10;
    const lo = Math.min(...all.map((c) => c[2])), hi = Math.max(...all.map((c) => c[1]));
    const y = (v) => pad + (hi - v) / ((hi - lo) || 1) * (H - 2 * pad);
    const x0 = (W - all.length * 26) / 2 + 13;
    const parts = all.map((c, i) => {
      const [o, h, l, cl] = c, x = x0 + i * 26;
      const up = cl > o, flat = Math.abs(cl - o) < (hi - lo) * 0.012;
      const cls = `${flat ? "cf" : up ? "cu" : "cd"}${i < ctx.length ? " ctx" : ""}`;
      const top = y(Math.max(o, cl)), bot = y(Math.min(o, cl));
      return `<g class="${cls}"><line x1="${x}" x2="${x}" y1="${y(h)}" y2="${y(l)}"/>`
        + `<rect x="${x - 7}" y="${flat ? top - 1 : top}" width="14" height="${flat ? 2 : Math.max(2, bot - top)}" rx="1.5"/></g>`;
    });
    const ann = lines.map((ln) => `<g class="ann"><line x1="${x0 - 13}" x2="${W - 6}" y1="${y(ln.v)}" y2="${y(ln.v)}"/>`
      + `<text x="${W - 8}" y="${y(ln.v) - 4}" text-anchor="end">${esc(ln.label)}</text></g>`).join("");
    return `<svg class="lsvg" viewBox="0 0 ${W} ${H}" role="img" aria-hidden="true">${ann}${parts.join("")}</svg>`;
  }

  // Chart patterns: points [x, y] on a 0..100 grid (y = 100 is the top), with
  // guide lines, labels and an optional volume strip underneath.
  function chartSvg({ pts, lines = [], labels = [], vol = null }) {
    const W = 300, H = vol ? 168 : 140, top = 12, ph = 112, l = 8, w = W - 16;
    const X = (x) => l + x / 100 * w, Y = (v) => top + (100 - v) / 100 * ph;
    const path = pts.map(([x, v], i) => `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
    const gl = lines.map((g) => `<g class="ann ${esc(g.cls || "")}"><line x1="${X(g.from[0])}" y1="${Y(g.from[1])}" x2="${X(g.to[0])}" y2="${Y(g.to[1])}"/>`
      + (g.label ? `<text x="${X(g.to[0]) - 2}" y="${Y(g.to[1]) - 4}" text-anchor="end">${esc(g.label)}</text>` : "") + "</g>").join("");
    const lb = labels.map((t) => `<text class="lbl" x="${X(t.at[0])}" y="${Y(t.at[1]) + (t.below ? 13 : -6)}" text-anchor="middle">${esc(t.text)}</text>`).join("");
    const vb = vol ? vol.map((v, i) => {
      const bw = w / vol.length;
      return `<rect class="vb${v > 0.75 ? " hi" : ""}" x="${l + i * bw + 1}" y="${H - 4 - v * 22}" width="${Math.max(1, bw - 2)}" height="${v * 22}"/>`;
    }).join("") : "";
    return `<svg class="lsvg wide" viewBox="0 0 ${W} ${H}" role="img" aria-hidden="true">${gl}<path class="price" d="${path}"/>${lb}${vb}</svg>`;
  }

  // ---------------------------------------------------------------- candles
  const DOWN = [[92, 94, 84, 86], [87, 88, 77, 79], [80, 81, 70, 72]];
  const UP = [[18, 26, 16, 24], [24, 33, 23, 31], [31, 40, 30, 38]];

  const CANDLES = [
    { group: "Nến đơn" },
    { name: "Nến thân dài (Marubozu)", en: "Marubozu", kind: "Sức mạnh", svg: { pat: [[20, 80, 20, 80], [80, 80, 20, 20]] },
      how: "Thân rất dài, gần như không có bóng: mở cửa ở một đầu, đóng cửa ở đầu kia của phiên.",
      means: "Bên mua (nến xanh) hoặc bên bán (nến đỏ) kiểm soát trọn phiên. Không phải mẫu đảo chiều; nói lên lực của phiên đó.",
      confirm: "Có giá trị hơn khi khối lượng lớn và xuất hiện lúc phá nền / phá hỗ trợ." },
    { name: "Con quay", en: "Spinning top", kind: "Lưỡng lự", svg: { pat: [[46, 72, 26, 54], [55, 74, 28, 47]] },
      how: "Thân nhỏ ở giữa, bóng trên và bóng dưới đều dài.",
      means: "Hai bên giằng co, chưa bên nào thắng. Sau một đợt tăng / giảm dài, đó là dấu hiệu lực đẩy đang yếu đi.",
      confirm: "Bản thân con quay không báo hướng — chờ phiên sau." },
    { name: "Doji", en: "Doji", kind: "Lưỡng lự", detect: true, svg: { pat: [[50, 74, 26, 50.3]] },
      how: "Giá mở cửa và đóng cửa gần như bằng nhau (thân chỉ là một vạch), bóng hai đầu.",
      means: "Lưỡng lự hoàn toàn. Đáng chú ý khi xuất hiện sau một xu hướng dài hoặc ngay tại kháng cự / hỗ trợ.",
      confirm: "Cần phiên sau xác nhận hướng. Ở Việt Nam, phiên kịch trần / kịch sàn (giá đứng yên) không tính là doji." },
    { name: "Doji chuồn chuồn", en: "Dragonfly doji", kind: "Lưỡng lự", detect: true, stat: "đảo chiều 50% / hạng 98",
      svg: { ctx: DOWN, pat: [[66, 66.4, 44, 66]] },
      how: "Doji có bóng dưới rất dài, gần như không có bóng trên: giá bị đạp sâu rồi kéo về đúng giá mở cửa.",
      means: "Bên mua đã đẩy lùi được đợt bán trong phiên. Sau xu hướng giảm có thể là tín hiệu tạo đáy sớm.",
      confirm: "Thống kê cho thấy chỉ khoảng một nửa trường hợp đảo chiều — không dùng riêng." },
    { name: "Doji bia mộ", en: "Gravestone doji", kind: "Lưỡng lự", detect: true, stat: "đảo chiều giảm 51% / hạng 77",
      svg: { ctx: UP, pat: [[42, 64, 41.6, 42]] },
      how: "Doji có bóng trên rất dài, gần như không có bóng dưới: giá được kéo cao rồi bị bán về giá mở cửa.",
      means: "Bên mua thất bại ở vùng giá cao. Sau xu hướng tăng là cảnh báo lực mua cạn.",
      confirm: "Chờ phiên sau đóng cửa thấp hơn." },
    { name: "Búa", en: "Hammer", kind: "Đảo chiều tăng", detect: true, stat: "đảo chiều tăng 60% / hạng 65",
      svg: { ctx: DOWN, pat: [[64, 70, 48, 69]] },
      how: "Xuất hiện sau xu hướng giảm. Thân nhỏ nằm ở phần trên, bóng dưới dài ít nhất gấp đôi thân, bóng trên rất ngắn.",
      means: "Giá bị bán sâu trong phiên nhưng bên mua kéo lên gần đỉnh — dấu hiệu bên bán đuối sức.",
      confirm: "Phiên sau tăng và đóng cửa trên thân búa; khối lượng lớn làm tín hiệu đáng tin hơn." },
    { name: "Người treo cổ", en: "Hanging man", kind: "Cảnh báo giảm", detect: true, stat: "thực tế tiếp diễn tăng 59% / hạng 87",
      svg: { ctx: UP, pat: [[40, 41.5, 26, 39]] },
      how: "Cùng hình dạng với búa nhưng xuất hiện sau xu hướng tăng, ở vùng giá cao.",
      means: "Trong phiên đã có lực bán mạnh — một lời cảnh báo. Nhưng thống kê cho thấy giá thường vẫn đi tiếp lên.",
      confirm: "Chỉ coi trọng khi phiên sau giảm rõ và đóng cửa dưới thân nến." },
    { name: "Búa ngược", en: "Inverted hammer", kind: "Đảo chiều tăng (yếu)", detect: true, stat: "thực tế tiếp diễn giảm 65% / hạng 6",
      svg: { ctx: DOWN, pat: [[66, 84, 65.5, 69]] },
      how: "Sau xu hướng giảm. Thân nhỏ ở phần dưới, bóng trên dài, gần như không có bóng dưới.",
      means: "Bên mua thử kéo giá lên nhưng chưa giữ được. Thống kê cho thấy giá thường vẫn tiếp tục giảm.",
      confirm: "Cần phiên sau tăng mạnh vượt bóng trên mới đáng chú ý." },
    { name: "Sao băng", en: "Shooting star", kind: "Đảo chiều giảm", detect: true, stat: "đảo chiều giảm 59% / hạng 55",
      svg: { ctx: UP, pat: [[41, 60, 38.5, 39]] },
      how: "Sau xu hướng tăng. Thân nhỏ ở phần dưới, bóng trên dài ít nhất gấp đôi thân.",
      means: "Giá được đẩy lên cao rồi bị bán mạnh về gần giá mở cửa — bên mua hụt hơi ở vùng cao.",
      confirm: "Phiên sau giảm, đóng cửa dưới thân sao băng." },

    { group: "Cụm nến đảo chiều tăng (sau xu hướng giảm)" },
    { name: "Nhấn chìm tăng", en: "Bullish engulfing", kind: "Đảo chiều tăng", detect: true, stat: "đảo chiều 63% / hạng 84",
      svg: { ctx: DOWN.slice(1), pat: [[70, 71, 64, 65], [63, 77, 62, 75]] },
      how: "Nến đỏ nhỏ, rồi nến xanh có thân bao trùm hoàn toàn thân nến đỏ trước đó.",
      means: "Bên mua lật ngược thế trận chỉ trong một phiên.",
      confirm: "Mạnh hơn khi khối lượng phiên xanh lớn và xuất hiện tại hỗ trợ." },
    { name: "Harami tăng", en: "Bullish harami", kind: "Đảo chiều tăng (yếu)", detect: true, stat: "đảo chiều 53% / hạng 38",
      svg: { ctx: DOWN.slice(1), pat: [[74, 75, 58, 59], [62, 67, 61, 66]] },
      how: "Nến đỏ dài, rồi nến xanh nhỏ nằm gọn trong thân nến đỏ (như bà bầu — 'harami' tiếng Nhật).",
      means: "Lực bán chững lại. Tín hiệu yếu, gần như ngang xác suất.",
      confirm: "Cần phiên sau tăng vượt đỉnh nến đỏ." },
    { name: "Đường xuyên", en: "Piercing line", kind: "Đảo chiều tăng", detect: true, stat: "đảo chiều 64% / hạng 13",
      svg: { ctx: DOWN.slice(1), pat: [[74, 75, 60, 61], [58, 71, 57, 70]], lines: [{ v: 67.5, label: "giữa thân" }] },
      how: "Nến đỏ dài, rồi nến xanh mở cửa thấp hơn nhưng đóng cửa vượt quá nửa thân nến đỏ.",
      means: "Bên mua lấy lại hơn một nửa phần mất của phiên trước.",
      confirm: "Đóng cửa càng sâu vào thân nến đỏ càng tốt; phiên sau giữ được giá." },
    { name: "Sao mai", en: "Morning star", kind: "Đảo chiều tăng", detect: true, stat: "đảo chiều 78% / hạng 12",
      svg: { ctx: DOWN.slice(1), pat: [[74, 75, 60, 61], [57, 60, 54, 58], [61, 74, 60, 72]] },
      how: "Ba nến: nến đỏ dài → nến thân nhỏ thấp hơn (ngôi sao) → nến xanh dài đóng cửa vượt nửa thân nến đỏ đầu tiên.",
      means: "Giảm → lưỡng lự → bên mua thắng. Một trong những mẫu đảo chiều tăng đáng tin nhất.",
      confirm: "Khối lượng tăng ở nến thứ ba." },
    { name: "Ba chàng lính trắng", en: "Three white soldiers", kind: "Đảo chiều tăng", detect: true, stat: "đảo chiều 82% / hạng 32 — xu hướng sau thường ngắn",
      svg: { ctx: DOWN.slice(1), pat: [[71, 79, 70, 78], [76, 85, 75, 84], [82, 92, 81, 91]] },
      how: "Ba nến xanh dài liên tiếp, mỗi nến mở cửa trong thân nến trước và đóng cửa cao hơn.",
      means: "Bên mua kiểm soát liên tục nhiều phiên.",
      confirm: "Nếu đã tăng quá xa, mẫu này dễ dẫn tới nhịp nghỉ ngắn." },

    { group: "Cụm nến đảo chiều giảm (sau xu hướng tăng)" },
    { name: "Nhấn chìm giảm", en: "Bearish engulfing", kind: "Đảo chiều giảm", detect: true, stat: "đảo chiều 79% / hạng 91 — xu hướng sau ngắn",
      svg: { ctx: UP.slice(1), pat: [[38, 44, 37, 43], [45, 46, 32, 34]] },
      how: "Nến xanh nhỏ, rồi nến đỏ có thân bao trùm hoàn toàn thân nến xanh trước.",
      means: "Bên bán lật ngược thế trận trong một phiên ở vùng giá cao.",
      confirm: "Đáng chú ý hơn khi khối lượng lớn và ngay tại kháng cự." },
    { name: "Harami giảm", en: "Bearish harami", kind: "Cảnh báo giảm (yếu)", detect: true, stat: "thực tế tiếp diễn tăng 53% / hạng 72",
      svg: { ctx: UP.slice(1), pat: [[30, 47, 29, 46], [42, 43, 37, 38]] },
      how: "Nến xanh dài, rồi nến đỏ nhỏ nằm gọn trong thân nến xanh.",
      means: "Lực mua chững lại, nhưng thống kê cho thấy giá thường vẫn tăng tiếp.",
      confirm: "Chỉ coi trọng khi phiên sau giảm thủng đáy nến xanh." },
    { name: "Mây đen che phủ", en: "Dark cloud cover", kind: "Đảo chiều giảm", detect: true, stat: "đảo chiều 60% / hạng 22",
      svg: { ctx: UP.slice(1), pat: [[32, 47, 31, 46], [49, 50, 36, 37]], lines: [{ v: 39, label: "giữa thân" }] },
      how: "Nến xanh dài, rồi nến đỏ mở cửa cao hơn đỉnh nhưng đóng cửa dưới nửa thân nến xanh.",
      means: "Bên bán xóa hơn một nửa thành quả của phiên trước.",
      confirm: "Phiên sau tiếp tục giảm." },
    { name: "Sao hôm", en: "Evening star", kind: "Đảo chiều giảm", detect: true, stat: "đảo chiều 72% / hạng 4",
      svg: { ctx: UP.slice(1), pat: [[30, 45, 29, 44], [47, 50, 45, 46], [44, 45, 31, 33]] },
      how: "Ba nến: nến xanh dài → nến thân nhỏ cao hơn (ngôi sao) → nến đỏ dài đóng cửa dưới nửa thân nến xanh đầu tiên.",
      means: "Tăng → lưỡng lự → bên bán thắng. Mẫu đảo chiều giảm có thứ hạng cao.",
      confirm: "Khối lượng lớn ở nến thứ ba." },
    { name: "Ba con quạ đen", en: "Three black crows", kind: "Đảo chiều giảm", detect: true, stat: "đảo chiều 78% / hạng 3",
      svg: { ctx: UP.slice(1), pat: [[40, 41, 32, 33], [35, 36, 26, 27], [29, 30, 20, 21]] },
      how: "Ba nến đỏ dài liên tiếp, mỗi nến mở cửa trong thân nến trước và đóng cửa thấp hơn.",
      means: "Bên bán kiểm soát liên tục nhiều phiên — thường là dấu hiệu xu hướng đã đổi.",
      confirm: "Ở Việt Nam, chú ý rủi ro giảm sàn liên tiếp khi thanh khoản thấp." },
  ];

  // ---------------------------------------------------------------- chart patterns
  const CHARTS = [
    { group: "Nền giá trong xu hướng tăng (SEPA dùng để tìm điểm mua)" },
    { name: "Cốc tay cầm", en: "Cup with handle", kind: "Tiếp diễn tăng", detect: true,
      svg: { pts: [[0, 32], [6, 60], [12, 90], [17, 80], [22, 69], [28, 62], [35, 60], [42, 61], [48, 66], [54, 76], [60, 86], [64, 88],
        [67, 84], [70, 86], [73, 82], [76, 84], [79, 83], [84, 93], [90, 99]],
        lines: [{ from: [60, 88.6], to: [96, 88.6], cls: "pivot", label: "Pivot" }],
        labels: [{ at: [12, 90], text: "Miệng trái" }, { at: [35, 60], text: "Đáy chữ U", below: true }, { at: [73, 82], text: "Tay cầm", below: true }],
        vol: [.6, .7, .8, .5, .5, .4, .4, .3, .3, .35, .4, .45, .3, .25, .2, .2, .95, .8] },
      how: "Sau đợt tăng ≥ ~30%, giá điều chỉnh 12–33% thành đáy tròn chữ U (không phải chữ V), kéo dài 7–65 tuần. Khi về gần đỉnh cũ, giá nghỉ thêm một nhịp nhỏ dốc xuống — tay cầm — sâu ≤ 8–12%, ở nửa trên của cốc.",
      means: "Đáy U cho thấy người bán yếu dần; tay cầm rũ bỏ những người mua muộn. Pivot là đỉnh tay cầm cộng một bước giá.",
      confirm: "Breakout qua pivot với khối lượng cao hơn trung bình ≥ 40–50%. Tay cầm 'nêm lên', nằm ở nửa dưới cốc hoặc đáy chữ V là dấu hiệu lỗi [SÁCH O'Neil]." },
    { name: "VCP — co hẹp biến động", en: "Volatility Contraction Pattern", kind: "Tiếp diễn tăng", detect: true,
      svg: { pts: [[0, 35], [10, 90], [18, 66], [26, 86], [33, 73], [40, 84], [46, 77], [52, 83], [57, 80], [62, 84], [68, 95], [74, 100]],
        lines: [{ from: [38, 84.6], to: [80, 84.6], cls: "pivot", label: "Pivot" }],
        labels: [{ at: [18, 66], text: "−27%", below: true }, { at: [33, 73], text: "−15%", below: true }, { at: [46, 77], text: "−8%", below: true }, { at: [57, 80], text: "−4%", below: true }],
        vol: [.6, .7, .55, .5, .45, .4, .35, .3, .25, .2, .15, .9, .8] },
      how: "Trong nền, mỗi nhịp điều chỉnh nông hơn nhịp trước (ví dụ 27% → 15% → 8% → 4%), thường 2–6 nhịp. Khối lượng cạn dần, giá đóng cửa sát nhau ở cuối nền.",
      means: "Nguồn cung (người muốn bán) bị hấp thụ dần; khi không còn ai bán, một lượng mua nhỏ cũng đẩy giá lên. Đây là mẫu cốt lõi của Minervini.",
      confirm: "Mua khi vượt đỉnh nhịp co hẹp cuối (pivot) với khối lượng tăng; stop đặt dưới đáy nhịp cuối [SÁCH Minervini]." },
    { name: "Nền phẳng", en: "Flat base", kind: "Tiếp diễn tăng", detect: true,
      svg: { pts: [[0, 30], [8, 58], [15, 86], [20, 79], [26, 86], [33, 80], [40, 85], [47, 79], [54, 84], [60, 81], [66, 86], [72, 96], [78, 100]],
        lines: [{ from: [15, 86.6], to: [84, 86.6], cls: "pivot", label: "Pivot" }, { from: [15, 78.5], to: [70, 78.5], cls: "guide", label: "biên dưới" }],
        labels: [{ at: [42, 78.5], text: "sâu ≤ 15%, ≥ 5 tuần", below: true }],
        vol: [.6, .7, .8, .4, .35, .3, .3, .25, .25, .2, .2, .85, .7] },
      how: "Thường là nền thứ hai, ngay sau khi cổ phiếu đã tăng ≥ 20% từ nền trước. Giá đi ngang trong biên hẹp, sâu không quá ~15%, ít nhất 5 tuần.",
      means: "Cổ phiếu mạnh nghỉ ngơi mà không bị bán tháo — người nắm giữ không muốn bán.",
      confirm: "Breakout qua đỉnh nền với khối lượng ≥ 40% trên trung bình 50 phiên [TRANG GỐC IBD]." },
    { name: "Cờ cao hẹp", en: "High tight flag / Power play", kind: "Tiếp diễn tăng",
      svg: { pts: [[0, 18], [5, 22], [10, 34], [15, 52], [20, 72], [25, 90], [30, 84], [35, 88], [40, 81], [45, 85], [50, 79], [55, 83], [62, 98], [68, 100]],
        lines: [{ from: [25, 90.6], to: [72, 90.6], cls: "pivot", label: "Pivot" }],
        labels: [{ at: [12, 40], text: "Cột cờ +100%" }, { at: [42, 79], text: "Cờ ≤ 20–25%", below: true }],
        vol: [.3, .5, .7, .9, 1, .8, .4, .35, .3, .25, .2, .2, .9, .7] },
      how: "Giá tăng ≥ 100% trong ≤ 8 tuần trên khối lượng lớn (cột cờ), rồi đi ngang chặt 3–6 tuần, điều chỉnh không quá 20–25% (lá cờ).",
      means: "Hiếm gặp và mạnh nhất, thường có chất xúc tác lớn (kết quả kinh doanh, sản phẩm mới).",
      confirm: "Lá cờ phải chặt, khối lượng cạn; cờ sâu hơn 25% hoặc kéo dài thì mất giá trị [SÁCH Minervini; TRANG GỐC IBD]. Đừng nhầm với 'cây thông' bên dưới — cây thông không có nhịp nghỉ chặt." },
    { name: "Tam giác tăng", en: "Ascending triangle", kind: "Tiếp diễn tăng",
      svg: { pts: [[0, 40], [10, 85], [20, 56], [30, 85], [40, 65], [50, 85], [58, 74], [66, 85], [72, 80], [80, 96], [86, 100]],
        lines: [{ from: [8, 85.6], to: [90, 85.6], cls: "pivot", label: "Kháng cự phẳng" }, { from: [18, 55], to: [74, 80], cls: "guide", label: "" }],
        labels: [{ at: [44, 62], text: "đáy sau cao hơn đáy trước", below: true }] },
      how: "Đỉnh nằm ngang ở cùng một mức, các đáy cao dần tạo thành cạnh dưới đi lên.",
      means: "Người bán đứng chờ ở một mức giá cố định, còn người mua chấp nhận trả giá cao dần — áp lực dồn về cạnh trên.",
      confirm: "Đóng cửa vượt cạnh trên với khối lượng lớn. Thủng cạnh dưới thì mẫu hỏng." },

    { group: "Mô hình đảo chiều" },
    { name: "Hai đáy (chữ W)", en: "Double bottom", kind: "Đảo chiều tăng / nền giá",
      svg: { pts: [[0, 40], [10, 90], [22, 58], [32, 77], [38, 66], [44, 54], [52, 68], [58, 75], [64, 77], [70, 90], [76, 96]],
        lines: [{ from: [32, 77.6], to: [82, 77.6], cls: "pivot", label: "Pivot = đỉnh giữa" }, { from: [18, 58], to: [50, 58], cls: "guide", label: "" }],
        labels: [{ at: [22, 58], text: "Đáy 1", below: true }, { at: [44, 54], text: "Đáy 2 thấp hơn", below: true }] },
      how: "Hình chữ W, ít nhất ~7 tuần. Đáy thứ hai thấp hơn đáy thứ nhất một chút (rũ bỏ những người đặt stop dưới đáy 1) nhưng không sụp.",
      means: "Thị trường thử lại vùng đáy lần hai và người mua giữ được giá.",
      confirm: "Pivot là đỉnh giữa chữ W; breakout với khối lượng trên trung bình [TRANG GỐC IBD]." },
    { name: "Vai đầu vai", en: "Head and shoulders top", kind: "Đảo chiều giảm",
      svg: { pts: [[0, 30], [12, 70], [20, 55], [32, 90], [44, 56], [56, 73], [66, 53], [74, 40], [82, 36]],
        lines: [{ from: [16, 55.5], to: [76, 53.5], cls: "stop", label: "Đường viền cổ" }],
        labels: [{ at: [12, 70], text: "Vai trái" }, { at: [32, 90], text: "Đầu" }, { at: [56, 73], text: "Vai phải" }] },
      how: "Ba đỉnh: đỉnh giữa (đầu) cao nhất, hai bên (vai) thấp hơn. Nối hai đáy giữa các đỉnh được đường viền cổ.",
      means: "Mỗi lần hồi lên đều thấp hơn — người mua yếu dần ở vùng đỉnh.",
      confirm: "Mẫu chỉ hoàn thành khi giá đóng cửa thủng đường viền cổ. Dạng ngược (vai đầu vai đảo) là mẫu tạo đáy." },
    { name: "Cây thông (đỉnh nhọn)", en: "Spike top / inverted V", kind: "Rủi ro cao", example: "HID",
      svg: { pts: [[0, 20], [8, 22], [16, 19], [24, 21], [30, 20], [34, 30], [38, 45], [42, 62], [46, 80], [50, 95], [52, 98], [55, 91], [58, 76], [62, 59], [66, 43], [70, 30], [76, 22], [84, 20], [92, 19]],
        lines: [{ from: [0, 20.6], to: [98, 20.6], cls: "stop", label: "giá chân sóng" }],
        labels: [{ at: [22, 55], text: "tăng dựng đứng" }, { at: [52, 98], text: "đỉnh nhọn" }, { at: [83, 52], text: "đạp ngược về chân" }],
        vol: [.1, .1, .1, .1, .12, .3, .6, .8, .9, 1, .95, .9, .8, .7, .6, .5, .3, .2, .15] },
      how: "Giá tăng gần như thẳng đứng trong vài tuần, nhiều phiên kịch trần liên tiếp, khối lượng gấp nhiều lần bình thường, không có nhịp nghỉ chặt nào. Sau đỉnh nhọn, giá giảm nhanh không kém và thường quay về đúng vùng giá bắt đầu đợt tăng.",
      means: "Đợt tăng dựa vào dòng tiền ngắn hạn hoặc tin đồn, không có nền giá đỡ phía dưới. Khi lực mua dừng, không có vùng hỗ trợ nào trên đường xuống ngoài chân sóng.",
      confirm: "Ví dụ HID 09/2026: từ ~3.350 đ (04/09) lên đỉnh 6.040 đ (18/09) — +80% trong 10 phiên, 6 phiên tăng trần, khối lượng gấp hơn 10 lần — rồi giảm còn 4.020 đ (01/10), mất ~3/4 đoạn tăng. SEPA không mua cổ phiếu đã chạy dựng đứng không có nền; ở Việt Nam còn có rủi ro kẹt T+2 khi giảm sàn liên tiếp." },
  ];

  function card(item, draw) {
    const kindCls = /giảm|Rủi ro/.test(item.kind) ? "k-down" : /tăng|Sức mạnh/.test(item.kind) ? "k-up" : "k-flat";
    return `<article class="lcard">
      <div class="lfig">${draw(item.svg)}</div>
      <div class="lbody">
        <div class="lhead"><h3>${esc(item.name)}</h3><span class="muted small">${esc(item.en)}</span></div>
        <div class="ltags"><span class="ltag ${kindCls}">${esc(item.kind)}</span>
          ${item.detect ? '<span class="ltag k-sys" title="Hệ thống tự nhận dạng mẫu này cho các mã đang theo dõi">tự nhận dạng</span>' : ""}
          ${item.stat ? `<span class="ltag" title="Thống kê Bulkowski trên thị trường Mỹ, chưa kiểm định ở Việt Nam">Bulkowski: ${esc(item.stat)}</span>` : ""}</div>
        <dl><dt>Nhận biết</dt><dd>${esc(item.how)}</dd><dt>Ý nghĩa</dt><dd>${esc(item.means)}</dd>
          <dt>Xác nhận / lưu ý</dt><dd>${esc(item.confirm)}</dd></dl>
        ${item.example ? `<button class="btn ghost small-btn" data-t="${esc(item.example)}" type="button">Xem biểu đồ ${esc(item.example)} →</button>` : ""}
      </div></article>`;
  }

  function render(list, draw) {
    return list.map((it) => (it.group ? `<h3 class="lgroup">${esc(it.group)}</h3>` : card(it, draw))).join("");
  }

  window.renderLearn = function renderLearn() {
    const c = document.getElementById("learn-candles"), k = document.getElementById("learn-charts");
    if (!c || c.dataset.done) return;
    c.innerHTML = render(CANDLES, candleSvg);
    k.innerHTML = render(CHARTS, chartSvg);
    c.dataset.done = "1";
  };
})();
