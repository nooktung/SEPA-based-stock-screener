# Chỉ báo: MA20/50/150/200, MACD, RSI, Volume

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

## 5. Chỉ báo

### 5.1 Đường trung bình động (Moving Averages)

| MA | Tương đương | Vai trò | Nguồn |
|---|---|---|---|
| **MA20** | ≈ 1 tháng giao dịch (4 tuần) | Xu hướng ngắn hạn; Minervini: cổ phiếu sau breakout nên **giữ được MA20** | [SÁCH] TLSMW qua Elearnmarkets |
| **MA50** | ≈ **10 tuần** | Xu hướng trung hạn; TT4, TT5; handle nên trên MA50 (O'Neil) | [SÁCH] |
| **MA150** | ≈ **30 tuần** | Đường chính trong Stage Analysis của Weinstein; TT1, TT2 | [SÁCH] TTLC; Weinstein (1988) |
| **MA200** | ≈ **40 tuần** | Xu hướng dài hạn; TT1–TT3; đường phân chia Stage 1/2/3/4 | [SÁCH] |

**Tại sao 150/200**: Minervini dùng MA150 (≈ MA30 tuần của Weinstein) và MA200 (≈ 40 tuần) để xác nhận xu hướng dài hạn đã chuyển sang tăng; yêu cầu thứ tự `close > SMA50 > SMA150 > SMA200` và MA200 dốc lên để loại cổ phiếu vừa bật khỏi đáy (Stage 1) hoặc đang tạo đỉnh (Stage 3) [SÁCH — diễn giải].

**SMA vs EMA — công thức khớp TradingView** [TRANG GỐC/THỨ CẤP]:
- **SMA** (TradingView `ta.sma`, chỉ báo "MA" mặc định): `SMA_t = mean(close[t-n+1 .. t])`. pandas: `close.rolling(n).mean()` — khớp chính xác.
- **EMA** (`ta.ema`): `alpha = 2/(n+1)`; `EMA_t = alpha*x_t + (1-alpha)*EMA_{t-1}`. **Khởi tạo**: mã Pine tham chiếu (tradingcode.net) khởi tạo bằng giá trị nguồn đầu tiên (`na(sum[1]) ? src`), tương đương `pandas.ewm(span=n, adjust=False)`. Một số tài liệu khác khởi tạo bằng SMA(n) — **CHƯA GIẢI QUYẾT (D15)**: built-in thực sự dùng cách nào không xác minh được; khác biệt tắt dần sau ~3–4×n thanh. Khuyến nghị: chỉ so sánh với TradingView **sau ≥ 3×n thanh**; **không bao giờ kiểm tra khớp MACD trong ~100 thanh đầu** — đường signal của MACD là EMA của một EMA nên thừa hưởng cùng sai lệch khởi tạo (EMA26 cần ~78 thanh, thêm EMA9 của nó).
- **RMA / Wilder** (`ta.rma`): `alpha = 1/n`; khởi tạo bằng `SMA(n)` tại thanh thứ n; sau đó `RMA_t = alpha*x_t + (1-alpha)*RMA_{t-1}` [THỨ CẤP — tradingcode.net, pineify, trích mã Pine].

### 5.2 MACD

**Tác giả**: Gerald Appel, cuối thập niên 1970. **Histogram** do Thomas Aspray thêm năm **1986** [TRANG GỐC — StockCharts; THỨ CẤP — Aspray, TASC V.6:8].

**Công thức (TradingView mặc định: fast 12, slow 26, signal 9; oscillator MA type = EMA, signal MA type = EMA)**:
```
macd_line  = EMA(close, 12) - EMA(close, 26)
signal     = EMA(macd_line, 9)
histogram  = macd_line - signal
```

| Tín hiệu | Định nghĩa code |
|---|---|
| Cắt đường tín hiệu (signal cross) | Tăng: `macd[t-1] <= signal[t-1] and macd[t] > signal[t]`; giảm: ngược lại |
| Cắt đường 0 (zero-line / centerline) | `macd` đổi dấu ⇔ EMA12 cắt EMA26 |
| Histogram | Dương/âm và độ dốc; histogram thu hẹp báo trước cắt tín hiệu (mục đích của Aspray) |
| Phân kỳ (divergence) | Tăng: giá đáy thấp hơn, MACD đáy cao hơn; giảm: giá đỉnh cao hơn, MACD đỉnh thấp hơn. Cần thuật toán xác định swing (pivot N-bar) [TRIỂN KHAI] |

**Điểm yếu** [TRANG GỐC — StockCharts]: MACD **không tốt cho xác định quá mua/quá bán**; trong các đợt biến động mạnh, MACD có thể vượt xa cực trị lịch sử; giá trị MACD phụ thuộc mức giá tuyệt đối nên **không so sánh được giữa các cổ phiếu** (dùng PPO nếu cần so sánh). Ngoài ra: chỉ báo trễ (lagging), nhiều tín hiệu nhiễu khi thị trường đi ngang [THỨ CẤP, kiến thức chung].

> Ghi chú hệ thống: vì MACD phụ thuộc mức giá, dùng giá **cùng thang đơn vị** (xem cảnh báo đơn vị trong CLAUDE.md); để so sánh chéo mã dùng `macd_line / EMA26` (≈ PPO).

### 5.3 RSI

**Tác giả**: J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978).

**Công thức (TradingView mặc định: length 14, source close, smoothing RMA)** [TRANG GỐC — StockCharts, TradingView Help]:
```
change = close - close.shift(1)
gain   = max(change, 0);  loss = max(-change, 0)
avg_gain_14 (lần đầu) = SMA(gain, 14);  avg_loss_14 (lần đầu) = SMA(loss, 14)
sau đó: avg_gain = (avg_gain_prev*13 + gain)/14    # = RMA, alpha = 1/14
        avg_loss = (avg_loss_prev*13 + loss)/14
RS  = avg_gain / avg_loss
RSI = 100 - 100/(1 + RS)
# biên: avg_loss == 0 -> RSI = 100; avg_gain == 0 -> RSI = 0
# (script RSI built-in của TradingView: down == 0 ? 100 : up == 0 ? 0 : 100 - 100/(1 + up/down) — xác nhận ở phản biện vòng 1)
```
Lưu ý: `pandas.ewm(alpha=1/14, adjust=False)` khởi tạo bằng giá trị đầu, **không** bằng SMA — sẽ lệch TradingView ở đầu chuỗi; triển khai RMA thủ công với khởi tạo SMA.

| Khái niệm | Quy tắc | Nguồn |
|---|---|---|
| Quá mua/quá bán | > **70** quá mua; < **30** quá bán (một số dùng 80/20) | [TRANG GỐC] Wilder qua StockCharts/TradingView |
| Đường giữa 50 | Quanh 50 = không xu hướng; trên 50 thiên tăng | [TRANG GỐC] TradingView |
| **Dịch chuyển vùng — Constance Brown** | Thị trường tăng: RSI dao động **40–90**, vùng **40–50** là hỗ trợ; thị trường giảm: **10–60**, vùng **50–60** là kháng cự | [TRANG GỐC] StockCharts trích *Technical Analysis for the Trading Professional* |
| **Dịch chuyển vùng — Andrew Cardwell** | Thị trường tăng: **40–80**; thị trường giảm: **20–60** | [THỨ CẤP] — **khác với Brown; ghi cả hai** |
| Phân kỳ | Tăng: giá đáy thấp hơn, RSI đáy cao hơn; giảm: ngược lại | [TRANG GỐC] |
| Cardwell về phân kỳ | Phân kỳ tăng chủ yếu xuất hiện trong **xu hướng giảm**, phân kỳ giảm trong **xu hướng tăng** → phân kỳ thường báo điều chỉnh ngắn chứ không phải đảo chiều | [TRANG GỐC] TradingView Help |
| Positive/negative reversal (Cardwell) | Positive: RSI đáy thấp hơn (thường vùng 30–50) trong khi giá đáy cao hơn → tăng. Negative: RSI đỉnh cao hơn (thường 50–70) trong khi giá đỉnh thấp hơn → giảm | [TRANG GỐC] StockCharts |
| Failure swing (Wilder) | Tăng: RSI xuống dưới 30, bật lên trên 30, hồi lại nhưng giữ trên 30, rồi vượt đỉnh trước. Giảm: đối xứng quanh 70 | [TRANG GỐC] StockCharts |

**Điểm yếu**: trong xu hướng mạnh RSI có thể ở vùng quá mua/quá bán rất lâu (lý do Brown/Cardwell đề xuất dịch vùng); phân kỳ có thể kéo dài nhiều lần trước khi giá đảo chiều [TRANG GỐC/THỨ CẤP].

### 5.4 Khối lượng (Volume)

| Khái niệm | Quy tắc đo được | Nguồn |
|---|---|---|
| Volume MA | `vol_sma50 = SMA(volume, 50)` là chuẩn so sánh của IBD cho breakout; `SMA(volume, 20)` cũng phổ biến | [TRANG GỐC] IBD (40% trên trung bình 50 ngày cho flat base) |
| Khối lượng breakout | `volume_breakout >= 1.4–1.5 × vol_sma50` | [TRANG GỐC] IBD; [THỨ CẤP] LuxAlgo |
| Cạn kiệt khối lượng (volume dry-up, VDU) | Khối lượng giảm mạnh trong vùng co hẹp cuối/handle/vùng pause của 3C. **Không có ngưỡng số chính thức của Minervini** — đề xuất `volume < 0.5 × vol_sma50` trong ≥ 1–3 phiên gần pivot (finermarketpoints nêu 40–60% trung bình 50 ngày, không ghi nguồn gốc) | [SÁCH] định tính; ngưỡng **[TRIỂN KHAI], UNVERIFIED** |
| Ngày phân phối / stalling / FTD (thị trường) | Xem mục 3.5 (định nghĩa, ngưỡng 5–6 ngày, hết hiệu lực 5% đóng cửa vs 6% intraday) | [TRANG GỐC]/[THỨ CẤP] |
| Ngày tích lũy/phân phối (cổ phiếu) | Tăng giá với volume > vol_sma50 = tích lũy; giảm giá với volume > vol_sma50 = phân phối. Stage 2: volume tăng khi giá tăng, co lại khi điều chỉnh | [SÁCH] Minervini (định tính); định nghĩa code **[TRIỂN KHAI]** |
| Pocket pivot (tham khảo — Morales & Kacher, *Trade Like an O'Neil Disciple*, 2010) | Phiên tăng có volume > volume lớn nhất của các phiên **giảm** trong 10 phiên trước; trong nền xây dựng hoặc gần MA10/MA50, không bị kéo giãn (xem 3.2b) | [THỨ CẤP] — S&C V.30:13, LuxAlgo (xác nhận ở phản biện vòng 1); không phải quy tắc Minervini |

---

