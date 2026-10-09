# SEPA tổng quan, Stage Analysis, Trend Template, RS Rating

>
> **Quy ước độ tin cậy** (gắn cho từng quy tắc):
> - **[SÁCH]** — con số xuất hiện trong sách gốc, được xác nhận qua ít nhất một bản tóm tắt/trích dẫn độc lập.
> - **[TRANG GỐC]** — lấy trực tiếp từ trang của tác giả/tổ chức (thepatternsite.com, IBD/investors.com qua Yahoo/Nasdaq, StockCharts, TradingView, TA-Lib).
> - **[THỨ CẤP]** — chỉ thấy trong nguồn thứ cấp (blog, bản tóm tắt, script cộng đồng); chưa đối chiếu được với sách gốc.
> - **[TRIỂN KHAI]** — đề xuất của người nghiên cứu để biến mô tả định tính thành tiêu chí đo được. **Không phải phương pháp gốc.**
> - **UNVERIFIED** — không xác minh được trong phiên này.
>
> **Nguyên tắc chung**: mọi tín hiệu dưới đây là **xác suất, không phải dự báo chắc chắn**. Chúng mô tả điều kiện mà bằng chứng lịch sử (của tác giả, chủ yếu trên thị trường Mỹ) cho thấy có lợi thế thống kê — không đảm bảo kết quả trên từng mã, và chưa được kiểm định trên thị trường Việt Nam.

---

---

## 1. Tổng quan SEPA (Specific Entry Point Analysis)

**Nguồn**: Mark Minervini, *Trade Like a Stock Market Wizard* (McGraw-Hill, 2013) — viết tắt **TLSMW**; *Think & Trade Like a Champion* (Access Publishing, 2017) — viết tắt **TTLC**. Tóm tắt đối chiếu: Elearnmarkets (TLSMW, TTLC), ChartMill, Sharpely.

### 1.1 Năm yếu tố của SEPA [SÁCH]

Minervini chia SEPA thành 5 nhóm tiêu chí chính (TLSMW, theo tóm tắt Elearnmarkets):

| # | Yếu tố | Nội dung | Đo lường được bằng code? |
|---|---|---|---|
| 1 | **Xu hướng (Trend)** | Cổ phiếu phải ở Giai đoạn 2 (Stage 2) — kiểm bằng Trend Template (mục 2) | Có — hoàn toàn |
| 2 | **Cơ bản (Fundamentals)** | Tăng trưởng EPS, doanh thu, biên lợi nhuận; tăng tốc (acceleration); bất ngờ lợi nhuận (earnings surprise) | Có — nếu có dữ liệu BCTC theo quý |
| 3 | **Chất xúc tác (Catalyst)** | Sản phẩm mới, thay đổi ngành, quản trị mới, sự kiện khiến tổ chức phải mua | Một phần — cần lớp LLM/tin tức để phân loại; code chỉ gắn cờ sự kiện |
| 4 | **Điểm vào (Entry point)** | Điểm pivot từ mẫu hình nền giá, đặc biệt VCP | Có — với quy tắc hình học (mục 3) |
| 5 | **Điểm thoát (Exit point)** | Cắt lỗ định trước; bán khi mạnh (sell into strength) hoặc bán khi yếu | Có — quy tắc số (mục 3.4) |

### 1.2 Phân tích giai đoạn (Stage Analysis)

**Gốc**: Stan Weinstein, *Secrets for Profiting in Bull and Bear Markets* (1988), dùng **đường MA 30 tuần**. Minervini áp dụng lại với **MA 200 ngày (≈ 40 tuần)** và MA 150 ngày (≈ 30 tuần). Mô tả định tính dưới đây là [SÁCH] (TLSMW, qua tóm tắt Elearnmarkets); các ngưỡng số trong cột "Tiêu chí đo được" là **[TRIỂN KHAI]**.

| Giai đoạn | Mô tả của Minervini [SÁCH] | Tiêu chí đo được đề xuất [TRIỂN KHAI] |
|---|---|---|
| **Stage 1 — Tích lũy/bị bỏ quên (Neglect/Consolidation)** | Đi ngang "hàng tháng, thậm chí hàng năm"; giá dao động quanh MA200 (40 tuần); khối lượng thấp, co hẹp so với Stage 4 | `abs(slope(SMA200, 20 phiên)) < ε` (ví dụ ε = 0,5%/tháng); giá cắt qua SMA200 ≥ k lần trong 60 phiên; `vol_sma50` thấp hơn `vol_sma50` của giai đoạn giảm trước đó |
| **Stage 2 — Tăng giá/tích lũy tổ chức (Advancing/Accumulation)** | Giá trên MA150 và MA200; MA200 dốc lên; đỉnh sau cao hơn đỉnh trước, đáy sau cao hơn đáy trước; khối lượng tăng vọt khi tăng, co lại khi điều chỉnh | **Vượt Trend Template đủ 8/8 tiêu chí** (mục 2). Đây là **điều kiện cần** Minervini đặt ra ("phải thỏa mới được xem xét") cho Stage 2, **không phải điều kiện đủ** và **không phải tín hiệu mua**. Skill phải kết hợp thêm nền giá/pivot, cơ bản và trạng thái thị trường chung (mục 3.5) |
| **Stage 3 — Đỉnh/phân phối (Topping/Distribution)** | Biến động tăng so với Stage 2; dao động "rộng hơn, lỏng hơn"; thường có **phiên giảm mạnh nhất kể từ khi Stage 2 bắt đầu**; MA200 đi ngang rồi quay xuống | `ATR(14)/close` tăng > x% so với trung bình Stage 2; xuất hiện phiên (hoặc tuần) giảm % lớn nhất kể từ ngày bắt đầu Stage 2; `slope(SMA200)` từ dương về ≈ 0; giá đóng cửa dưới SMA50 nhiều lần |
| **Stage 4 — Suy giảm/đầu hàng (Declining/Capitulation)** | Giá phần lớn dưới MA200; đáy thấp hơn, đỉnh thấp hơn; gần hoặc lập đáy 52 tuần; khối lượng tăng vọt khi giảm | `close < SMA200` và `SMA200 < SMA200[20]`; `close` trong vòng x% của đáy 52 tuần; số phiên giảm có volume > vol_sma50 nhiều hơn số phiên tăng có volume > vol_sma50 |

**Chu kỳ**: Minervini cho rằng quy tắc "50/80": sau khi cổ phiếu dẫn dắt tạo đỉnh lớn, có khoảng 50% khả năng giảm 80% và 80% khả năng giảm 50% [SÁCH — TTLC, theo tóm tắt Elearnmarkets]. Chỉ mua trong Stage 2; không mua Stage 1, 3, 4.

> Lưu ý: Weinstein dùng MA 30 tuần (≈ MA150 ngày) làm đường chính; Minervini dùng MA200 ngày trong mô tả giai đoạn nhưng Trend Template kiểm cả MA150 và MA200. Code nên tính cả hai.

---

## 2. Trend Template (Mẫu xu hướng)

**Nguồn**: TLSMW (2013), TTLC (2017), blog minervini.com (2009, không truy cập được trực tiếp — HTTP 404 trong phiên này), đối chiếu qua Elearnmarkets, Sharpely, TradingView script "TwP: Mark Minervini Trend Template Criteria", ChartMill, Deepvue.

### 2.1 Tám tiêu chí [SÁCH]

Cổ phiếu phải thỏa **tất cả** 8 tiêu chí để được coi là Stage 2 được xác nhận:

| # | Tiêu chí | Biểu thức code (daily bars, giá đã điều chỉnh quyền) | Biến thể theo nguồn |
|---|---|---|---|
| TT1 | Giá hiện tại trên MA150 và MA200 | `close > SMA150 and close > SMA200` | Thống nhất |
| TT2 | MA150 trên MA200 | `SMA150 > SMA200` | Thống nhất |
| TT3 | MA200 dốc lên **ít nhất 1 tháng (tốt hơn: tối thiểu 4–5 tháng trong đa số trường hợp)** | Tối thiểu: `SMA200 > SMA200.shift(22)` (1 tháng ≈ 21–22 phiên). Ưu tiên: `SMA200 > SMA200.shift(84..105)` và/hoặc `SMA200` không giảm trong ≥ 80% số phiên của 4–5 tháng gần nhất [TRIỂN KHAI cho phần "dốc lên"] | TLSMW và TTLC giống nhau: "at least 1 month (preferably 4–5 months)". Sách không định nghĩa "trending up" theo công thức — cách đo độ dốc là lựa chọn triển khai |
| TT4 | MA50 trên MA150 và MA200 | `SMA50 > SMA150 and SMA50 > SMA200` | Thống nhất |
| TT5 | Giá hiện tại trên MA50 | `close > SMA50` | TTLC diễn đạt "trading above 50-day MA coming out of base" |
| TT6 | Giá cao hơn đáy 52 tuần ít nhất X% | `close >= low_52w * (1 + X)` với `low_52w = min(low, 252 phiên)` | **TRANH CÃI**: **30%** theo TLSMW (2013) và blog minervini.com; **25%** theo TTLC (2017). Minervini ghi chú nhiều cổ phiếu tốt nhất cao hơn 100%, 300% hoặc hơn so với đáy 52 tuần. Ghi cả hai; để trong config |
| TT7 | Giá nằm trong phạm vi 25% so với đỉnh 52 tuần (càng gần đỉnh càng tốt) | `close >= high_52w * (1 - 0.25)` với `high_52w = max(high, 252 phiên)` | Thống nhất 25% |
| TT8 | RS Rating (theo IBD) **không thấp hơn 70** | `rs_rating >= 70` | TLSMW: ≥ 70, "lý tưởng 80–90"; TTLC: ≥ 70, "tốt hơn trong vùng 90" (cả hai xác nhận qua tóm tắt Elearnmarkets). Tóm tắt TTLC còn nêu kèm "**RS line trong xu hướng tăng ít nhất 6 tuần, tốt hơn 13 tuần**" — được trình bày cạnh template, **UNVERIFIED** là một trong 8 tiêu chí gốc; nên cài như tiêu chí phụ tùy chọn (mục 2.3) |

**Chi tiết triển khai** [TRIỂN KHAI]:
- 52 tuần = 252 phiên ở Mỹ. Ở Việt Nam một năm có khoảng 245–250 phiên (nghỉ Tết dài) — dùng tham số `lookback_52w_sessions` trong config, không hard-code.
- Dùng `high`/`low` hay `close` cho đỉnh/đáy 52 tuần: sách không nói rõ. Đa số screener (ChartMill, script TradingView) dùng high/low intraday. Ghi rõ lựa chọn trong config.
- Tất cả MA phải tính trên **giá đã điều chỉnh sự kiện quyền (ex-rights adjusted)**; nếu không, một đợt chia cổ tức cổ phiếu có thể làm TT6/TT7 sai hoàn toàn.
- SMA hay EMA: sách không ghi rõ "simple"; tất cả screener tham khảo (ChartMill, Deepvue, script TradingView) dùng **SMA**. Ghi nhận: SMA là cách hiểu phổ biến, **UNVERIFIED** từ văn bản gốc.

### 2.2 RS Rating (Relative Strength Rating) của IBD

**Mô tả công khai của IBD** [TRANG GỐC — mô tả rating của IBD trích lại trên các bài IBD phát hành lại (ví dụ inkl "Stocks With Rising Relative Strength")]: RS Rating so sánh hiệu suất giá của cổ phiếu **trong 52 tuần gần nhất** với mọi cổ phiếu khác trong cơ sở dữ liệu IBD, cho điểm **1–99** (99 = mạnh hơn 99% cổ phiếu). Chi tiết "**quý gần nhất được tính trọng số gấp đôi**" chỉ đến từ **nguồn thứ cấp** (các bản tái tạo trên Portfolio123, Optuma, script TradingView, repo skyte/relative-strength) [THỨ CẤP]. Công thức chính xác của IBD là **độc quyền — UNVERIFIED**.

**Hai công thức tái tạo phổ biến** [THỨ CẤP] — ghi cả hai, không tự chọn:

**Biến thể A — các kỳ chồng lấn** (repo gốc **skyte/relative-strength**, README: "hiệu suất năm, quý gần nhất tính gấp đôi, chia cho hiệu suất năm của chỉ số tham chiếu"; Fred6725/relative-strength là bản phái sinh; script TradingView "IBD Style Relative Strength Rating"):

```
P3  = close / close.shift(63)  - 1     # ~3 tháng
P6  = close / close.shift(126) - 1
P9  = close / close.shift(189) - 1
P12 = close / close.shift(252) - 1
score = 0.4*P3 + 0.2*P6 + 0.2*P9 + 0.2*P12
rs_rating = percentile_rank(score, universe) -> làm tròn, kẹp vào [1, 99]
```

Trọng số hiệu dụng theo từng quý (xấp xỉ cộng tính): vì Q1 nằm trong cả P3/P6/P9/P12, Q2 trong P6/P9/P12, v.v., tỷ lệ trọng số **Q1 : Q2 : Q3 : Q4 = 1,0 : 0,6 : 0,4 : 0,2** — nghiêng về gần đây **mạnh hơn nhiều** so với "gấp đôi".

**Chuẩn hóa benchmark** `RS = (1 + score_stock)/(1 + score_benchmark)`: khi mọi cổ phiếu dùng **cùng một** benchmark thì đây là phép biến đổi đơn điệu → **không thay đổi thứ hạng percentile**. Chỉ hữu ích để hiển thị "RS so với chỉ số"; **không coi là biến thể riêng** trong code.

**Biến thể B — các quý không chồng lấn (bám sát câu chữ "quý gần nhất x2")**: tương đương trọng số **Q1 40%, Q2/Q3/Q4 mỗi quý 20%**.

```
Q1 = close / close.shift(63)  - 1                 # quý gần nhất
Q2 = close.shift(63)  / close.shift(126) - 1
Q3 = close.shift(126) / close.shift(189) - 1
Q4 = close.shift(189) / close.shift(252) - 1
score = 2*Q1 + Q2 + Q3 + Q4       # (hoặc chia 5 để chuẩn hóa)
rs_rating = percentile_rank(score, universe) trong [1, 99]
```

**Percentile**: `rs_rating = floor(rank_pct * 98) + 1` hoặc `ceil(rank_pct*99)` — cả hai cho khoảng 1–99; chọn một và cố định [TRIỂN KHAI]. Cổ phiếu có < 252 phiên dữ liệu (IPO mới): phải loại khỏi xếp hạng hoặc tính theo dữ liệu có sẵn và **gắn cờ** — không được nội suy.

**Bắt buộc dùng giá đóng cửa đã điều chỉnh quyền (adjusted close)** cho mọi Pk/Qk. Chính repo skyte cảnh báo giá chưa điều chỉnh chia tách làm RS sai. Ở VN, một đợt thưởng cổ phiếu 5:1 (−16,7%) sẽ kéo tụt RS của mã đó nếu dùng giá thô. Mã có chuỗi bị đánh dấu stale (`verify_continuity()` không rỗng) → **loại khỏi xếp hạng**.

**Quan trọng**: RS Rating là **xếp hạng tương đối trong một vũ trụ (universe)**. Kết quả thay đổi theo vũ trụ được chọn (xem mục 6).

### 2.3 RS line (đường sức mạnh tương đối) [THỨ CẤP]

- `rs_line = adj_close_stock / close_benchmark` (VN: benchmark = VN-Index hoặc VNAllShare — [TRIỂN KHAI]).
- **Xu hướng RS line**: tóm tắt TTLC nêu RS line nên tăng **≥ 6 tuần, tốt hơn 13 tuần** (UNVERIFIED là tiêu chí template). Đo [TRIỂN KHAI]: `rs_line > rs_line.shift(30)` (6 tuần) và/hoặc `> rs_line.shift(65)` (13 tuần), hoặc hệ số góc hồi quy dương.
- **RS line lập đỉnh mới trước giá** ("blue dot" của IBD/MarketSmith): `rs_line == max(rs_line, 252)` trong khi `close < max(close, 252)` — thường được coi là tín hiệu dẫn dắt khi cổ phiếu còn trong nền [THỨ CẤP; là quy ước IBD/MarketSmith, không phải tiêu chí Minervini].

---

