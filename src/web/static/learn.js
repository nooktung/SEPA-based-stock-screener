"use strict";
// Educational diagrams are schematic. Text lives in HTML outside the SVG viewport.
(function () {
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const $=id=>document.getElementById(id);
 function candleSvg({ctx=[],pat=[],lines=[]},compact=false) {
  const all=[...ctx,...pat],W=Math.max(160,all.length*44+56),H=220,pad=28;
  const lo=Math.min(...all.map(c=>c[2])),hi=Math.max(...all.map(c=>c[1]));
  const y=v=>pad+(hi-v)/(hi-lo||1)*(H-2*pad),step=Math.min(48,(W-50)/all.length),start=(W-step*(all.length-1))/2;
  return `<svg class="lsvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Minh họa thân và bóng nến"><path class="learn-baseline" d="M20 195H${W-20}"/>${lines.map(g=>`<line class="learn-guide" x1="20" x2="${W-20}" y1="${y(g.v)}" y2="${y(g.v)}"/>`).join('')}${all.map(([o,h,l,c],i)=>{const x=start+i*step,flat=Math.abs(c-o)<(hi-lo)*.012;return `<g class="${flat?'cf':c>o?'cu':'cd'}${i<ctx.length?' ctx':''}"><line x1="${x}" x2="${x}" y1="${y(h)}" y2="${y(l)}"/><rect x="${x-10}" y="${y(Math.max(o,c))}" width="20" height="${Math.max(2,Math.abs(y(o)-y(c)))}" rx="2"/></g>`;}).join('')}</svg>`;
 }
 function chartSvg({pts,lines=[],labels=[],vol=null}) {
  const W=480,H=260,X=x=>28+x/100*424,Y=v=>24+(100-v)/100*176;
  const path=pts.map(([x,y],i)=>`${i?'L':'M'}${X(x)},${Y(y)}`).join(' ');
  return `<svg class="lsvg wide" viewBox="0 0 ${W} ${H}" role="img" aria-label="Sơ đồ cấu trúc giá và các đường xác nhận"><path class="learn-baseline" d="M28 205H452"/>${lines.map(g=>`<line class="learn-guide ${esc(g.cls||'')}" x1="${X(g.from[0])}" y1="${Y(g.from[1])}" x2="${X(g.to[0])}" y2="${Y(g.to[1])}"/>`).join('')}<path class="price" d="${path}"/>${vol?vol.map((v,i)=>`<rect class="vb${v>.75?' hi':''}" x="${28+i*424/vol.length}" y="${252-v*30}" width="${Math.max(1,424/vol.length-3)}" height="${v*30}"/>`).join(''):''}</svg>`;
 }
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
    { name: "Doji chuồn chuồn", en: "Dragonfly doji", kind: "Lưỡng lự", detect: true,
      svg: { ctx: DOWN, pat: [[66, 66.4, 44, 66]] },
      how: "Doji có bóng dưới rất dài, gần như không có bóng trên: giá bị đạp sâu rồi kéo về đúng giá mở cửa.",
      means: "Bên mua đã đẩy lùi được đợt bán trong phiên. Sau xu hướng giảm có thể là tín hiệu tạo đáy sớm.",
      confirm: "Thống kê cho thấy chỉ khoảng một nửa trường hợp đảo chiều — không dùng riêng." },
    { name: "Doji bia mộ", en: "Gravestone doji", kind: "Lưỡng lự", detect: true,
      svg: { ctx: UP, pat: [[42, 64, 41.6, 42]] },
      how: "Doji có bóng trên rất dài, gần như không có bóng dưới: giá được kéo cao rồi bị bán về giá mở cửa.",
      means: "Bên mua thất bại ở vùng giá cao. Sau xu hướng tăng là cảnh báo lực mua cạn.",
      confirm: "Chờ phiên sau đóng cửa thấp hơn." },
    { name: "Búa", en: "Hammer", kind: "Đảo chiều tăng", detect: true,
      svg: { ctx: DOWN, pat: [[64, 70, 48, 69]] },
      how: "Xuất hiện sau xu hướng giảm. Thân nhỏ nằm ở phần trên, bóng dưới dài ít nhất gấp đôi thân, bóng trên rất ngắn.",
      means: "Giá bị bán sâu trong phiên nhưng bên mua kéo lên gần đỉnh — dấu hiệu bên bán đuối sức.",
      confirm: "Phiên sau tăng và đóng cửa trên thân búa; khối lượng lớn làm tín hiệu đáng tin hơn." },
    { name: "Người treo cổ", en: "Hanging man", kind: "Cảnh báo giảm", detect: true,
      svg: { ctx: UP, pat: [[40, 41.5, 26, 39]] },
      how: "Cùng hình dạng với búa nhưng xuất hiện sau xu hướng tăng, ở vùng giá cao.",
      means: "Trong phiên đã có lực bán mạnh — một lời cảnh báo. Nhưng thống kê cho thấy giá thường vẫn đi tiếp lên.",
      confirm: "Chỉ coi trọng khi phiên sau giảm rõ và đóng cửa dưới thân nến." },
    { name: "Búa ngược", en: "Inverted hammer", kind: "Đảo chiều tăng (yếu)", detect: true,
      svg: { ctx: DOWN, pat: [[66, 84, 65.5, 69]] },
      how: "Sau xu hướng giảm. Thân nhỏ ở phần dưới, bóng trên dài, gần như không có bóng dưới.",
      means: "Bên mua thử kéo giá lên nhưng chưa giữ được. Thống kê cho thấy giá thường vẫn tiếp tục giảm.",
      confirm: "Cần phiên sau tăng mạnh vượt bóng trên mới đáng chú ý." },
    { name: "Sao băng", en: "Shooting star", kind: "Đảo chiều giảm", detect: true,
      svg: { ctx: UP, pat: [[41, 60, 38.5, 39]] },
      how: "Sau xu hướng tăng. Thân nhỏ ở phần dưới, bóng trên dài ít nhất gấp đôi thân.",
      means: "Giá được đẩy lên cao rồi bị bán mạnh về gần giá mở cửa — bên mua hụt hơi ở vùng cao.",
      confirm: "Phiên sau giảm, đóng cửa dưới thân sao băng." },

    { group: "Cụm nến đảo chiều tăng (sau xu hướng giảm)" },
    { name: "Nhấn chìm tăng", en: "Bullish engulfing", kind: "Đảo chiều tăng", detect: true,
      svg: { ctx: DOWN.slice(1), pat: [[70, 71, 64, 65], [63, 77, 62, 75]] },
      how: "Nến đỏ nhỏ, rồi nến xanh có thân bao trùm hoàn toàn thân nến đỏ trước đó.",
      means: "Bên mua lật ngược thế trận chỉ trong một phiên.",
      confirm: "Mạnh hơn khi khối lượng phiên xanh lớn và xuất hiện tại hỗ trợ." },
    { name: "Harami tăng", en: "Bullish harami", kind: "Đảo chiều tăng (yếu)", detect: true,
      svg: { ctx: DOWN.slice(1), pat: [[74, 75, 58, 59], [62, 67, 61, 66]] },
      how: "Nến đỏ dài, rồi nến xanh nhỏ nằm gọn trong thân nến đỏ (như bà bầu — 'harami' tiếng Nhật).",
      means: "Lực bán chững lại. Tín hiệu yếu, gần như ngang xác suất.",
      confirm: "Cần phiên sau tăng vượt đỉnh nến đỏ." },
    { name: "Đường xuyên", en: "Piercing line", kind: "Đảo chiều tăng", detect: true,
      svg: { ctx: DOWN.slice(1), pat: [[74, 75, 60, 61], [58, 71, 57, 70]], lines: [{ v: 67.5, label: "giữa thân" }] },
      how: "Nến đỏ dài, rồi nến xanh mở cửa thấp hơn nhưng đóng cửa vượt quá nửa thân nến đỏ.",
      means: "Bên mua lấy lại hơn một nửa phần mất của phiên trước.",
      confirm: "Đóng cửa càng sâu vào thân nến đỏ càng tốt; phiên sau giữ được giá." },
    { name: "Sao mai", en: "Morning star", kind: "Đảo chiều tăng", detect: true,
      svg: { ctx: DOWN.slice(1), pat: [[74, 75, 60, 61], [57, 60, 54, 58], [61, 74, 60, 72]] },
      how: "Ba nến: nến đỏ dài → nến thân nhỏ thấp hơn (ngôi sao) → nến xanh dài đóng cửa vượt nửa thân nến đỏ đầu tiên.",
      means: "Giảm → lưỡng lự → bên mua thắng. Cần nhìn đủ ba phiên và vị trí hình thành.",
      confirm: "Khối lượng tăng ở nến thứ ba." },
    { name: "Ba chàng lính trắng", en: "Three white soldiers", kind: "Đảo chiều tăng", detect: true,
      svg: { ctx: DOWN.slice(1), pat: [[71, 79, 70, 78], [76, 85, 75, 84], [82, 92, 81, 91]] },
      how: "Ba nến xanh dài liên tiếp, mỗi nến mở cửa trong thân nến trước và đóng cửa cao hơn.",
      means: "Bên mua kiểm soát liên tục nhiều phiên.",
      confirm: "Nếu đã tăng quá xa, mẫu này dễ dẫn tới nhịp nghỉ ngắn." },

    { group: "Cụm nến đảo chiều giảm (sau xu hướng tăng)" },
    { name: "Nhấn chìm giảm", en: "Bearish engulfing", kind: "Đảo chiều giảm", detect: true,
      svg: { ctx: UP.slice(1), pat: [[38, 44, 37, 43], [45, 46, 32, 34]] },
      how: "Nến xanh nhỏ, rồi nến đỏ có thân bao trùm hoàn toàn thân nến xanh trước.",
      means: "Bên bán lật ngược thế trận trong một phiên ở vùng giá cao.",
      confirm: "Đáng chú ý hơn khi khối lượng lớn và ngay tại kháng cự." },
    { name: "Harami giảm", en: "Bearish harami", kind: "Cảnh báo giảm (yếu)", detect: true,
      svg: { ctx: UP.slice(1), pat: [[30, 47, 29, 46], [42, 43, 37, 38]] },
      how: "Nến xanh dài, rồi nến đỏ nhỏ nằm gọn trong thân nến xanh.",
      means: "Lực mua chững lại, nhưng thống kê cho thấy giá thường vẫn tăng tiếp.",
      confirm: "Chỉ coi trọng khi phiên sau giảm thủng đáy nến xanh." },
    { name: "Mây đen che phủ", en: "Dark cloud cover", kind: "Đảo chiều giảm", detect: true,
      svg: { ctx: UP.slice(1), pat: [[32, 47, 31, 46], [49, 50, 36, 37]], lines: [{ v: 39, label: "giữa thân" }] },
      how: "Nến xanh dài, rồi nến đỏ mở cửa cao hơn đỉnh nhưng đóng cửa dưới nửa thân nến xanh.",
      means: "Bên bán xóa hơn một nửa thành quả của phiên trước.",
      confirm: "Phiên sau tiếp tục giảm." },
    { name: "Sao hôm", en: "Evening star", kind: "Đảo chiều giảm", detect: true,
      svg: { ctx: UP.slice(1), pat: [[30, 45, 29, 44], [47, 50, 45, 46], [44, 45, 31, 33]] },
      how: "Ba nến: nến xanh dài → nến thân nhỏ cao hơn (ngôi sao) → nến đỏ dài đóng cửa dưới nửa thân nến xanh đầu tiên.",
      means: "Tăng → lưỡng lự → bên bán thắng. Cần kiểm tra vị trí và phiên xác nhận.",
      confirm: "Khối lượng lớn ở nến thứ ba." },
    { name: "Ba con quạ đen", en: "Three black crows", kind: "Đảo chiều giảm", detect: true,
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
      means: "Một cấu trúc hiếm gặp, thường có chất xúc tác lớn (kết quả kinh doanh, sản phẩm mới).",
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
      means: "Giá đi quá xa khỏi vùng tích lũy gần nhất, nên một nhịp đảo chiều có thể rất mạnh. Hình dạng này không cho biết nguyên nhân tăng giá; cần kiểm tra thanh khoản và các vùng hỗ trợ thực tế.",
      confirm: "Đối chiếu lịch sử giá, khối lượng và vùng tích lũy trước đó. Không nhầm một đợt tăng dựng đứng với nền giá chặt có điểm xác nhận rõ; mức dừng dự kiến có thể khó thực hiện nếu thanh khoản yếu." },
  ];

  // ---------------------------------------------------------------- SEPA basics (tester report 3, R3-13)
  const SEPA = [
    { name: "Trend Template — 8 tiêu chí xu hướng", en: "Minervini Trend Template", kind: "Xu hướng",
      svg: { pts: [[0, 22], [10, 26], [20, 34], [30, 44], [40, 52], [50, 63], [60, 70], [70, 80], [80, 86], [90, 92], [100, 96]],
        lines: [{ from: [0, 18], to: [100, 70], cls: "guide", label: "MA200 đi lên" }, { from: [0, 20], to: [100, 78], cls: "guide", label: "MA150" },
          { from: [20, 30], to: [100, 88], cls: "pivot", label: "MA50" }],
        labels: [{ at: [88, 92], text: "giá > MA50 > MA150 > MA200" }] },
      how: "Giá trên MA150 và MA200; MA150 trên MA200; MA200 đi lên ≥ 1 tháng; MA50 trên MA150 và MA200; giá trên MA50; cao hơn đáy 52 tuần ≥ 30%; cách đỉnh 52 tuần ≤ 25%; RS ≥ 70.",
      means: "Đây là điều kiện cần của một cổ phiếu dẫn dắt ở giai đoạn 2, chưa phải tín hiệu mua. Web tô 8 chấm xanh / đỏ cho từng tiêu chí.",
      confirm: "Nhóm A = đủ 8/8; nhóm B = gần đạt và đang tạo nền [SÁCH Minervini]." },
    { name: "Bốn giai đoạn của một cổ phiếu", en: "Stage analysis", kind: "Xu hướng",
      svg: { pts: [[0, 30], [8, 27], [16, 31], [24, 28], [30, 32], [36, 45], [44, 62], [52, 78], [58, 88], [64, 84], [70, 90], [76, 85], [82, 70], [90, 52], [100, 38]],
        labels: [{ at: [15, 30], text: "GĐ1 tích lũy", below: true }, { at: [46, 64], text: "GĐ2 tăng" }, { at: [70, 90], text: "GĐ3 phân phối" }, { at: [92, 50], text: "GĐ4 giảm", below: true }] },
      how: "GĐ1: đi ngang dưới / quanh MA200 phẳng. GĐ2: giá và các đường MA đi lên, cấu trúc đỉnh / đáy cao dần. GĐ3: dao động mạnh ở vùng đỉnh, MA đi ngang. GĐ4: giá dưới MA200 đi xuống.",
      means: "SEPA chỉ mua ở giai đoạn 2. Trend Template là cách đo giai đoạn 2 bằng con số.",
      confirm: "Mua ở GĐ1 là chờ không biết bao lâu; mua ở GĐ3–4 là đi ngược xu hướng [SÁCH Minervini, Weinstein]." },
    { name: "Pivot và vùng mua", en: "Pivot buy zone", kind: "Điểm mua",
      svg: { pts: [[0, 40], [10, 85], [22, 62], [34, 80], [46, 70], [58, 78], [66, 74], [72, 77], [78, 83], [84, 88], [92, 90]],
        lines: [{ from: [34, 79], to: [100, 79], cls: "pivot", label: "Pivot" }, { from: [74, 83], to: [100, 83], cls: "guide", label: "+5%" }],
        labels: [{ at: [64, 74], text: "Sắp tới pivot", below: true }, { at: [86, 86], text: "Vùng mua" }] },
      how: "Pivot = đỉnh của nhịp co hẹp cuối cộng một bước giá. Vùng mua theo SEPA là từ pivot tới cao hơn pivot 5%.",
      means: "Dưới pivot là chưa có điểm mua (web ghi \"Sắp tới pivot\"); trên pivot quá 5% là đã chạy xa, rủi ro tới stop lớn.",
      confirm: "Chỉ khi giá vượt pivot với khối lượng tăng và thị trường chung cho phép [SÁCH Minervini]." },
    { name: "Cắt lỗ 7–8%", en: "Stop loss", kind: "Kỷ luật",
      svg: { pts: [[0, 60], [15, 66], [30, 72], [42, 70], [52, 62], [62, 55], [72, 48], [82, 44], [92, 40]],
        lines: [{ from: [30, 72], to: [100, 72], cls: "pivot", label: "Giá mua" }, { from: [30, 50], to: [100, 50], cls: "stop", label: "Stop −8%" }],
        labels: [{ at: [80, 44], text: "bán khi đóng cửa dưới stop", below: true }] },
      how: "Đặt mức cắt lỗ trước khi mua: dưới đáy nhịp co hẹp cuối, và không xa hơn 7–8% dưới giá mua. Web tính stop sao cho rủi ro từ pivot ≤ 8%.",
      means: "Lỗ 8% cần lãi 8,7% để hòa; lỗ 30% cần lãi 43%. Cắt lỗ nhỏ giữ được vốn cho lần thử sau.",
      confirm: "Không bình quân giá xuống, không dời stop xuống. Cần tính tới thanh khoản, thời gian chứng khoán về tài khoản và khả năng thực hiện mức dừng lỗ [SÁCH Minervini]." },
    { name: "Quản trị vốn: mua bao nhiêu", en: "Position sizing", kind: "Quản trị vốn",
      svg: { pts: [[0, 50], [100, 50]],
        labels: [{ at: [50, 80], text: "khối lượng = rủi ro chấp nhận / (giá mua − stop)" }, { at: [50, 40], text: "rủi ro mỗi lệnh ≈ 1–2% tài khoản", below: true }] },
      how: "Ví dụ minh họa: tài khoản 500 triệu, chấp nhận mất 1% = 5 triệu cho một lệnh; mua 50.000 đ, stop 46.000 đ → rủi ro 4.000 đ/cp → tối đa 1.250 cp (khoảng 62,5 triệu).",
      means: "Khối lượng mua tính từ khoảng cách tới stop, không tính theo cảm giác. Stop càng xa thì mua càng ít.",
      confirm: "Tổng rủi ro các lệnh đang mở nên được giới hạn; khi thua liên tiếp thì giảm quy mô. Web không tính khối lượng mua cho bạn — đây chỉ là cách tính tham khảo [SÁCH Minervini]." },
    { name: "Thị trường chung quyết định nhiều nhất", en: "Market direction", kind: "Thị trường",
      svg: { pts: [[0, 80], [12, 70], [24, 58], [34, 46], [42, 38], [48, 42], [54, 48], [60, 56], [68, 64], [76, 72], [86, 80], [96, 88]],
        labels: [{ at: [42, 38], text: "đáy", below: true }, { at: [60, 56], text: "FTD" }, { at: [16, 70], text: "ngày phân phối" }] },
      how: "Theo dõi VN-Index: số ngày phân phối (giảm ≥ 0,2% với khối lượng lớn hơn phiên trước) và ngày follow-through (FTD) xác nhận nhịp hồi.",
      means: "Diễn biến thị trường chung giúp đánh giá bối cảnh của cổ phiếu; không suy ra kết quả một lệnh chỉ từ trạng thái VN-Index.",
      confirm: "Thị trường điều chỉnh: SEPA không mở vị thế mới; chịu áp lực: giảm số lượng và quy mô [TRANG GỐC IBD; SÁCH Minervini]." },
  ];


 // More common structures, paired with an explicit invalidation condition.
 CHARTS.push(
  {name:'Hai đỉnh',en:'Double top',kind:'Đảo chiều giảm',svg:{pts:[[0,20],[15,85],[30,48],[48,83],[62,46],[78,25],[95,18]],lines:[{from:[25,48],to:[95,48],cls:'stop',label:'Vùng xác nhận: đáy giữa hai đỉnh'}]},how:'Hai lần thử vùng đỉnh tương tự nhau, ở giữa có một nhịp giảm rõ.',means:'Cấu trúc mới là cảnh báo khi giá còn trên đáy giữa hai đỉnh.',confirm:'Chỉ coi là hoàn thành khi phá hỗ trợ giữa hai đỉnh; nếu vượt lại vùng đỉnh, cách đọc giảm không còn phù hợp.'},
  {name:'Tam giác tăng',en:'Ascending triangle',kind:'Chờ phá biên',svg:{pts:[[0,35],[12,85],[24,42],[36,85],[48,55],[60,85],[72,69],[82,85],[95,98]],lines:[{from:[12,85],to:[90,85],cls:'pivot',label:'Kháng cự ngang'},{from:[24,42],to:[82,76],label:'Đáy nâng dần'}]},how:'Đỉnh gặp một vùng kháng cự ngang trong khi các đáy sau cao dần.',means:'Biên dao động thu hẹp. Hình tam giác chưa đảm bảo giá sẽ phá lên.',confirm:'Quan sát giá đóng cửa ra ngoài biên và khối lượng; thủng cạnh dưới làm mất kịch bản phá lên.'},
  {name:'Tam giác giảm',en:'Descending triangle',kind:'Chờ phá biên',svg:{pts:[[0,90],[12,25],[24,78],[36,25],[48,63],[60,25],[72,47],[84,25],[96,10]],lines:[{from:[12,25],to:[96,25],cls:'stop',label:'Hỗ trợ ngang'},{from:[0,90],to:[84,37],label:'Đỉnh hạ dần'}]},how:'Đáy giữ quanh một vùng hỗ trợ ngang, còn đỉnh sau thấp dần.',means:'Áp lực bán xuất hiện ở các mức thấp dần nhưng vẫn cần chờ giá rời biên.',confirm:'Phá hỗ trợ là điều kiện xác nhận kịch bản giảm; vượt cạnh trên là dấu hiệu cách đọc này đã sai.'},
  {name:'Phá vỡ giả',en:'False breakout',kind:'Rủi ro cao',svg:{pts:[[0,55],[14,75],[27,56],[40,74],[54,60],[66,76],[74,94],[82,70],[89,54],[100,40]],lines:[{from:[8,76],to:[100,76],cls:'pivot',label:'Kháng cự / vùng phá vỡ'}]},how:'Giá có lúc vượt kháng cự nhưng nhanh chóng quay lại bên trong nền.',means:'Việc chạm qua một đường giá chưa chứng minh bên mua giữ được vùng đó.',confirm:'So sánh giá đóng cửa, khối lượng và khả năng giữ vùng vừa vượt; xác định trước điều kiện vô hiệu thay vì cố giữ tên mô hình.'}
 );
 const neutral={
  'Doji chuồn chuồn':['Bên mua kéo giá lên khỏi đáy phiên; ý nghĩa phụ thuộc xu hướng và vùng hỗ trợ.','Quan sát phiên sau và phản ứng tại vùng giá đó, không xem hình dạng riêng lẻ là xác nhận đảo chiều.'],
  'Doji bia mộ':['Giá không giữ được vùng cao trong phiên.','Cần xem xu hướng trước đó và phản ứng ở kháng cự; doji riêng lẻ không xác định được hướng tiếp theo.'],
  'Người treo cổ':['Có lực bán trong phiên tăng trước đó; đây mới là một cảnh báo.','Chờ diễn biến sau và xem hỗ trợ gần nhất có bị phá hay không.'],
  'Búa ngược':['Có nỗ lực kéo lên nhưng mức đóng cửa chưa giữ được vùng cao.','Cần diễn biến sau xác nhận; một bóng nến dài không đủ chứng minh xu hướng đã đổi.'],
  'Harami tăng':['Biên thân nến thu hẹp sau phiên giảm mạnh.','Phân biệt nhịp nghỉ với đảo chiều bằng diễn biến tiếp theo và vị trí hỗ trợ.'],
  'Harami giảm':['Biên thân nến thu hẹp sau phiên tăng mạnh.','Chưa kết luận giảm nếu giá vẫn giữ hỗ trợ và xu hướng trước đó.']
 };
 for(const c of CANDLES)if(neutral[c.name]){c.means=neutral[c.name][0];c.confirm=neutral[c.name][1];}
 const BASICS=[
  {name:'Cấu tạo một cây nến',en:'Open · High · Low · Close',kind:'Nền tảng',candle:true,svg:{pat:[[50,54,47,53],[53,55,48,49]]},how:'Mỗi cây nến biểu diễn một khoảng thời gian: phiên, tuần hoặc tháng. Bốn mức giá là mở cửa (O), cao nhất (H), thấp nhất (L) và đóng cửa (C).',means:'Thân là đoạn từ mở cửa đến đóng cửa. Bóng trên đi tới giá cao nhất; bóng dưới đi tới giá thấp nhất. Nến xanh khi đóng cao hơn mở, nến đỏ khi đóng thấp hơn mở.',confirm:'Màu nến so mở–đóng trong cùng cây nến; % trên bảng giá lại so với giá tham chiếu. Hai cách so sánh có thể cho màu khác nhau.'},
  {name:'Đọc thân và bóng nến',en:'Real body & shadows',kind:'Nền tảng',candle:true,svg:{pat:[[50,54,35,53],[50,68,47,48],[50,61,39,50]]},how:'So độ dài thân, bóng trên và bóng dưới với toàn bộ biên cao–thấp của phiên. Thân dài thể hiện chênh lệch mở–đóng lớn; thân nhỏ cho thấy hai mức này gần nhau.',means:'Bóng dưới dài cho thấy giá từng xuống thấp rồi hồi; bóng trên dài cho thấy giá từng lên cao nhưng không giữ được tới đóng cửa. Doji có mở và đóng gần nhau.',confirm:'Hình dạng kể diễn biến trong phiên, chưa đủ khẳng định đảo chiều. Cần đặt cạnh các nến trước và vùng hỗ trợ hoặc kháng cự.'},
  {name:'Xu hướng và vị trí',en:'Trend & price zones',kind:'Bối cảnh',svg:{pts:[[0,20],[12,45],[24,32],[36,60],[48,46],[60,76],[72,60],[88,94]],lines:[{from:[0,18],to:[90,74],label:'Vùng hỗ trợ đi lên'}]},how:'Đọc một chuỗi nến: đỉnh và đáy nâng dần, hạ dần hay đi ngang? Đánh dấu những vùng giá nhiều lần có phản ứng thay vì ép mọi điểm vào một đường mỏng.',means:'Một nến xanh giữa cấu trúc đỉnh–đáy giảm chưa đủ xác nhận xu hướng tăng. Cùng một hình nến ở hỗ trợ và ở giữa vùng đi ngang có thể mang ý nghĩa khác nhau.',confirm:'Xem khung thời gian lớn hơn và vị trí của mẫu trong cấu trúc chung trước khi đặt tên hoặc diễn giải.'},
  {name:'Khối lượng và xác nhận',en:'Volume & confirmation',kind:'Bối cảnh',svg:{pts:[[0,48],[15,72],[28,50],[42,73],[55,55],[68,72],[78,90],[90,75],[100,88]],lines:[{from:[10,74],to:[100,74],cls:'pivot',label:'Kháng cự và vùng phá vỡ'}],vol:[.4,.5,.45,.5,.35,.3,.35,.9,.5,.7]},how:'So khối lượng hiện tại với các phiên gần đó. Quan sát giá đóng cửa và khả năng giữ vùng vừa vượt, thay vì chỉ nhìn râu nến chạm qua cản.',means:'Khối lượng cho thêm thông tin về mức tham gia, không tự quyết định hướng đi. Một lần vượt kháng cự vẫn có thể trở thành phá vỡ giả.',confirm:'Luôn xác định điều kiện làm cách đọc ban đầu không còn phù hợp; đánh giá lại khi giá quay vào nền hoặc phá theo hướng ngược lại.'}
 ];
 const modules=[['candles','01','Mẫu nến','Chọn một mẫu bên trái để xem hình và cách đọc bên phải.'],['patterns','02','Mô hình giá','Các cấu trúc nhiều phiên, vùng xác nhận và điều kiện thất bại.'],['basics','03','Đọc biểu đồ','Cấu tạo nến, thân–bóng nến, xu hướng và khối lượng.'],['sepa','04','SEPA & rủi ro','Xu hướng, điểm vô hiệu và quy mô vị thế.']];
 let active='candles',selected={basics:0,candles:0,patterns:0,sepa:0};
 const library=()=> (active==='basics'?BASICS:active==='candles'?CANDLES:active==='patterns'?CHARTS:SEPA).filter(x=>!x.group);
 function figure(item,mini=false){
  if(item.en==='Position sizing')return mini?'<span class="learn-formula-icon">÷</span>':`<div class="learn-formula"><span>Số cổ phiếu theo ngân sách rủi ro</span><strong>Rủi ro chấp nhận ÷ (giá mua − mức dừng)</strong><small>Ví dụ: 5.000.000 ÷ 4.000 = 1.250 cổ phiếu</small></div>`;
  const isCandle=active==='candles'||item.candle;
  const labels=[...(item.svg.lines||[]).filter(x=>x.label).map(x=>x.label),...(item.svg.labels||[]).map(x=>x.text)];
  return (isCandle?candleSvg(item.svg):chartSvg(item.svg))+(!mini?`<figcaption><span>Hình minh họa · không theo tỷ lệ thật</span>${labels.length?`<div class="learn-figure-keys">${labels.map(t=>`<span>${esc(t)}</span>`).join('')}</div>`:''}${isCandle?'<small>Nến mờ: bối cảnh trước mẫu. Nến đậm: cụm nến đang học.</small>':item.svg.vol?'<small>Các cột phía dưới minh họa khối lượng.</small>':''}</figcaption>`:'');
 }
 function renderLesson(item){
  const fail=active==='candles'?'Nếu giá không giữ được vùng hình thành mẫu hoặc diễn biến sau đi ngược hướng kỳ vọng, cần đánh giá lại; không suy ra tín hiệu chỉ từ tên nến.':active==='patterns'?'Nếu giá phá theo hướng ngược lại hoặc nhanh chóng quay lại nền, cấu trúc có thể thất bại. Vùng vô hiệu cần được xác định trước khi áp dụng.':'Quy tắc là khung quản trị, không bảo đảm khớp lệnh tại giá mong muốn hoặc bảo đảm kết quả.';
  $('learn-lesson').innerHTML=`<div class="learn-lesson-head"><div><span class="learn-eyebrow">ĐANG TÌM HIỂU</span><h3>${esc(item.name)}</h3><p>${esc(item.en)}</p></div><span class="ltag">${esc(item.kind)}</span></div><div class="learn-lesson-layout"><figure class="lfig">${figure(item)}</figure><div class="learn-explanation"><section><h4>01 · Nhận biết</h4><p>${esc(item.how)}</p></section><section><h4>02 · Cách đọc</h4><p>${esc(item.means)}</p></section><section><h4>03 · Xác nhận & lưu ý</h4><p>${esc(item.confirm)}</p></section><details><summary>Khi nào cách đọc này không còn phù hợp?</summary><p>${fail}</p></details>${item.example?`<button class="btn" data-t="${esc(item.example)}">Mở biểu đồ ${esc(item.example)} để đối chiếu →</button>`:''}</div></div>`;
 }
 function renderLibrary(){
  const items=library(),rows=items.map((item,i)=>({item,i}));
  $('learn-picker').innerHTML=rows.map(({item,i})=>`<option value="${i}">${esc(item.name)}</option>`).join('');$('learn-picker').disabled=!rows.length;
  $('learn-catalog').innerHTML=rows.map(({item,i})=>`<button type="button" class="learn-preview ${selected[active]===i?'selected':''}" data-lesson="${i}" aria-pressed="${selected[active]===i}"><div class="learn-preview-figure" aria-hidden="true">${figure(item,true)}</div><div class="learn-preview-copy"><span class="learn-preview-kind">${esc(item.kind)}</span><h3>${esc(item.name)}</h3><span>${esc(item.en)}</span></div><span class="learn-preview-arrow" aria-hidden="true">↗</span></button>`).join('')||'<p class="empty">Không có bài phù hợp. Thử từ khóa khác hoặc bỏ bộ lọc.</p>';
  if(rows.length){if(!rows.some(x=>x.i===selected[active])){selected[active]=rows[0].i;return renderLibrary();}renderLesson(items[selected[active]]);$('learn-picker').value=String(selected[active]);$('learn-lesson').hidden=false;}
  else $('learn-lesson').hidden=true;
 }
 function renderModule(){
  $('learn-content').setAttribute('aria-labelledby','learn-tab-'+active);
  document.querySelectorAll('[data-learn-tab]').forEach(b=>{const on=b.dataset.learnTab===active;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});
  renderLibrary();
 }
 function selectLesson(index){
  selected[active]=index;renderLesson(library()[index]);$('learn-picker').value=String(index);
  document.querySelectorAll('#learn-catalog [data-lesson]').forEach(b=>{const on=Number(b.dataset.lesson)===index;b.classList.toggle('selected',on);b.setAttribute('aria-pressed',String(on));});
 }
 window.renderLearn=function(){
  const root=$('tab-learn');if(root.dataset.ready)return;root.dataset.ready='1';
  $('learn-nav').innerHTML=modules.map(([id,num,title])=>`<button type="button" role="tab" id="learn-tab-${id}" aria-controls="learn-content" aria-selected="${id===active}" data-learn-tab="${id}"><span>${num}</span>${title}</button>`).join('');
  renderModule();
  root.addEventListener('click',e=>{
   const tab=e.target.closest('[data-learn-tab]');if(tab){active=tab.dataset.learnTab;renderModule();return;}
   const lesson=e.target.closest('[data-lesson]');if(lesson)selectLesson(Number(lesson.dataset.lesson));
  });
  $('learn-picker').addEventListener('change',e=>selectLesson(Number(e.target.value)));
  $('learn-nav').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();let i=modules.findIndex(x=>x[0]===active);i=e.key==='Home'?0:e.key==='End'?modules.length-1:(i+(e.key==='ArrowRight'?1:-1)+modules.length)%modules.length;active=modules[i][0];renderModule();$('learn-tab-'+active).focus();});
 };
})();
