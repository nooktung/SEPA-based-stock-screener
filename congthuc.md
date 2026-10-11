# Công thức và cách tính của toàn hệ thống

Bản tổng hợp để duyệt, viết ngày 06/10/2026 từ đúng code và cấu hình đang chạy
(`src/quant/`, `src/collectors/`, `config/thresholds.yaml`). Mọi con số ngưỡng dưới đây nằm trong
`config/thresholds.yaml`: đổi ở đó là hệ thống đổi theo, không cần sửa code.

**Nhãn nguồn** đi kèm từng quy tắc:

| Nhãn | Nghĩa |
|---|---|
| **[SÁCH]** | Có trong sách gốc (Minervini, O'Neil), đã kiểm qua tóm tắt độc lập |
| **[TRANG GỐC]** | Từ trang của tác giả / nhà phát hành (IBD, TradingView, TA-Lib…) |
| **[THỨ CẤP]** | Chỉ có nguồn thứ cấp |
| **[TRIỂN KHAI]** | Cách đo do mình chọn để biến một quy tắc định tính thành con số — **không phải phương pháp gốc** |
| **[CHUẨN]** | Định nghĩa toán học / chỉ báo chuẩn, có tác giả công bố |

**Ký hiệu**: O, H, L, C, V = giá mở, cao, thấp, đóng cửa, khối lượng của một phiên; C(−n) = giá đóng cửa
n phiên trước; TB = trung bình; "phiên" = một ngày giao dịch.

---

## Mục lục

1. [Dữ liệu giá](#1-dữ-liệu-giá)
2. [Đường trung bình và chỉ báo nền tảng](#2-đường-trung-bình-và-chỉ-báo-nền-tảng)
3. [Thanh khoản](#3-thanh-khoản)
4. [RS Rating](#4-rs-rating)
5. [Trend Template 8 tiêu chí](#5-trend-template-8-tiêu-chí)
6. [Nền giá, pivot, stop](#6-nền-giá-pivot-stop)
7. [Rủi ro](#7-rủi-ro)
8. [Phễu lọc, chia nhóm A/B/C, xếp hạng](#8-phễu-lọc-chia-nhóm-abc-xếp-hạng)
9. [Cơ bản (BCTC quý)](#9-cơ-bản-bctc-quý)
10. [Ngành, sóng ngành, đầu ngành](#10-ngành-sóng-ngành-đầu-ngành)
11. [Trạng thái thị trường](#11-trạng-thái-thị-trường)
12. [Trạng thái của từng mã (3 câu hỏi)](#12-trạng-thái-của-từng-mã-3-câu-hỏi)
13. [Sự kiện theo dõi hằng ngày](#13-sự-kiện-theo-dõi-hằng-ngày)
14. [Chỉ báo kỹ thuật kiểu TradingView](#14-chỉ-báo-kỹ-thuật-kiểu-tradingview)
15. [Mẫu nến](#15-mẫu-nến)
16. [Biểu đồ: vùng giá, trendline, kênh, khung tuần/tháng](#16-biểu-đồ-vùng-giá-trendline-kênh-khung-tuầntháng)
17. [% thay đổi và giá tham chiếu](#17--thay-đổi-và-giá-tham-chiếu)
18. [Khối ngoại và khối nội](#18-khối-ngoại-và-khối-nội)
19. [Kiểm tra vị thế](#19-kiểm-tra-vị-thế)
20. [Toàn thị trường](#20-toàn-thị-trường)
21. [Có trong cấu hình nhưng chưa dùng](#21-có-trong-cấu-hình-nhưng-chưa-dùng)

---

## 1. Dữ liệu giá

**Nguồn**: DNSE OpenAPI (`/price/ohlc`), một lượt gọi cho cả lịch sử ~11 năm (từ 15/09/2015).

**Đơn vị**: DNSE trả giá theo **nghìn đồng** (24,35 = 24.350 đ); code nhân với hệ số cấu hình 1.000, không
bao giờ đoán đơn vị theo độ lớn con số. Giá trị giao dịch khối ngoại là **đồng**; chỉ số là **điểm**.

**Điều chỉnh quyền**: lịch sử của DNSE đã được điều chỉnh cho các đợt chia / thưởng **cổ phiếu**
(không điều chỉnh cổ tức tiền mặt). DNSE điều chỉnh lại **toàn bộ lịch sử vào tối trước ngày GDKHQ**.

**Kiểm tra lịch sử bị điều chỉnh lại**: mỗi lần tải, đọc lại 15 ngày đã lưu; nếu một giá đóng cửa đã lưu
lệch giá mới > 0,5% → lịch sử đã bị điều chỉnh → tải lại toàn bộ chuỗi và thay thế.
Hệ số điều chỉnh = trung vị (giá mới / giá cũ) trên các phiên chung; được ghi vào bảng `price_adjustments`
cùng **phiên cuối còn ở thang cũ** (dùng ở mục 17).

**Kiểm tra nến hôm nay đã chốt chưa** (so với dữ liệu khớp lệnh): nến bị coi là chưa chốt nếu
- giá đóng cửa lệch giá khớp cuối > 0,05%, hoặc cao của nến < cao trong ngày, hoặc thấp của nến > thấp trong ngày;
- **trừ khi** O, H, L, C của nến đều bằng giá khớp × **cùng một hệ số** (lệch nhau ≤ 0,5%, hệ số khác 1
  quá 0,5%): đó là nến đã điều chỉnh trước ngày GDKHQ → giữ lại.

**Kiểm tra nhảy giá bất thường** (300 phiên gần nhất): một phiên không thể vượt biên độ:

| Sàn | Biên độ | Giới hạn đóng cửa → đóng cửa |
|---|---|---|
| HOSE | ±7% | ±7,5% (biên độ + 0,5 điểm cho làm tròn bước giá) |
| HNX | ±10% | ±10,5% |
| UPCoM | ±15% | −37,2% … +55,6% (tham chiếu là giá bình quân phiên trước: (1−b)²/(1+b)−1 … (1+b)²/(1−b)−1) |

Vượt → chuỗi bị gắn cờ **"dữ liệu cần kiểm tra"** và bị loại khỏi việc lọc (dữ liệu vẫn giữ, không sửa).

**Bước giá** (dùng cho pivot, làm tròn): HOSE < 10.000 đ: 10 đ; 10.000–49.950 đ: 50 đ; ≥ 50.000 đ: 100 đ.
HNX, UPCoM: 100 đ.

---

## 2. Đường trung bình và chỉ báo nền tảng

| Tên | Công thức | Nguồn |
|---|---|---|
| **SMA n** | trung bình cộng n giá đóng cửa gần nhất | [CHUẨN] |
| **EMA n** | EMA = EMA(hôm qua) + α·(C − EMA(hôm qua)), α = 2/(n+1); khởi đầu từ giá đầu tiên | [CHUẨN] |
| **WMA n** | Σ(Cᵢ·wᵢ)/Σwᵢ với trọng số 1, 2, …, n (phiên mới nhất = n) | [CHUẨN] |
| **Làm mượt Wilder n** | giá trị đầu = trung bình cộng n giá trị đầu; sau đó: TB = (TB cũ·(n−1) + giá trị mới)/n | Wilder 1978 |
| **True Range** | max(H − L, \|H − C(−1)\|, \|L − C(−1)\|) | Wilder 1978 |
| **RSI 14** | thay đổi = C − C(−1); TB tăng, TB giảm = làm mượt Wilder 14 của phần tăng / phần giảm; RS = TB tăng / TB giảm; **RSI = 100 − 100/(1 + RS)**. Toàn tăng = 100; 14 phiên đi ngang hoàn toàn = không xác định (không gán 50) | Wilder 1978 |
| **MACD (12, 26, 9)** | MACD = EMA12 − EMA26; Tín hiệu = EMA9 của MACD; Histogram = MACD − Tín hiệu | Appel |

Bốn đường MA trên biểu đồ ngày: **MA20, MA50, MA150, MA200** (đều là SMA). Đã kiểm tra lại bằng phép tính
tay riêng ngày 04/10/2026 (14 mã + VN-Index): khớp, chỉ lệch do làm tròn.

---

## 3. Thanh khoản

```
Giá trị giao dịch TB = trung bình 20 phiên của (C × V)          [TRIỂN KHAI — DNSE không trả giá trị giao dịch]
Tỷ lệ phiên có giao dịch = số phiên có V > 0 / 60 phiên gần nhất
```

- **Ngưỡng lọc**: Giá trị TB ≥ **3 tỷ đ** (cả 3 sàn) **và** tỷ lệ phiên có giao dịch ≥ **90%**.
- Bộ lọc chỉ nạp ~1,5 năm gần nhất. Mã có < 252 phiên có giá trong khoảng đó: nếu tổng lịch sử lưu ≥ 252 phiên thì ghi
  **"giao dịch thưa / tạm ngừng"**, còn lại ghi **"mới niêm yết"** (trước 11/10/2026 cả hai cùng ghi "lịch sử < 252 phiên").
- **Cờ dữ liệu cần kiểm tra**: biến động một phiên vượt biên độ sàn trong 300 phiên gần nhất. Các phiên **trước và đúng
  ngày lên sàn hiện tại** (`listed_date`) dùng biên độ UPCoM (rộng nhất), vì mã đã giao dịch ở sàn khác (MCH, BVB, VVS
  từng bị gắn cờ oan). Lý do gắn cờ được lưu và hiển thị.
- **Nhãn hạng** (chỉ là nhãn, không ảnh hưởng nhóm hay điểm): **Cao** ≥ 10 tỷ; **Khá** 8–10 tỷ; **Thấp** 3–8 tỷ.

---

## 4. RS Rating

Độ mạnh giá so với **mọi mã đủ dữ liệu** (~785 mã, kể cả mã dưới ngưỡng thanh khoản) — giống IBD xếp trên toàn thị
trường, nên RS 90 là top 10% thị trường (lựa chọn của bạn 11/10/2026; trước đó chỉ xếp trong ~186 mã đủ thanh khoản).
Công thức của IBD là bí mật → dùng bản tái tạo [THỨ CẤP]:

```
Biến thể A (đang dùng):  điểm thô = 0,4·R(63) + 0,2·R(126) + 0,2·R(189) + 0,2·R(252)
                         với R(n) = C / C(−n) − 1     (63 phiên ≈ 3 tháng … 252 ≈ 12 tháng)
Biến thể B (dự phòng):   2·Q1 + Q2 + Q3 + Q4   (lợi nhuận từng quý riêng, quý gần nhất ×2)

RS = thứ hạng phần trăm của điểm thô trong nhóm mã đủ dữ liệu, làm tròn, giới hạn 1…99
Mọi trang (Danh sách, Toàn thị trường, chi tiết) dùng RS của cùng một lượt lọc; bảng Toàn thị trường dựng lại
sau mỗi lượt lọc mới và ghi rõ RS của lượt nào.
```

- Cần ≥ 253 phiên lịch sử; thiếu thì không có RS.
- **Tầng RS**: ≥ 90 "Dẫn dắt mạnh"; 80–89 "Dẫn dắt"; 70–79 "Đạt" [SÁCH: Minervini lấy 70 làm sàn, thích 80–90+].

---

## 5. Trend Template 8 tiêu chí

52 tuần = **250 phiên** (Việt Nam có ~245–250 phiên/năm). Cần ≥ 252 phiên lịch sử.

| Mã | Điều kiện đạt | Giá trị hiển thị | Nguồn |
|---|---|---|---|
| **TT1** | C > MA150 **và** C > MA200 | (C / max(MA150, MA200) − 1)·100% | [SÁCH] |
| **TT2** | MA150 > MA200 | (MA150/MA200 − 1)·100% | [SÁCH] |
| **TT3** | MA200 hôm nay > MA200 của **22 phiên trước** (≥ 1 tháng đi lên) | % thay đổi MA200 trong 22 phiên | [SÁCH] (sách thích 4–5 tháng = 105 phiên: chỉ hiển thị, không bắt buộc) |
| **TT4** | MA50 > MA150 **và** MA50 > MA200 | (MA50 / max(MA150, MA200) − 1)·100% | [SÁCH] |
| **TT5** | C > MA50 | (C/MA50 − 1)·100% | [SÁCH] |
| **TT6** | C cao hơn đáy 52 tuần ≥ **30%** | (C / đáy 52 tuần − 1)·100% | [SÁCH] — đang tranh cãi: 30% (sách 2013) hay 25% (sách 2017) |
| **TT7** | C cách đỉnh 52 tuần ≤ **25%** | (1 − C / đỉnh 52 tuần)·100% | [SÁCH] |
| **TT8** | RS ≥ **70** | RS | [SÁCH] |

Đỉnh / đáy 52 tuần lấy từ giá **cao / thấp** của nến, không phải giá đóng cửa.
Mã mới niêm yết (< 252 phiên): chỉ đo được TT7 (so với đỉnh sau niêm yết); các tiêu chí khác ghi "chưa đo được",
không tính là trượt.

---

## 6. Nền giá, pivot, stop

Tất cả cách đo nền là **[TRIỂN KHAI]** của các mô tả trong sách (Minervini, O'Neil, IBD).

### 6.1 Xác định nền (quy tắc nền 2, từ 11/10/2026 theo báo cáo kiểm thử lần 2)

1. Cửa sổ: **325 phiên** gần nhất (65 tuần). **Đỉnh nền** = giá cao nhất trong cửa sổ, **bỏ phiên giật** [TRIỂN KHAI]:
   một phiên mở cửa cách giá cao nhất ≤ 1 bước giá, đóng cửa ở nửa dưới biên độ phiên và có giá cao vượt mọi phiên
   trong ±10 phiên ít nhất 3% được tính bằng **giá đóng cửa** khi tìm đỉnh (ABB 09/07/2026: mở cửa giá trần 18.900, đóng
   17.200). Phiên đã bỏ được ghi trên giao diện.
2. **Ngày bắt đầu nền = đỉnh trái** [TRIỂN KHAI]: lùi từ đỉnh nền về phiên sớm nhất có giá cao ≥ đỉnh × (1 − 3%) **và**
   sau phiên đó giá đã điều chỉnh ≥ 3% trước khi lên đỉnh; dừng lùi khi nền tính từ đó sâu hơn 33% (độ sâu tối đa của
   cốc). Ví dụ ABB: nền bắt đầu 10/07 (18.000), đỉnh 18.200 ngày 18/09. Đỉnh 18.000 một năm trước rồi giảm 40% và hồi
   lên 18.200 là hai nền, không lùi.
3. Nếu nền < **15 phiên** (đỉnh mới trong 3 tuần): pivot = đỉnh + 1 bước giá; khoảng cách = C/pivot − 1:
   - > +5% → **EXTENDED** (đã chạy xa)
   - < −10% → **SHARP_PULLBACK** (vừa lập đỉnh rồi giảm mạnh)
   - còn lại → **NEAR_HIGH** (sát đỉnh, chưa có nền)
4. Nếu nền ≥ 15 phiên:
   - Đáy nền = giá thấp nhất trong nền (tính từ ngày bắt đầu); **độ sâu** = (1 − đáy/đỉnh)·100%.
   - **Nhịp co hẹp chính** (đo từ đỉnh nền): H₀ = đỉnh; Lₖ = đáy thấp nhất sau Hₖ; Hₖ₊₁ = đỉnh cao nhất sau Lₖ … Một
     nhịp được tính khi sâu ≥ **3%** và dài ≥ **2 phiên** (đỉnh và đáy khác phiên). Dừng khi nhịp tiếp theo nông hơn 3%.
     Đỉnh thấp dần, đáy cao dần, nên mỗi nhịp không sâu hơn nhịp trước (trước đây đếm mọi dao động 3% kiểu zigzag: KSV
     37 "nhịp"). Đỉnh kế tiếp là **lần cuối** giá chạm mức cao nhất đó, nên đỉnh luôn thấp hơn hẳn: mã giá thấp dao động
     giữa hai bước giá (VHG 1.600đ, bước 100đ) là 1 nhịp, không phải 18. Toàn thị trường: tối đa 6 nhịp; 0 nhịp khi nền
     bắt đầu từ đỉnh trái và từ đỉnh cao nhất giá chưa điều chỉnh 3%.
   - **Vùng chặt cuối**: sau nhịp chính cuối, đoạn điều chỉnh < 3% kéo dài ≥ **3 phiên** và có đỉnh ở **nửa trên** của
     nền được tính là nhịp cuối cùng. Ở nửa dưới thì không (đó là nhịp hồi trong nền: HCM 23.750 nằm dưới đỉnh trái 13%).
   - **Pivot** = đỉnh của nhịp cuối (vùng chặt nếu có) + 1 bước giá [skill 02 §3.1: "pivot = max(high) của nhịp co hẹp
     cuối"]; nếu không có nhịp nào: đỉnh nền + 1 bước giá. Trước 11/10/2026: giá cao nhất kể từ đáy nhịp cuối.
   - **Cạn khối lượng** = TB khối lượng 5 phiên < **0,7 ×** TB 50 phiên [UNVERIFIED].
   - **Stop** = max(đáy nhịp cuối, pivot × (1 − 8%)) [SÁCH: stop tối đa 7–8%]; đáy nhịp cuối là đáy vùng chặt khi pivot
     lấy từ vùng chặt.
   - Pivot **làm tròn lên**; stop **làm tròn xuống** nhưng **không thấp hơn pivot × 0,92 làm tròn lên**, nên rủi ro từ
     pivot luôn ≤ 8% (trước đây làm tròn xuống cả mức pivot −8%: ABB 8,42%).
   - **Chỉ nền đang hình thành** (VCP, nền phẳng, cốc, cốc tay cầm, nền) **có stop**; EXTENDED, NEAR_HIGH,
     SHARP_PULLBACK, TOO_DEEP, BROKEN không có (không có điểm mua ở pivot thì stop không bảo vệ gì — lựa chọn của bạn
     11/10/2026).
   - Nếu C > pivot + 5% → **EXTENDED**; nếu C < đáy nền → **BROKEN** (thủng nền).
5. **Cung treo** (thông tin, không đổi nhóm): đỉnh nền / pivot − 1. Từ **5%** ghi thông tin, từ **10%** cảnh báo
   (với VCP và cốc tay cầm, pivot thấp hơn đỉnh trái 3–8% là bình thường).

### 6.1b Giữ pivot / stop sau sự kiện [TRIỂN KHAI] (sửa theo báo cáo kiểm thử 11/10/2026)

Nền được đọc lại mỗi phiên, nên phiên breakout trở thành đỉnh mới và pivot "leo" lên trên giá vừa breakout
(GMD breakout qua 52.770 ngày 07/10 rồi danh sách ghi pivot 55.100); sau khi thủng stop, stop mới bị tính xuống ngay
giá hiện tại (VIC). Vì vậy:

```
Nếu trong 5 phiên gần nhất có phiên i mà (nền đọc tới phiên i−1 là nền thật: VCP/phẳng/cốc/cốc tay cầm/nền) và
   C(i) > pivot(i−1) ≥ C(i−1)  (breakout)   hoặc   C(i) < stop(i−1) ≤ C(i−1)  (thủng stop)
→ giữ nguyên nền đó (pivot, stop, nhãn) và ghi "giữ từ breakout / phiên thủng stop dd/mm"
   trừ khi đã có một nền mới hình thành bắt đầu sau phiên i.
Một lần đóng cửa vượt pivot của nền có đỉnh nằm ngay trong 5 phiên đó không tính là breakout (nền đọc lại sau phiên
breakout lấy chính phiên đó làm đỉnh).
Đã thủng stop = C hôm nay ≤ stop → hiện "đã thủng stop dd/mm" (ngày đóng cửa xuống tới stop), không hiện "rủi ro
tới stop"; trên biểu đồ stop vẽ nét chấm màu xám.
```
**Giai đoạn chuyển sang quy tắc nền 2:** mã đang giữ pivot / stop tính theo cách cũ (trạng thái lưu không có
`base_rules`) giữ tiếp các số đó tới hết 5 phiên giữ, hoặc tới khi đóng cửa dưới stop (giữ sau breakout), hoặc khi có
sự kiện quyền điều chỉnh giá sau ngày bắt đầu giữ. Bộ kiểm tra bất biến (`python -m src.main check`) xếp các mã này vào
nhóm "đang chuyển".
5 phiên = cửa sổ của nhãn "Vừa breakout" / "Mất hiệu lực", nên nhãn và con số luôn nói về cùng một nền
(`sepa_screen.base.hold_sessions`). Nhóm A/B/C vẫn quyết định theo nền đọc hôm nay.

### 6.2 Gán nhãn nền (thứ tự ưu tiên)

| Nhãn | Điều kiện | Pivot | Stop |
|---|---|---|---|
| **CUP_HANDLE** (cốc tay cầm) | đạt mọi quy tắc cốc (6.3) **và** có tay cầm hợp lệ | đỉnh tay cầm (miệng cốc phải) + 1 bước giá | max(đáy tay cầm, pivot −8%) |
| **VCP** | 2–6 nhịp chính (kể cả vùng chặt cuối); nhịp đầu sâu 10–35%; mỗi nhịp sau ≤ nhịp trước + 1 điểm % (luôn đúng với nhịp chính); nhịp cuối ≤ 10%; **nhịp cuối bắt đầu ở nửa trên của nền** [TRIỂN KHAI] | như 6.1 | như 6.1 |
| **FLAT_BASE** (nền phẳng) | độ sâu ≤ 15% và ≥ 25 phiên [TRANG GỐC IBD] | đỉnh nền + 1 bước giá | max(đáy nền, pivot −8%) |
| **CUP** (cốc chưa có tay cầm) | đạt mọi quy tắc cốc, chưa có tay cầm | đỉnh nền (miệng cốc trái) + 1 bước giá | pivot −8% |
| **BASE** (nền chung) | độ sâu ≤ 33% | như 6.1 | như 6.1 |
| **TOO_DEEP** | sâu hơn 33% | như 6.1 (chỉ để tham khảo) | không có |

Mỗi nhãn được giải thích bằng bộ tiêu chí của chính nó (nền phẳng không còn hiện tiêu chí tay cầm). Nhãn "nền" (BASE) hiện
"chưa đạt mẫu hình chuẩn — kiểm tra theo quy tắc cốc / VCP" và câu hỏi "Nền giá" có dấu ~ thay cho ✓.

### 6.3 Quy tắc hình cốc (chỉ xét khi độ sâu ≤ 33% và nền ≥ 35 phiên)

| Quy tắc | Điều kiện | Nguồn |
|---|---|---|
| Độ sâu | 12–33% | [SÁCH/THỨ CẤP] O'Neil |
| Độ dài | ≥ 35 phiên (~7 tuần) | [SÁCH] |
| Sườn trái (chữ U không phải V) | từ đỉnh xuống tới 1/4 dưới của cốc mất ≥ 15 phiên | [TRIỂN KHAI] |
| Đáy tròn | ≥ 10 phiên đóng cửa trong 1/4 dưới của cốc | [TRIỂN KHAI] |
| Xu hướng tăng trước nền | từ đáy thấp nhất trong 130 phiên trước đỉnh (lấy lần gần nhất nếu bằng nhau) lên đỉnh: tăng ≥ 30% và kéo dài ≥ 20 phiên | [SÁCH] ~30% / [TRIỂN KHAI] cách đo |
| Hồi lên nửa trên | C ≥ giữa cốc = đáy + (đỉnh − đáy)/2; câu chữ nói đúng kết quả ("còn ở nửa dưới …" khi chưa đạt) | [SÁCH] |
| **Tay cầm** | miệng cốc phải = giá cao nhất sau đáy cốc (phải ≥ giữa cốc); tay cầm = các phiên sau đó, ≥ 5 phiên; sâu 0–12% tính từ miệng phải; đáy tay cầm ≥ giữa cốc; TB khối lượng tay cầm < TB khối lượng cả cốc | [THỨ CẤP] / [TRIỂN KHAI] |

### 6.4 Điểm chất lượng nền (chỉ để xếp hạng, 0–100) [TRIỂN KHAI]

```
điểm gốc:  VCP 45 · CUP_HANDLE 40 · FLAT_BASE 35 · CUP 30 · BASE 15   (nhãn khác: 0)
+ 20 nếu cạn khối lượng
+ max(0, 15 − độ sâu nhịp cuối %)
+ 20 × (1 − min(10, |khoảng cách tới pivot nếu âm|)/10)    nếu giá cách pivot ≥ −10%
tối đa 100
```

---

## 7. Rủi ro

```
Rủi ro tới stop      = (pivot − stop) / pivot × 100%     (rủi ro nếu mua ở pivot; để trống khi đã thủng stop)
Lỗ tệ nhất khi kẹt T+2 (từ giá tham chiếu) = 1 − (1 − b)³        b = biên độ sàn
Lỗ tệ nhất nếu mua ở giá trần             = 1 − (1 − b)³ / (1 + b)
```

Ba phiên giảm sàn có thể cộng dồn trước chiều T+2 (phần còn lại của ngày T, ngày T+1, sáng T+2):
HOSE **19,6%**, HNX **27,1%**, UPCoM **38,6%**. Đây không phải giới hạn trên: cổ phiếu trắng bên mua thì không bán được.

---

## 8. Phễu lọc, chia nhóm A/B/C, xếp hạng

### 8.1 Phễu (toàn bộ ~1.663 cổ phiếu thường 3 ký tự trên HOSE/HNX/UPCoM)

1. **Cổng dữ liệu**: có giá; không bị gắn cờ "cần kiểm tra"; ≥ 252 phiên; phiên cuối không cũ hơn 7 ngày.
2. **Thanh khoản** (mục 3).
3. **RS** tính trên các mã còn lại (mục 4).
4. **Trend Template** (mục 5) và **nền giá** (mục 6) cho từng mã.

### 8.2 Nhóm

| Nhóm | Điều kiện |
|---|---|
| **A** | đạt 8/8 tiêu chí |
| **B** | đạt ≥ 6/8 **và** TT8 (RS ≥ 70) **và** đang tạo nền (VCP / FLAT_BASE / CUP_HANDLE / CUP / BASE) |
| bị loại khỏi danh sách chính | A/B có nền vừa gãy (BROKEN, SHARP_PULLBACK) **hoặc** cơ bản FAIL (mục 9) |
| **Mã mới niêm yết** | 20–251 phiên, đủ thanh khoản, phiên cuối ≤ 7 ngày, cách đỉnh sau niêm yết ≤ 25% → đứng đầu nhóm C |
| **C** (theo dõi mở rộng, **không phải ứng viên SEPA**) | RS ≥ 60, đạt ≥ 3/8, đạt **TT1 và TT5** (giá trên MA50, MA150, MA200), nền không phải BROKEN / SHARP_PULLBACK; mã thuộc sóng ngành đang mạnh xếp trước, rồi theo điểm |

Tổng tối đa **80 mã**; **không độn** cho đủ: ít mã đạt thì danh sách ngắn hơn.
Danh sách chính trên web = **A + B**. Nhóm C nằm ở tab riêng.

### 8.3 Xếp hạng trong nhóm

Thứ tự: tầng RS (Dẫn dắt mạnh → Dẫn dắt → Đạt → không xếp tầng), sau đó **điểm tổng hợp** giảm dần [TRIỂN KHAI]:

```
điểm = (35·RS + 20·(số tiêu chí đạt/8·100) + 15·gần_đỉnh + 20·chất_lượng_nền + 10·điểm_cơ_bản)
       / (tổng trọng số của các phần có dữ liệu)

gần_đỉnh      = max(0, 100 − 4 × (% cách đỉnh 52 tuần))      (cách đỉnh 25% → 0 điểm)
điểm_cơ_bản   = A+ 100 · A 80 · B 55 · YẾU 30 · FAIL 0 · không có dữ liệu → bỏ phần này, chia lại trọng số
```

---

## 9. Cơ bản (BCTC quý)

**Nguồn**: CafeF, báo cáo kết quả kinh doanh theo quý (dữ liệu tổng hợp, chưa đối chiếu BCTC gốc).

**Nhận một quý chỉ khi**: có LNST và LNST công ty mẹ; doanh thu thuần > 0; và
`|LNST − (LNST công ty mẹ + cổ đông thiểu số)| ≤ 1% × |LNST|` (loại các mẫu BCTC ngân hàng / chứng khoán
bị CafeF hiển thị sai dòng, thay vì chấm điểm sai).

**Tăng trưởng cùng kỳ** (YoY) = (quý này / cùng quý năm trước − 1)·100%. Năm trước lỗ hoặc bằng 0 → **không xác định**
(không tính là tăng vô hạn). LNST công ty mẹ dùng thay EPS (CafeF để trống EPS).

**Xếp hạng** (cần ≥ 5 quý hợp lệ, theo thứ tự xét):

| Hạng | Điều kiện |
|---|---|
| **FAIL** | lỗ trong quý gần nhất; **hoặc** LNST giảm ≥ 20% YoY; **hoặc** cả LNST và doanh thu cùng giảm |
| không có dữ liệu | thiếu quý cùng kỳ, hoặc tăng trưởng LNST không xác định |
| **A+** | LNST ≥ +25%, doanh thu ≥ +15%, và tăng trưởng LNST **tăng tốc** 3 quý liền (q0 > q−1 > q−2) |
| **A** | LNST ≥ +25% và doanh thu ≥ +10% |
| **B** | LNST ≥ +10% hoặc doanh thu ≥ +10% |
| **YẾU** | còn lại |

**Code 33** (chỉ hiển thị): tăng trưởng LNST, doanh thu và biên lợi nhuận đều tăng tốc 3 quý liền [SÁCH, O'Neil].
FAIL đưa mã A/B ra khỏi danh sách chính. Ngưỡng là [TRIỂN KHAI] (Minervini/O'Neil tìm EPS tăng ≥ 20–25% và tăng tốc).

---

## 10. Ngành, sóng ngành, đầu ngành

Chỉ để hiển thị, trọng số 0 trong điểm (chưa có backtest cho thấy giúp ích ở VN).

- **Phân ngành**: cây ngành của Vietstock, cấp 2 (nhóm ngành).
- **RS ngành** = trung bình **điểm RS thô** của các mã đủ thanh khoản trong ngành (cần ≥ 3 mã), xếp hạng phần trăm
  giữa các ngành (1–99). ≥ 80 = "ngành dẫn dắt".
- **Độ rộng ngành** = % mã trong ngành có C > MA50, C > MA200.
- **Sóng ngành** (`config/themes.yaml`, nhóm do bạn tự định nghĩa): RS nhóm = trung bình RS thô của các mã (cần ≥ 2),
  xếp hạng **chung bảng với các ngành**; ≥ 80 = **đang mạnh**. Kèm trung vị lợi nhuận 1 tháng (21 phiên) và 3 tháng (63 phiên).
  Sóng mạnh chỉ đưa mã lên đầu nhóm C, không bao giờ nâng mã vào A/B.
- **Đầu ngành**: 3 mã đủ thanh khoản có giá trị giao dịch TB lớn nhất mỗi ngành.

---

## 11. Trạng thái thị trường

Tính trên VN-Index.

```
Khối lượng chỉ số  = KL KHỚP LỆNH của phiên (không gồm thỏa thuận)                     [lựa chọn của bạn, 08/10/2026]
                     nguồn: DNSE /price/VNINDEX/market-index, trường contauctAccTrdVol — bằng đúng KL VN-Index
                     của VNDirect (5/5 phiên đối chiếu 08/10/2026). DNSE giữ dữ liệu này từ 25/03/2026.
                     Cặp phiên nào thiếu KL khớp lệnh thì CẢ HAI phiên dùng tổng KL của nến OHLC (có thỏa thuận),
                     không bao giờ so khớp lệnh với tổng KL.
                     cấu hình: sepa_screen.market.index_volume = matched   (total = tổng KL như trước)

Ngày phân phối = phiên chỉ số giảm ≥ 0,2% VÀ khối lượng > phiên trước               [TRANG GỐC IBD]

Một ngày phân phối d bị XOÁ khỏi bộ đếm khi xảy ra điều nào đến trước:                [TRANG GỐC IBD]
  (a) đã quá 25 phiên kể từ d;
  (b) từ sau d, giá CAO trong phiên của chỉ số đạt ≥ C(d) × 1,05                       [đo bằng giá cao: THỨ CẤP]
Số ngày phân phối = số ngày phân phối còn hiệu lực
  cấu hình: sepa_screen.market.distribution_window = 25, distribution_expire_gain_pct = 5,
            distribution_expire_on = high   (close = chỉ tính theo giá đóng cửa, bảo thủ hơn)

CORRECTION      nếu giá < MA200
UPTREND         nếu giá > MA50 > MA200 và số ngày phân phối < 5
UNDER_PRESSURE  nếu số ngày phân phối ≥ 5 hoặc giá < MA50
MIXED           còn lại                                                               [TRIỂN KHAI ngưỡng VN]
```

**Ngày follow-through (FTD)**. Tiêu chí FTD là [TRANG GỐC IBD]; cách xác định "đáy điều chỉnh hiện tại" và máy trạng
thái là [TRIỂN KHAI]. Duyệt **tuần tự từng phiên** trong 90 phiên gần nhất:

```
Trạng thái: CHỜ_ĐÁY → NỖ_LỰC_HỒI_PHỤC → FTD_HIỆU_LỰC → (thất bại / mất hiệu lực) → CHỜ_ĐÁY

đáy_ref   = giá THẤP nhất kể từ mốc bắt đầu của chu kỳ hiện tại
            mốc bắt đầu = đầu cửa sổ, hoặc phiên FTD gần nhất bị thất bại / mất hiệu lực

CHỜ_ĐÁY / NỖ_LỰC_HỒI_PHỤC:
  nếu L < đáy_ref:                        đáy_ref = L; huỷ ngày 1 (nếu có); về CHỜ_ĐÁY
  nếu chưa có ngày 1 và C > C(−1):       phiên này = NGÀY 1 → NỖ_LỰC_HỒI_PHỤC
  nếu đang hồi phục, thứ tự ngày ≥ 4,
     C / C(−1) − 1 ≥ 1,25%, V > V(−1):   phiên này = FTD → FTD_HIỆU_LỰC     (V = khối lượng chỉ số ở trên)

FTD_HIỆU_LỰC:
  nếu L < đáy_ref:                        FTD THẤT BẠI → mốc bắt đầu = phiên này, về CHỜ_ĐÁY   (giá THẤP trong phiên,
                                          lựa chọn của bạn 07/10/2026; cấu hình ftd.undercut_on = low | close)
  nếu số ngày phân phối kể từ FTD ≥ 5:   FTD MẤT HIỆU LỰC → mốc bắt đầu = phiên này, về CHỜ_ĐÁY
  (ngày phân phối ở đây cũng áp dụng quy tắc xoá ở trên)

cấu hình: sepa_screen.market.ftd.lookback_sessions = 90, earliest_day = 4, min_gain_pct = 1.25,
          pressure_distribution_days = 5
```

**Ghi chú thị trường kèm mọi trạng thái của mã**: CORRECTION → "SEPA không mở vị thế mới" (trừ khi có FTD còn hiệu lực:
"thận trọng"); UNDER_PRESSURE → "giảm số lượng và quy mô"; UPTREND → "xu hướng tăng"; MIXED → "chủ yếu theo dõi".

> Lưu ý dữ liệu (09/10/2026): giá cao / thấp trong phiên của nến VN-Index từ DNSE hay lệch số chính thức của HOSE
> (137 phiên gần nhất: giá cao khớp 94, giá thấp khớp 57; market-index của DNSE khớp 135 / 136). Hai quy tắc dùng chúng:
> FTD thất bại (giá thấp) và xoá ngày phân phối khi tăng 5% (giá cao). Theo lựa chọn của bạn, quy tắc **vẫn đọc nến DNSE**;
> `python -m src.main index-check` báo cáo hằng ngày mức lệch và tính lại trạng thái thị trường bằng giá cao / thấp của
> HOSE để bạn xem trước khi quyết (09/10: chỉ ngày đáy của nhịp hồi đổi từ 09/10 sang 08/10).

**Độ rộng thị trường** (chỉ hiển thị, không vào quy tắc nào): số mã tăng / đứng / giảm của HOSE từ cùng phản hồi
market-index; mã tăng đã gồm mã trần, mã giảm đã gồm mã sàn (KL ba nhóm cộng lại bằng tổng KL).

> Đổi định nghĩa khối lượng ngày 08/10/2026: trước đó dùng KL của nến OHLC chỉ số từ DNSE, có ngày bằng khớp lệnh, có ngày
> gồm cả thỏa thuận (+11–40%). Theo KL khớp lệnh, phiên 08/10 có 9 ngày phân phối (theo cách cũ: 8): bỏ 07/09 (KL khớp
> thấp hơn phiên trước, chỉ tổng KL cao hơn nhờ thỏa thuận), thêm 14/09 và 24/09; FTD 03/08 mất hiệu lực ngày 24/09
> (cách cũ: 28/09).

---

## 12. Trạng thái của từng mã (3 câu hỏi)

| Câu hỏi | Trả lời |
|---|---|
| Xu hướng? | A → Đạt; B → Gần đạt; khác → Không |
| Có nền? | Đạt nếu nhãn nền là VCP / FLAT_BASE / CUP_HANDLE / CUP / BASE |
| Vị trí so với pivot? | khoảng cách > +5% → Đã chạy xa; < −5% → Chưa tới pivot; còn lại → Ở vùng pivot |

**4 trạng thái** (xét sự kiện quyết định gần nhất trong **5 phiên**):
1. **Mất hiệu lực**: sự kiện gần nhất là breakout thất bại, thủng stop, hoặc mất tiêu chí Trend Template.
2. **Vừa breakout**: sự kiện gần nhất là breakout hợp lệ.
3. **Ở vùng pivot**: xu hướng Đạt/Gần đạt + có nền + ở vùng pivot.
4. **Đang theo dõi**: còn lại.

Không trạng thái nào là lệnh mua / bán.

---

## 13. Sự kiện theo dõi hằng ngày

So trạng thái **hôm nay** với **phiên trước**. KL tương đối = V hôm nay / TB V của 50 phiên trước đó.

**Quanh ngày giao dịch không hưởng quyền (GDKHQ)** [TRIỂN KHAI] (sửa 11/10/2026): DNSE điều chỉnh **giá** quá khứ khi
chia/thưởng cổ phiếu nhưng **không điều chỉnh khối lượng** (HDB 09/10: giá ÷ 1,3, khối lượng giữ nguyên), nên trung bình
50 phiên lẫn hai đơn vị cổ phiếu. Khi có ngày GDKHQ đã ghi nhận (bảng `price_adjustments`) trong cửa sổ 50 phiên:
TB khối lượng chỉ tính **từ ngày GDKHQ** (cần ≥ 10 phiên; thiếu thì "KL không so được"). Breakout trong ngày GDKHQ và
2 phiên sau không được xếp **hợp lệ** (thành BREAKOUT_WEAK, ghi lý do). Không đoán quyền là chia cổ phiếu hay cổ tức
tiền.
"Đóng cửa ở giá trần" = C ≥ giá trần − 0,5 đ (giá trần thật nếu đã lưu; nếu chưa có thì suy từ C(−1) × biên độ, sai số 1 bước giá).

| Sự kiện | Điều kiện | Nguồn |
|---|---|---|
| **BREAKOUT** (hợp lệ) | C hôm nay > pivot hôm qua ≥ C hôm qua, nền hôm qua là nền thật, **và** cả 3: KL ≥ 1,4× TB50 (hoặc đóng cửa ở giá trần); đóng cửa ≥ 50% biên độ phiên ((C−L)/(H−L)); C ≤ pivot × 1,05 | KL [TRANG GỐC IBD]; còn lại [TRIỂN KHAI] |
| **BREAKOUT_WEAK** | như trên nhưng thiếu ≥ 1 điều kiện (ghi rõ thiếu gì) | [SÁCH] |
| **NEW_HIGH_NO_BASE** | vượt pivot khi nền hôm qua chỉ là NEAR_HIGH (đỉnh mới vài tuần, chưa có nền) | [SÁCH] |
| **BREAKOUT_FAILED** | trong 10 phiên gần đây có breakout, và hôm nay C < pivot đó ≤ C hôm qua | [SÁCH] / 10 phiên [TRIỂN KHAI] |
| **STOP_BREACHED** | C hôm nay < stop hôm qua ≤ C hôm qua | [TRIỂN KHAI] |
| **TREND_TEMPLATE_BROKEN** | mất ≥ 1 tiêu chí giá (TT1–TT7) so với hôm qua | |
| **TREND_TEMPLATE_RESTORED** | đạt lại đủ 7 tiêu chí giá | |
| **MA50_BREAK_HEAVY_VOLUME** | hôm qua C ≥ MA50, hôm nay C < MA50, KL ≥ 1,4× TB50 | [THỨ CẤP] |
| **LARGEST_DOWN_DAY** | phiên giảm có % giảm lớn nhất trong 120 phiên | [SÁCH] / 120 phiên [TRIỂN KHAI] |
| **SETUP_READY** | nền VCP / FLAT / CUP_HANDLE / CUP, đã cạn KL, giá cách pivot từ −3% đến 0% (mới đạt hôm nay) | [TRIỂN KHAI] |
| **CLIMAX_WARNING** | (mới xuất hiện hôm nay) giá cao hơn MA200 ≥ 70% **hoặc** ≥ 7/8 phiên gần nhất tăng | [TRANG GỐC IBD] |

---

## 14. Chỉ báo kỹ thuật kiểu TradingView

View "Kỹ thuật". Chỉ để xem, không phải quy tắc SEPA, chưa kiểm định trên dữ liệu VN.

### 14.1 Cách cho điểm [TRANG GỐC TradingView "Technical Ratings"]

```
Mỗi chỉ báo bỏ 1 phiếu: +1, −1 hoặc 0. Chỉ báo chưa đủ dữ liệu: không bỏ phiếu.
Điểm nhóm = (số phiếu + − số phiếu −) / số phiếu                         ∈ [−1, 1]
Xếp hạng chung = trung bình (điểm Đường MA, điểm Dao động)

> 0,5 Tích cực mạnh · 0,1…0,5 Tích cực · −0,1…0,1 Trung tính · −0,5…−0,1 Tiêu cực · < −0,5 Tiêu cực mạnh
```

### 14.2 Đường MA (15 phiếu): **+ nếu đường < C, − nếu đường > C**

SMA và EMA 10/20/30/50/100/200 (12 đường); **Ichimoku cơ sở** = (cao nhất + thấp nhất của 26 phiên)/2 [Hosoda];
**VWMA 20** = Σ(C·V)/ΣV trong 20 phiên; **Hull 9** = WMA(2·WMA(C,4) − WMA(C,9), 3) [Alan Hull].

### 14.3 Dao động (11 phiếu)

"Xu hướng tăng / giảm" ở đây = C trên / dưới SMA50.

| Chỉ báo | Công thức | + khi | − khi |
|---|---|---|---|
| RSI 14 | mục 2 [Wilder] | < 30 và tăng so với hôm qua | > 70 và giảm |
| Stoch (14,3,3) | thô = 100·(C − thấp nhất 14)/(cao nhất 14 − thấp nhất 14); %K = SMA3(thô); %D = SMA3(%K) [Lane] | %K < 20 và %K cắt lên %D | %K > 80 và cắt xuống %D |
| CCI 20 | TP = (H+L+C)/3; CCI = (TP − SMA20(TP)) / (0,015 × TB độ lệch tuyệt đối của TP quanh SMA20) [Lambert] | < −100 và tăng | > +100 và giảm |
| ADX 14 | +DM = H−H(−1) nếu lớn hơn L(−1)−L và > 0; −DM tương tự; +DI = 100·Wilder(+DM)/Wilder(TR); −DI tương tự; DX = 100·\|+DI − −DI\|/(+DI + −DI); ADX = Wilder(DX) [Wilder] | ADX > 20 và +DI cắt lên −DI | ADX > 20 và +DI cắt xuống −DI |
| Awesome Osc. | SMA5 − SMA34 của (H+L)/2 [Bill Williams] | vượt lên 0, hoặc "đĩa" trên 0 (2 cột dương, cột giữa thấp nhất) | ngược lại |
| Momentum 10 | C − C(−10) | > 0 | < 0 |
| MACD (12,26) | mục 2 [Appel] | MACD > Tín hiệu | MACD < Tín hiệu |
| Stoch RSI (3,3,14,14) | Stoch 14,3,3 áp lên chuỗi RSI 14 [Chande & Kroll] | xu hướng giảm, %K và %D < 20, %K > %D | xu hướng tăng, %K và %D > 80, %K < %D |
| Williams %R 14 | −100·(cao nhất 14 − C)/(cao nhất 14 − thấp nhất 14) [Larry Williams] | < −80 và tăng | > −20 và giảm |
| Bull Bear Power | (H − EMA13) + (L − EMA13) [Elder] | xu hướng tăng, BBP < 0 và tăng | xu hướng giảm, BBP > 0 và giảm |
| Ultimate Osc. (7,14,28) | BP = C − min(L, C(−1)); TR = max(H, C(−1)) − min(L, C(−1)); Aₖ = ΣBP/ΣTR trong k phiên; UO = 100·(4A₇ + 2A₁₄ + A₂₈)/7 [Larry Williams] | > 70 | < 30 |

Hai chỗ tài liệu TradingView không rõ, mình chọn theo cách khớp bộ lọc TradingView nhiều hơn khi đối chiếu 03/10/2026:
Momentum theo **dấu** (khớp 15/19 mã); Stoch yêu cầu **cắt nhau**.

### 14.4 Cột số và màu

Các cột RSI 14, Mom 10, AO, CCI 20, Stoch %K, %D là **giá trị thô** của các công thức trên tại phiên gần nhất.
Màu: Mom, AO, CCI theo dấu (xanh +, đỏ −, vàng 0; CCI đậm khi vượt ±100); RSI, Stoch theo mốc 50
(xanh > 50, đỏ < 50, vàng = 50; đậm ở RSI ≥ 70 / ≤ 30, Stoch ≥ 80 / ≤ 20).

---

## 15. Mẫu nến

Mẫu nến của phiên gần nhất. Định nghĩa theo Nison (nến Nhật), Bulkowski (thống kê, **thị trường Mỹ**), TA-Lib (ngưỡng).

```
thân = |C − O|        biên độ = H − L        bóng trên = H − max(O,C)        bóng dưới = min(O,C) − L
TB10 = biên độ trung bình 10 phiên trước;     TB22 = biên độ trung bình 22 phiên trước

Doji:          thân ≤ 0,1 × TB10                       [TA-Lib]
Thân nhỏ:      thân ≤ 0,3 × TB10
Bóng ngắn:     bóng ≤ 0,1 × TB10                       [TA-Lib]
Bóng dài:      bóng ≥ 2 × thân                         [Nison]
Nến dài:       biên độ > 1,46 × TB22                   [Bulkowski]
Xu hướng trước mẫu: "tăng" nếu C > SMA10 và SMA10 cao hơn 5 phiên trước; "giảm" ngược lại   [TRIỂN KHAI]
```

| Loại | Mẫu (điều kiện chính) |
|---|---|
| 3 nến | **Sao mai** (giảm; nến đen dài, nến thân nhỏ nằm dưới, nến trắng đóng cửa ≥ giữa nến 1) · **Sao hôm** (ngược lại) · **Ba chàng lính trắng** (3 nến trắng dài, đóng cửa tăng dần, mở cửa trong thân nến trước, bóng trên ngắn) · **Ba con quạ đen** (ngược lại) |
| 2 nến | **Nhấn chìm tăng/giảm** (thân nến 2 bao trùm thân nến 1, ngược màu, sau xu hướng ngược) · **Đường xuyên** (giảm; mở dưới đáy nến trước, đóng trên giữa thân) · **Mây đen che phủ** (ngược lại) · **Harami tăng/giảm** (thân nến 2 nằm trong thân nến dài 1) |
| 1 nến | **Doji / Doji chuồn chuồn / Doji bia mộ** · **Búa** (sau giảm; bóng dưới ≥ 2× thân, bóng trên ngắn) · **Người treo cổ** (cùng hình, sau tăng) · **Sao băng** (sau tăng; bóng trên ≥ 2× thân) · **Búa ngược** (cùng hình, sau giảm) |

Ưu tiên mẫu 3 nến → 2 nến → 1 nến. Phiên khóa trần / sàn không xét: H = L, hoặc tăng ≥ 97% biên độ và đóng cửa đúng giá
cao nhất (trần), hoặc giảm ≥ 97% biên độ và đóng cửa đúng giá thấp nhất (sàn).
Mọi mẫu cần phiên sau xác nhận. Tỷ lệ đảo chiều hiển thị là thống kê của Bulkowski trên thị trường Mỹ.

---

## 16. Biểu đồ: vùng giá, trendline, kênh, khung tuần/tháng

### 16.1 Vùng hỗ trợ / kháng cự [TRIỂN KHAI]

Từ 11/10/2026: vùng gần nhất mỗi phía được chọn trong **mọi** vùng (trước đây chỉ trong 3 vùng mạnh nhất, nên hỗ trợ
"gần nhất" của DRI cách giá 24%), cách giá ≥ **1%** (`chart_levels.zones.min_distance_pct`); vùng đang chứa giá được ghi
riêng. Tâm vùng làm tròn tới bước giá gần nhất, cận dưới làm tròn xuống, cận trên làm tròn lên. Mức lỗ 8% ở "Kiểm tra vị
thế" làm tròn lên theo bước giá.

1. **Mức ứng viên**: đỉnh / đáy swing (cao/thấp nhất ±3 nến, chỉ dùng sau khi đã xác nhận đủ 3 nến bên phải);
   mức bị phá: phiên đóng cửa vượt đỉnh 20 / 60 phiên trước → đỉnh đó là một mức; thủng đáy 20 / 60 phiên → đáy đó là một mức.
2. **Gộp**: các mức cách tâm cụm ≤ **1,5%** gộp thành một vùng; vùng = tâm ± **0,75%**.
3. **Số lần chạm** = số phiên mà biên độ (H…L) chạm vào vùng ± 1%.
4. **Độ mạnh** = 60 × min(chạm/8, 1) + 25 × 0,5^(số phiên từ lần chạm cuối / 120) + 15 × min(số loại nguồn/3, 1).
5. Vùng trên giá = kháng cự, dưới giá = hỗ trợ. Biểu đồ chỉ vẽ vùng **gần giá nhất** mỗi bên.

### 16.2 Trendline [TRIỂN KHAI]

- Tìm đỉnh / đáy swing (±5 nến) trong 250 phiên gần nhất.
- **Đường hỗ trợ** nối 2 đáy swing, **đường kháng cự** nối 2 đỉnh swing; hai điểm neo cách nhau ≥ 10 phiên;
  **không giá đóng cửa nào xuyên qua đường quá 1%** giữa hai điểm neo.
- Chọn đường có điểm neo thứ 2 gần nhất; trong số đó, đường được nhiều đỉnh/đáy chạm (trong ±1%) nhất, rồi dài nhất.
- **Ngày phá** = phiên đầu tiên sau điểm neo 2 đóng cửa xuyên đường quá 1%; chưa phá → kéo dài thêm 20 phiên về tương lai.
- Độ dốc %/tháng = (độ dốc mỗi phiên × 21) / giá tại điểm neo 2 × 100.

### 16.3 Kênh giá song song

Dựng trên trendline có điểm neo 2 gần hơn. Cạnh đối diện song song, đi qua giá **cao nhất** (nếu là đường hỗ trợ) hoặc
**thấp nhất** (nếu là đường kháng cự) trong đoạn đó → cạnh đối diện chứa mọi nến. Đường giữa = trung bình hai cạnh.
Vị trí giá trong kênh = (C − cạnh dưới)/(cạnh trên − cạnh dưới) × 100% (0 = cạnh dưới, 100 = cạnh trên).

### 16.4 Khung tuần / tháng

```
Nến tuần (thứ Hai–thứ Sáu) / nến tháng:  O = O phiên đầu;  H = cao nhất;  L = thấp nhất;  C = C phiên cuối;  V = tổng
Nến đang chạy (tuần / tháng chưa hết) được đánh dấu "chưa kết thúc"
```

| | Ngày | Tuần | Tháng |
|---|---|---|---|
| 4 đường MA | 20 / 50 / 150 / 200 phiên | 4 / 10 / 30 / 40 tuần | 5 / 10 / 20 / 40 tháng |
| RSI, MACD | trên giá đóng phiên | trên giá đóng tuần | trên giá đóng tháng |
| Trendline: tìm trong / swing ± / neo cách ≥ / dung sai / tia | 250 / 5 / 10 / 1% / 20 phiên | 156 / 3 / 6 / 2% / 8 tuần | 120 / 2 / 4 / 3% / 4 tháng |

MA10 và MA40 tuần là hai đường MarketSmith dùng; MA30 tuần là đường giai đoạn của Weinstein. Thông số trendline tuần/tháng
là [TRIỂN KHAI], co giãn theo độ dài nến. Vùng hỗ trợ / kháng cự ở khung tuần/tháng vẫn lấy từ biểu đồ ngày.
Lọc, trạng thái, sự kiện luôn tính trên nến **ngày**.

---

## 17. % thay đổi và giá tham chiếu

Bảng giá Việt Nam tính % so với **giá tham chiếu**:
- HOSE, HNX: tham chiếu = giá đóng cửa phiên trước (trừ ngày GDKHQ: sàn hạ tham chiếu).
- UPCoM: tham chiếu = **giá khớp bình quân phiên trước**, làm tròn tới bước giá 100 đ.

```
% thay đổi = C / tham chiếu − 1                      (khi đã có tham chiếu của đúng phiên đó)
% thay đổi = C / C(−1) − 1  và ghi rõ "so với đóng cửa phiên trước"   (khi chưa có)
```

**Nguồn giá tham chiếu, theo thứ tự ưu tiên:**
1. **Giá DNSE công bố** (`/price/{mã}/secdef`: tham chiếu, trần, sàn). DNSE công bố giá của phiên kế tiếp vào khoảng
   **20:45 tối hôm trước** (có mã HNX lúc 08:00 sáng). Gán phiên theo thời điểm công bố: trước 15:00 ngày làm việc →
   phiên hôm đó; sau đó → ngày làm việc kế tiếp. Ngày lễ: giá gán cho ngày không có phiên dùng cho phiên đầu tiên sau đó.
2. **UPCoM, phiên bị lỡ**: tham chiếu = làm_tròn_100(giá bình quân phiên giao dịch trước của mã) — lấy `avgPrice`
   từ dữ liệu khớp lệnh DNSE; trần = làm_tròn_xuống(tham chiếu × 1,15), sàn = làm_tròn_lên(tham chiếu × 0,85).
   Mỗi lần chạy quy tắc được **kiểm lại** với giá DNSE công bố của các mã UPCoM đang theo dõi; khớp < 90% thì lần đó
   không dùng (06/10/2026: khớp 13/13). Phiên trước cách > 14 ngày (tạm ngừng giao dịch) → không bù.
3. HOSE / HNX không bù (không suy ra được ngày GDKHQ từ giá).

**Khi DNSE điều chỉnh lịch sử (mã có quyền)**: giá tham chiếu lưu nguyên như sàn công bố. Với phiên thuộc **thang cũ**
(trước ngày GDKHQ), % được tính trên thang của chính ngày đó:

```
C_thang_cũ = làm_tròn_bước_giá(C_đã_điều_chỉnh / hệ số)
% = C_thang_cũ / tham chiếu_công_bố − 1
```

Ví dụ GMD 06/10/2026 (thưởng 2:1, hệ số 2/3): 52.000 / (2/3) = 78.000; 78.000 / 77.800 − 1 = **+0,26%** (đúng bảng giá).

**Lưới an toàn**: nếu |C / tham chiếu − 1| > biên độ sàn + 1 điểm (điều không thể xảy ra trong một phiên) thì tham chiếu
đang ở thang giá khác → dùng % so với đóng cửa.

**Đóng cửa ở giá trần** (mục 13) dùng giá trần đã lưu khi có.

---

## 18. Khối ngoại và khối nội

Nguồn: DNSE, theo **số cổ phiếu** từng phiên.

```
Ngoại ròng (cp)      = Ngoại mua − Ngoại bán
Ngoại ròng (đồng)    = giá trị ròng DNSE trả về
Ngoại ròng 5 phiên   = tổng ngoại ròng 5 phiên gần nhất
Nội mua              = KL khớp phiên − Ngoại mua        (không âm)
Nội bán              = KL khớp phiên − Ngoại bán        (không âm)
```

"Khối nội" là phần còn lại sau khi trừ khối ngoại. DNSE không tách cá nhân / tổ chức / tự doanh trong nước,
nên không bao giờ trình bày khối nội là "cá nhân".

---

## 19. Kiểm tra vị thế

Bạn nhập giá vốn; hệ thống đặt nó giữa các mức giá đã đo. **Không bao giờ** đưa ra kết luận mua thêm / bán.

```
Lãi/lỗ %            = C / giá vốn − 1
Giá lỗ tối đa       = giá vốn × (1 − 8%)                                  [SÁCH: Minervini cắt lỗ tối đa 7–8%]
Tình huống:  lỗ ≤ −8% → vượt mức lỗ tối đa;  −8% < lỗ < 0 → lỗ trong giới hạn (cảnh báo khi ≤ −5%);
             0 ≤ lãi < 16% → lãi nhỏ;  lãi ≥ 16% (= 2 × 8%) → nhắc quy tắc dời stop lên hòa vốn   [SÁCH]
Kiểm tra đơn vị: giá vốn lớn / nhỏ hơn giá hiện tại quá 5 lần → hỏi lại trước (thường là gõ 68,5 thay vì 68.500)
```

Kèm: MA50, MA200, pivot, stop, vùng hỗ trợ / kháng cự gần nhất, 5 phiên gần nhất (giá, KL, khối ngoại), các sự kiện xấu
trong 10 phiên (thủng stop, breakout thất bại, thủng MA50 với KL lớn, phiên giảm mạnh nhất 120 phiên, mất tiêu chí
Trend Template, climax), cơ bản, thị trường, tin 7 ngày.

---

## 20. Toàn thị trường

Một dòng cho mỗi mã có giá (~1.470), dựng lại hằng ngày (~15 giây), chỉ để xem:
- Giá, % (mục 17), phiên gần nhất, thanh khoản và hạng (mục 3).
- RS: lấy từ lần lọc gần nhất; mã dưới ngưỡng thanh khoản không có RS → TT8 "chưa đo được" (không tính trượt).
- Trend Template (mục 5), giá so với MA50 / MA150 / MA200, **cách đỉnh** = (C / đỉnh 52 tuần − 1)·100%, nền giá (mục 6), cơ bản.
- Chuỗi bị gắn cờ "cần kiểm tra" → chỉ hiện giá, không tính chỉ báo.

---

## 20b. Mẫu đánh ngắn hạn ≤ 2 tuần — CHỈ backtest, chưa hiện trên dashboard

Theo yêu cầu của bạn (09/10/2026). Mọi ngưỡng ở `swing` trong `config/thresholds.yaml`, đều [TRIỂN KHAI]. Tín hiệu biết được
lúc đóng cửa phiên i. C = đóng cửa, O = mở cửa, H/L = cao/thấp, V = khối lượng, MA = trung bình đơn giản.

```
Xu hướng tăng      = C > MA50 và MA50 > MA200
Điều chỉnh RSI2    = xu hướng tăng và RSI(2) < 10                        (RSI kiểu Wilder, như mục 14)
Điều chỉnh 3DOWN   = xu hướng tăng, 3 phiên đóng cửa giảm liên tiếp, L ≤ MA20 × 1,02
Quá bán            = C / C(−5) − 1 ≤ −10% và RSI(14) < 30
  RED              = quá bán và hôm nay đóng cửa giảm ("hôm nay đỏ")
  REVERSAL         = quá bán và (C − L)/(H − L) ≥ 0,6
Đà tăng CEILING    = C / C(−1) − 1 ≥ biên độ − 0,6% và C = H   (trần ƯỚC LƯỢNG, chưa có giá trần lịch sử)
        SURGE      = tăng ≥ 5%, V ≥ 2 × TB V 20 phiên trước, không phải trần
        HIGH20     = C > đỉnh 20 phiên trước, V ≥ 1,5 × TB V 20 phiên trước
Nền ngắn NR7       = xu hướng tăng và biên độ H − L nhỏ nhất 7 phiên
         BREAKOUT  = 10 phiên trước có (đỉnh / đáy − 1) ≤ 8%, C > đỉnh đó, V ≥ 1,3 × TB V 20
Một mã cùng một mẫu chỉ tính lại sau 5 phiên.
```

**Giao dịch giả lập:** mua giá mở cửa phiên i+1 (= B); bỏ tín hiệu nếu B mở cửa ở trần ước lượng. Bán được từ chiều B+2.
Giữ cố định: bán giá đóng cửa B+3 / B+5 / B+10. SL/TP: cắt lỗ −5%, chốt lời +8%, tối đa 10 phiên — ở B+2 chỉ biết chắc giá
đóng cửa; sau đó giá mở cửa vượt mức thì khớp ở giá mở cửa, chạm mức thì khớp ở mức; chạm cả hai trong một phiên tính là cắt lỗ.
Phiên khóa sàn (H = L, giảm hết biên độ) không bán được → dời sang phiên sau. Phí: 0,15% × 2 + thuế 0,1% + trượt giá theo
hạng thanh khoản (Cao 0,1%, Khá 0,15%, Thấp 0,25%, Dưới 3 tỷ 0,5% mỗi chiều). Bỏ cửa sổ quanh biến động không thể có trong
một phiên (quyền chưa điều chỉnh).

**Đối chứng:** cứ 5 phiên lấy một phiên giao dịch bất kỳ của mọi mã, cùng hạng thanh khoản, cùng cách giao dịch.
**Cửa bằng chứng:** trong mẫu N ≥ 200, hơn đối chứng ≥ 0,5 điểm % với t ≥ 2,5; ngoài mẫu (30% thời gian mới nhất) N ≥ 60,
trung bình dương và vẫn hơn đối chứng. **Độ vững** (với mẫu vượt cửa): trung vị, bỏ 1% cực trị, bỏ 10 mã đóng góp nhiều nhất,
từng năm, trượt giá gấp đôi, giá trị giao dịch phiên tín hiệu. Báo cáo: `python -m src.main backtest-swing` →
`data/backtest/swing_<ngày>.md`.

---

## 21. Có trong cấu hình nhưng chưa dùng

Các mục sau có trong `config/thresholds.yaml` nhưng **không ảnh hưởng gì đến số liệu trên dashboard**: thuộc các phần
chưa xây (Phase 9 — bộ chấm điểm / khuyến nghị) hoặc module cũ chỉ dùng cho backtest:

- `scoring_weights`, `recommendation_bands`, `recommendation_gates`, `confidence` (bộ chấm điểm & khuyến nghị chưa làm;
  dashboard không có nhãn MUA / BÁN nào).
- `market_regime`, `technical`, `risk_reward`, `patterns` (bẫy tăng/giảm, hành vi, phân kỳ, rũ bỏ) — module phân tích cũ
  và backtest mẫu hình (`docs/PATTERNS.md`); riêng `patterns.zones` và `patterns.swing_strength` được dùng cho vùng hỗ trợ /
  kháng cự (mục 16.1). Các giá trị ghi `[chọn lại]` trong phần `patterns` là giá trị dựng lại sau khi mất file gốc, chưa kiểm định.
- `backtest`: phí 0,15% × 2 + thuế bán 0,10% + trượt giá 0,10% × 2 = 0,6% khứ hồi — chỉ dùng khi chạy backtest.

---

*Mỗi khi đổi công thức hoặc ngưỡng, cập nhật file này cùng lúc. Nguồn chi tiết từng quy tắc SEPA: skill
`.claude/skills/sepa-vn-screening/references/`; hướng dẫn sử dụng: `C.md`; cách chạy: `run.md`.*
