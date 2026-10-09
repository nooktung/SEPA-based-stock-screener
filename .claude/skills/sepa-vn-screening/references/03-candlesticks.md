# Mẫu hình nến Nhật (tiếp diễn và đảo chiều)

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

## 4. Mẫu hình nến Nhật (Candlestick patterns)

**Nguồn**: Steve Nison, *Japanese Candlestick Charting Techniques* (1991; bản 2, 2001); Thomas Bulkowski, *Encyclopedia of Candlestick Charts* (Wiley, 2008) và thepatternsite.com; TA-Lib candle settings.

> **Cảnh báo**: Nến là **tín hiệu xác suất, không phải dự báo**. Kiểm định của Bulkowski (~4,7 triệu nến, chủ yếu cổ phiếu Mỹ) cho thấy **nhiều mẫu hình kinh điển hoạt động gần như ngẫu nhiên (50–60%)**, và một số mẫu hình "đảo chiều" thực tế lại **tiếp diễn** nhiều hơn. Trong SEPA, nến chỉ nên là **bằng chứng phụ** tại điểm pivot/stop, không phải tín hiệu độc lập.

### 4.0 Nguyên tố hình học (dùng chung cho code)

```
body        = abs(C - O)
rng         = H - L                       # nếu rng == 0 -> bỏ qua nến (không phân loại)
upper       = H - max(O, C)
lower       = min(O, C) - L
is_white    = C > O ;  is_black = C < O
body_mid    = (O + C) / 2
avg_body(N) = SMA(body, N) của N nến TRƯỚC (không gồm nến hiện tại)
avg_rng(N)  = SMA(rng, N)  của N nến TRƯỚC
```

**Ngưỡng — các nguồn KHÁC NHAU, phải để trong config**:

| Khái niệm | Nison | Bulkowski | TA-Lib mặc định [TRANG GỐC] |
|---|---|---|---|
| Doji | Giá mở và đóng **bằng hoặc gần như bằng** nhau | Mở và đóng "cách nhau vài xu" (within pennies) | `body <= 0.1 × avg_rng(10)` |
| Thân dài (long body) | Thân dài, định tính | **Tall candle**: chiều cao = H − L; so với trung bình 22 phiên trước (≈ 1 tháng, không gồm nến hiện tại). **Mặc định `rng > 1.46 × avg_rng(22)`**: trang MinorHiLow của Bulkowski mô tả quy trình "tính chiều cao trung bình 22 phiên trước… **nhân chiều cao trung bình với 146%** và so sánh"; 146% là trung vị chiều cao của các tall candle trong nghiên cứu. Câu "146% above" trên trang TallCandleSetup là diễn đạt lỏng (nếu đọc theo chữ sẽ thành 2,46×) — chỉ giữ làm ghi chú. **ĐÃ GIẢI QUYẾT (U15)**; tham số `tall_candle_mult = 1.46` | `body > 1.0 × avg_body(10)`; BodyVeryLong: `> 3.0 × avg_body(10)` |
| Thân ngắn | Định tính | Ngược với tall | `body < 1.0 × avg_body(10)` |
| Bóng dài | Hammer: bóng dưới ≥ **2 lần** thân | Hammer: bóng dưới **≥ 2–3 lần** thân | ShadowLong: `shadow > 1.0 × body`; ShadowVeryLong: `> 2.0 × body` |
| Bóng rất ngắn | "Ít hoặc không có" | "Little or no" | `shadow < 0.1 × avg_rng(10)` |
| "Gần" (near) / "xa" (far) / "bằng" (equal) | — | — | Near `0.2 × avg_rng(5)`; Far `0.6 × avg_rng(5)`; Equal `0.05 × avg_rng(5)` |

**Bối cảnh xu hướng trước** [TRIỂN KHAI]: Nison và Bulkowski đều yêu cầu xu hướng trước cho mẫu hình đảo chiều nhưng **không đưa công thức duy nhất**. Đề xuất: `downtrend = close[-1] < SMA(close,10)[-1] and SMA10 dốc xuống trong 5 phiên` (hoặc hồi quy tuyến tính 10 phiên có hệ số góc âm). Tham số trong config.

**Xác nhận (confirmation)** [SÁCH — Nison, nguyên tắc chung]: tín hiệu đảo chiều tăng được xác nhận khi phiên sau đóng cửa **trên** thân/đỉnh mẫu hình; đảo chiều giảm khi phiên sau đóng **dưới**. Bulkowski định nghĩa "breakout" là giá đóng cửa vượt đỉnh hoặc thủng đáy của **toàn bộ mẫu hình** [TRANG GỐC].

Cột "Bulkowski" dưới đây: `hành vi thực tế %` / `hạng hiệu suất tổng thể (1 = tốt nhất, trên 103)` / `hạng tần suất`. Tất cả [TRANG GỐC] thepatternsite.com trừ khi ghi khác.

**Cách đọc số liệu Bulkowski (bắt buộc hiển thị kèm khi skill trích dẫn)**:
- **Hạng hiệu suất tổng thể** xếp theo **hiệu suất 10 phiên sau breakout**, tổng hợp qua thị trường tăng và giảm, hai chiều breakout (ví dụ trang Dragonfly: "10-day performance after the breakout ranks it 98th"). Nó **không** đo tỷ lệ đảo chiều.
- **% đảo chiều/tiếp diễn** chỉ cho biết giá đóng cửa vượt đỉnh hay thủng đáy mẫu hình trước — không nói gì về quy mô di chuyển sau đó. Một số % cao (ví dụ three white soldiers 82%, bearish engulfing 79%) đi kèm xu hướng sau **ngắn**, như chính Bulkowski ghi chú.
- Dữ liệu là **cổ phiếu Mỹ**, từ sách 2008 (~4,7 triệu nến). **Không có kiểm định cho VN.** Mẫu hình hiếm (tần suất > 90) có cỡ mẫu nhỏ (vài chục đến ~100 trường hợp) → sai số lớn.

### 4.1 Nến đơn

| Mẫu hình | Định nghĩa hình học (code) | Xu hướng trước | Xác nhận | Bulkowski |
|---|---|---|---|---|
| **Doji (chung)** | `body <= doji_ratio × avg_rng(10)` (TA-Lib 0,1) | Không bắt buộc | Cần | Xem các biến thể |
| **Long-legged doji** | Doji + bóng trên và bóng dưới đều dài (ví dụ `upper, lower >= long_shadow_ratio × avg_rng`) | Không | Cần | Tiếp diễn tăng 51% (ngẫu nhiên) / hạng 37 / tần suất 41 |
| **Dragonfly doji** | Doji + bóng dưới dài + `upper ≈ 0` | Không bắt buộc (Bulkowski) | Cần | Đảo chiều 50% / hạng **98** / tần suất 44 |
| **Gravestone doji** | Doji + bóng trên dài + `lower ≈ 0` | Không bắt buộc | Cần | Đảo chiều giảm 51% / hạng 77 / tần suất 42 |
| **Northern doji** | Doji trong xu hướng tăng | Tăng | Cần | Tiếp diễn tăng 51% / hạng 83 / tần suất 6 |
| **Southern doji** | Doji trong xu hướng giảm | Giảm | Cần | Đảo chiều tăng 52% / hạng 78 / tần suất 8 |
| **Hammer (Búa)** | `lower >= 2 × body` (Nison: ≥ 2; Bulkowski: 2–3); `upper` rất nhỏ; thân nằm ở phần trên của biên độ | **Giảm** | Nison: nên chờ xác nhận | Đảo chiều tăng 60% / hạng 65 / tần suất 36 |
| **Hanging man (Người treo cổ)** | Cùng hình học với hammer | **Tăng** | Nison: **bắt buộc** xác nhận — phiên sau đóng cửa dưới thân hanging man | **Tiếp diễn tăng 59%** (ngược lý thuyết) / hạng 87 / tần suất 16 |
| **Inverted hammer (Búa ngược)** | Nison: nến đơn, `upper >= 2 × body`, `lower` rất nhỏ, thân ở phần dưới. **Bulkowski định nghĩa là mẫu 2 nến**: nến đen cao đóng cửa gần đáy, tiếp theo nến ngắn có bóng trên cao, ít/không bóng dưới | Giảm | Cần (Nison) | **Tiếp diễn giảm 65%** (ngược lý thuyết) / hạng **6** / tần suất 61 |
| **Shooting star (Sao băng)** | `upper >= 2 × body`; `lower` rất nhỏ; thân nhỏ **không phải doji** | **Tăng** | Cần | Đảo chiều giảm 59% / hạng 55 / tần suất 37 |
| **Marubozu trắng** | Nến trắng cao, `upper ≈ 0` và `lower ≈ 0` | Không | — | Tiếp diễn 56% / hạng 71 / tần suất 27 |
| **Marubozu đen** | Nến đen cao, không bóng | Không | — | Tiếp diễn 53% / hạng 57 / tần suất 30 |
| **Spinning top (Con xoay)** | Thân nhỏ; bóng (trên và dưới) cao hơn thân | Không | — | Trắng: đảo chiều 50% / hạng 69 / tần suất **2**. Đen: đảo chiều 51% / hạng 73 / tần suất **1** |

### 4.2 Nến đôi

| Mẫu hình | Định nghĩa hình học (code) | Xu hướng trước | Bulkowski |
|---|---|---|---|
| **Bullish engulfing (Nhấn chìm tăng)** | n1 đen, n2 trắng; `O2 < C1` và `C2 > O1` (thân n2 bao trọn thân n1; bỏ qua bóng). Nison: thân n1 có thể là doji | Giảm | Đảo chiều 63% / hạng 84 / tần suất 12 |
| **Bearish engulfing (Nhấn chìm giảm)** | n1 trắng, n2 đen; `O2 > C1` và `C2 < O1` | Tăng | Đảo chiều **79%** nhưng hạng **91** (xu hướng sau ngắn) / tần suất 11 |
| **Bullish harami** | n1 đen cao; n2 trắng với thân nằm trong thân n1 (`max(O2,C2) <= O1`, `min(O2,C2) >= C1`); bỏ qua bóng | Giảm | Đảo chiều 53% / hạng 38 / tần suất 25 |
| **Bearish harami** | n1 trắng cao; n2 đen nhỏ, O2 và C2 nằm trong thân n1 | Tăng | **Tiếp diễn tăng 53%** / hạng 72 / tần suất 26 |
| **Bullish harami cross** | n1 đen cao; n2 doji nằm trong **biên độ cao–thấp** của n1 | Giảm | **Tiếp diễn giảm 55%** / hạng 50 / tần suất 47 |
| **Bearish harami cross** | n1 trắng cao; n2 doji nằm trong biên độ n1 (kể cả bóng) | Tăng | **Tiếp diễn tăng 57%** / hạng 80 / tần suất 45 |
| **Piercing line (Đường xuyên)** | n1 đen; n2 trắng **mở cửa dưới đáy n1** (`O2 < L1`), đóng cửa **trên điểm giữa thân n1 nhưng dưới O1** (`body_mid1 < C2 < O1`) | Giảm | Đảo chiều 64% / hạng **13** / tần suất 40 |
| **Dark cloud cover (Mây đen che phủ)** | n1 trắng cao; n2 đen **mở cửa trên đỉnh n1** (`O2 > H1`) [Bulkowski]; đóng cửa **dưới điểm giữa thân n1** (`C2 < body_mid1`) [Bulkowski]; và đóng cửa **trên O1** (`C2 > O1`, nếu không sẽ thành bearish engulfing) [Nison — Bulkowski không nêu điều kiện này] | Tăng | Đảo chiều 60% / hạng 22 / tần suất 46 |
| **Tweezer top (Nhíp đỉnh)** | Hai nến liền kề có **đỉnh bằng hoặc gần bằng** nhau (`abs(H1-H2) <= equal × avg_rng`) | Tăng | **Tiếp diễn tăng 56%** / hạng 81 / tần suất 35 |
| **Tweezer bottom (Nhíp đáy)** | Hai nến có **đáy bằng nhau** | Giảm | **Tiếp diễn giảm 52%** / hạng 44 / tần suất 39 |
| **Bullish kicker/kicking** | n1 marubozu đen cao; **gap lên**; n2 marubozu trắng cao (`L2 > H1`) | Không bắt buộc (Bulkowski) | Đảo chiều 53% / hạng **96** / tần suất 100 (hiếm) |
| **Bearish kicker/kicking** | n1 marubozu trắng; gap xuống; n2 marubozu đen (`H2 < L1`) | Không bắt buộc | Đảo chiều 54% / hạng **102** / tần suất 102 |

**Lưu ý piercing/dark cloud**: Nison và Bulkowski dùng **mở cửa dưới đáy (trên đỉnh) của nến trước**. Một số nguồn thứ cấp nới lỏng thành "dưới giá đóng cửa trước" — đó là biến thể, không phải định nghĩa gốc.

### 4.3 Nến ba (và năm)

| Mẫu hình | Định nghĩa hình học (code) | Xu hướng trước | Bulkowski |
|---|---|---|---|
| **Morning star (Sao mai)** | n1 đen cao; n2 thân nhỏ (màu bất kỳ), **thân gap xuống dưới thân n1** (`max(O2,C2) < C1`); n3 trắng cao, **thân gap lên trên thân n2** và đóng cửa **ít nhất giữa thân n1** (`C3 >= body_mid1`) | Giảm | Đảo chiều 78% / hạng **12** / tần suất 66 |
| **Evening star (Sao hôm)** | n1 trắng cao; n2 thân nhỏ, thân gap **lên trên** thân n1 và n3; n3 đen cao, mở cửa dưới n2, đóng cửa **ít nhất giữa thân n1** (`C3 <= body_mid1`) | Tăng | Đảo chiều 72% / hạng **4** / tần suất 71 |
| **Morning doji star** | Như morning star nhưng n2 là **doji**; thân n2 gap dưới thân n1; thân n3 gap trên thân n2 | Giảm | Đảo chiều 76% / hạng 25 / tần suất 78 |
| **Evening doji star** | Như evening star nhưng n2 là doji; thân n2 gap trên hai nến bên cạnh; n3 đen cao đóng cửa **tại hoặc dưới** điểm giữa n1 | Tăng | Đảo chiều 71% / hạng 30 / tần suất 81 |
| **Three white soldiers (Ba chàng lính trắng)** | Ba nến trắng cao; mỗi nến **đóng gần đỉnh**; đóng cửa sau cao hơn; mỗi nến **mở cửa trong thân nến trước** | Giảm (theo lý thuyết) | Đảo chiều 82% / hạng 32 / tần suất 67 — xu hướng sau thường ngắn |
| **Three black crows (Ba con quạ đen)** | Ba nến đen cao; nến 2 và 3 **mở cửa trong thân nến trước**; cả ba **đóng gần đáy**, tạo đáy mới | **Tăng** | Đảo chiều 78% / hạng **3** / tần suất 60 |
| **Three inside up** | n1 đen cao; n2 trắng nhỏ với thân **nằm trong thân n1** (đỉnh hoặc đáy thân có thể bằng, nhưng không cả hai); n3 trắng **đóng cửa trên C2** | Giảm | Đảo chiều 65% / hạng 20 / tần suất 31 |
| **Three inside down** | n1 trắng cao; n2 đen nhỏ với O2, C2 trong thân n1; n3 **đóng thấp hơn** (màu bất kỳ) | Tăng | Đảo chiều 60% / hạng 56 / tần suất 33 |
| **Three outside up** | n1 đen; n2 trắng **mở dưới thân n1 và đóng trên thân n1** (engulfing); n3 đóng cao hơn | Giảm | Đảo chiều 75% / hạng 34 / tần suất 24 |
| **Three outside down** | n1 trắng; n2 đen mở cao hơn và đóng thấp hơn thân n1; n3 đóng thấp hơn | Tăng | Đảo chiều 69% / hạng 39 / tần suất 21 |
| **Rising three methods (Tăng ba phương pháp)** — 5 nến | n1 trắng cao; n2–n4 là 3 nến nhỏ **đi xuống**, **n2 và n4 đen, n3 màu bất kỳ**, đóng cửa trong biên độ cao–thấp của n1; n5 trắng cao **đóng cửa trên C1** [Bulkowski; Nison tương tự]. Code: `is_white(1) and is_tall(1)`, `is_black(2) and is_black(4)`, `C2 > C3 > C4` (hoặc hồi quy giảm), `L1 <= C_k <= H1` với k = 2..4, `is_white(5) and is_tall(5)`, `C5 > C1` | Tăng | **Tiếp diễn tăng 74%** / hạng 94 / tần suất 88 (n = 102) |
| **Falling three methods** — 5 nến | n1 đen cao; 3 nến trắng nhỏ đi lên (nến giữa có thể đen), nằm trong biên độ n1; n5 đen cao **đóng dưới C1** | Giảm | **Tiếp diễn giảm 71%** / hạng 89 / tần suất 91 |
| **Bullish abandoned baby (Em bé bị bỏ rơi — tăng)** | n1 đen; n2 **doji gap xuống dưới bóng của cả hai nến bên cạnh** (`H2 < L1` và `H2 < L3`); n3 trắng | Giảm | Đảo chiều 70% / hạng **9** / tần suất 92 (hiếm) |
| **Bearish abandoned baby** | n1 trắng; n2 doji với **bóng dưới trên đỉnh n1** (`L2 > H1`); n3 đen với **bóng trên dưới đáy doji** (`H3 < L2`) | Tăng | Đảo chiều 69% / hạng 64 / tần suất 96 (hiếm) |

**Top-10 của Bulkowski** (trang CandlePerformers) [TRANG GỐC] gồm: three line strike (giảm/tăng), three black crows, evening star, upside tasuki gap, inverted hammer, matching low, bullish abandoned baby, two black gapping, bearish breakaway. Lưu ý: xếp hạng "hiệu suất tổng thể" đo **mức độ di chuyển sau breakout**, khác với tỷ lệ đảo chiều %. Mâu thuẫn nhỏ trên chính trang Bulkowski: trang CandlePerformers xếp bearish breakaway thứ 10, nhưng trang riêng của mẫu này ghi hạng **11**, và trang Two black gapping ghi hạng **10** → dùng số trên trang riêng từng mẫu.

### 4.4 Các mẫu hình top-10 và mẫu bổ sung (định nghĩa hình học)

| Mẫu hình | Định nghĩa (Bulkowski, nguyên văn đã dịch) → code | Xu hướng trước | Bulkowski |
|---|---|---|---|
| **Bearish three-line strike** (4 nến) | Ba nến đen với **đáy thấp dần**, tiếp theo một nến trắng cao **mở cửa dưới giá đóng cửa trước** và **đóng cửa trên giá mở cửa của nến đầu tiên**: `is_black(1) and is_black(2) and is_black(3)`, `L1>L2>L3`, `is_white(4) and is_tall(4)`, `O4 < C3`, `C4 > O1` | Giảm | **Đảo chiều tăng 84%** (lý thuyết: tiếp diễn giảm) / hạng **1** / tần suất 94 (**n = 85**) — ⚠ cỡ mẫu nhỏ, Bulkowski tự cảnh báo |
| **Bullish three-line strike** (4 nến) | Ba nến trắng, mỗi nến **đóng cao hơn**; nến đen cao **mở cao hơn** nhưng **đóng dưới giá mở của nến đầu tiên**: `is_white(1) and is_white(2) and is_white(3)`, `C1<C2<C3`, `is_black(4) and is_tall(4)`, `O4 > C3`, `C4 < O1` | Tăng | **Đảo chiều giảm 65%** (lý thuyết: tiếp diễn tăng) / hạng **2** / tần suất 95 (**n = 69**) — ⚠ cỡ mẫu nhỏ |
| **Upside tasuki gap** (3 nến) | Bulkowski (và Nison): nến trắng trong xu hướng tăng, rồi nến trắng thứ hai **gap lên, có khoảng trống giữa bóng hai nến**; nến thứ ba là nến đen **mở trong thân nến 2** và **đóng trong khoảng gap** (không lấp gap): `is_white(1) and is_white(2)`, `L2 > H1`, `is_black(3)`, `min(O2,C2) < O3 < max(O2,C2)`, `H1 < C3 < L2` | Tăng | Tiếp diễn tăng 57% ("gần ngẫu nhiên") / hạng **5** / tần suất 74 (**n = 704**) |
| **Matching low** (2 nến) | Nến đen thân cao, tiếp theo nến đen có **giá đóng cửa (không phải giá thấp nhất) bằng giá đóng cửa trước**: `is_black(1,2)`, `abs(C2-C1) <= equal_tol` | Giảm | **Tiếp diễn giảm 61%** (lý thuyết: đảo chiều tăng) / hạng **8** / tần suất 58 |
| **Two black gapping** (2 nến + gap) | Một **gap giá xuống**, tiếp theo hai nến đen; nến đen thứ hai có **đỉnh thấp hơn đỉnh nến trước**: `H1 < L0` (gap so với nến trước pattern), `is_black(1,2)`, `H2 < H1` | Giảm | Tiếp diễn giảm 68% / hạng **10** / tần suất 29 |
| **Bearish breakaway** (5 nến) | Nến 1 trắng cao; nến 2 **trắng**, **gap lên**; nến 3 đóng cao hơn (màu bất kỳ); nến 4 **trắng**, đóng cao hơn; nến 5 **đen cao**, **đóng cửa trong khoảng gap giữa thân nến 1 và nến 2**: `is_white(1) and is_tall(1)`, `is_white(2)`, `min(O2,C2) > C1`, `C3 > C2`, `is_white(4) and C4 > C3`, `is_black(5) and is_tall(5)`, `C1 < C5 < min(O2,C2)` | Tăng | Đảo chiều giảm 63% (89% trong thị trường gấu) / hạng **11** / tần suất 98 |
| **Bullish belt hold** (1 nến) | Nến trắng **không có bóng dưới** (`O == L`), **đóng gần đỉnh** | Giảm | Đảo chiều tăng 71% / hạng 62 / tần suất 22 |
| **Bearish belt hold** (1 nến) | **Mở cửa tại đỉnh phiên** (`O == H`), **đóng gần đáy**, nến đen cao, thường có bóng dưới nhỏ | Tăng | Đảo chiều giảm 68% / hạng 63 / tần suất 19 |
| **Mat hold** (5 nến) | Nến trắng cao; ngày 2: nến đen nhỏ **đóng cao hơn** (`C2 > C1`; theo Nison thường gap lên — Bulkowski chỉ nêu "higher close"); ngày 3: nến nhỏ màu bất kỳ; ngày 4: nến đen nhỏ; ngày 2–4 đi xuống nhưng **thân nằm trên đáy nến 1**; ngày 5: nến trắng cao **đóng trên đỉnh của 4 nến trước** | Tăng | Tiếp diễn tăng 78% / hạng 86 / tần suất 93 (52 mẫu) |

`is_tall(i)`: `rng_i > tall_candle_mult × avg_rng(22 phiên trước nến i)`, mặc định `tall_candle_mult = 1.46` (Bulkowski, mục 4.0). **Cảnh báo cỡ mẫu**: hạng 1 và 2 của hai mẫu three-line strike dựa trên **85 và 69 mẫu** trong ~4,7 triệu nến. Bulkowski tự lưu ý các hạng đầu này đến từ mẫu nhỏ. Mọi chỗ skill trích "top performer" phải hiển thị kèm cảnh báo này.

"Đóng gần đỉnh/đáy" và "không có bóng": dùng ngưỡng TA-Lib `ShadowVeryShort` (`< 0.1 × avg_rng(10)`) thay cho phép so sánh bằng tuyệt đối [TRIỂN KHAI]. Các URL: `ThreeLineStrikeBear.html`, `ThreeLineStrikeBull.html`, `UpsideTasukiGap.html`, `MatchingLow.html`, `TwoBlackGapping.html`, `BearBreakaway.html`, `BeltHoldBull.html`, `BeltHoldBear.html`, `MatHold.html` trên thepatternsite.com.

---

