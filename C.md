# C.md — Toàn bộ cách hệ thống lọc cổ phiếu, nhận dạng mẫu hình và vẽ biểu đồ

> Tài liệu này mô tả **đúng những gì code đang chạy** (đối chiếu với `config/thresholds.yaml`,
> `src/quant/screener.py`, `src/quant/monitor.py`, `src/web/` ngày 27/09/2026).
> Mọi con số đều là tham số trong `config/thresholds.yaml` — sửa ở đó, không cần sửa code.
>
> **Đây là công cụ nghiên cứu, không phải khuyến nghị mua bán.** Danh sách lọc ra là
> *ứng viên để bạn tự nghiên cứu tiếp*. Hệ thống không có chức năng đặt lệnh.

---

## Mục lục

1. [Bức tranh tổng thể](#1-bức-tranh-tổng-thể)
2. [Nhãn độ tin cậy của từng quy tắc](#2-nhãn-độ-tin-cậy-của-từng-quy-tắc)
3. [Dữ liệu đầu vào](#3-dữ-liệu-đầu-vào)
4. [Phễu lọc — từng tầng](#4-phễu-lọc--từng-tầng)
5. [Trend Template 8 tiêu chí](#5-trend-template-8-tiêu-chí)
6. [RS Rating](#6-rs-rating)
7. [Mẫu hình nền giá (base)](#7-mẫu-hình-nền-giá-base)
8. [Pivot, stop, rủi ro](#8-pivot-stop-rủi-ro)
9. [Chia nhóm A / B / C và xếp hạng](#9-chia-nhóm-a--b--c-và-xếp-hạng)
10. [Trạng thái thị trường](#10-trạng-thái-thị-trường)
11. [Theo dõi hằng ngày (monitor) — các sự kiện](#11-theo-dõi-hằng-ngày-monitor--các-sự-kiện)
12. [Biểu đồ và các đường chỉ báo](#12-biểu-đồ-và-các-đường-chỉ-báo)
13. [Tin tức](#13-tin-tức)
14. [Các lệnh](#14-các-lệnh)
15. [Giới hạn cần biết](#15-giới-hạn-cần-biết)
16. [Ba câu hỏi và 4 trạng thái](#16-ba-câu-hỏi-và-4-trạng-thái)
17. [Cổng cơ bản (BCTC quý)](#17-cổng-cơ-bản-bctc-quý)
18. [Sức mạnh ngành](#18-sức-mạnh-ngành)
19. [Kiểm tra vị thế](#19-kiểm-tra-vị-thế)
20. [Backtest các quy tắc trên dữ liệu Việt Nam](#20-backtest-các-quy-tắc-trên-dữ-liệu-việt-nam)
21. [Hỗ trợ / kháng cự, kênh giá song song, điểm của bạn](#21-hỗ-trợ--kháng-cự-kênh-giá-song-song-điểm-của-bạn)
22. [Đầu ngành và lọc theo ngành](#22-đầu-ngành-và-lọc-theo-ngành)
23. [Sóng ngành (nhóm bạn tự định nghĩa)](#23-sóng-ngành-nhóm-bạn-tự-định-nghĩa)
24. [Danh mục của tôi](#24-danh-mục-của-tôi)
25. [Chỉ báo kỹ thuật kiểu TradingView](#25-chỉ-báo-kỹ-thuật-kiểu-tradingview)

---

## 1. Bức tranh tổng thể

Phương pháp gốc là **SEPA của Mark Minervini** (kết hợp các mẫu hình nền giá của
William O'Neil / IBD). Ý tưởng: siêu cổ phiếu hầu như luôn **đang ở giai đoạn tăng
trưởng (Stage 2)**, **mạnh hơn thị trường**, và **tạo nền giá chặt** trước khi bứt phá.

Quy trình của bạn gồm hai tốc độ:

```
[Khi bạn ra lệnh "lọc lại"]                         python -m src.main screen
  1.663 cổ phiếu HOSE/HNX/UPCoM
    → dữ liệu sạch → thanh khoản → RS Rating → Trend Template → nền giá → cổng cơ bản → xếp hạng
    → DANH SÁCH CHÍNH (nhóm A + B, chuẩn SEPA)
    → THEO DÕI MỞ RỘNG (nhóm C, chưa đạt chuẩn) — tổng tối đa 80 mã được theo dõi
    — lưu lại, không bao giờ sửa lượt cũ

[Hằng ngày sau 15:00, chỉ cho 80 mã + đầu ngành + watchlist]     python -m src.main monitor / news
  cập nhật giá → tính lại trạng thái hôm nay và hôm qua → sự kiện (breakout, thủng stop…)
  thu thập tin tức → xem trên web                     python -m src.main start
```

Nguyên tắc xuyên suốt:
- **Code tính toán, không đoán.** Mọi MA, RSI, độ sâu nền, pivot… đều do Python tính.
- **Thiếu dữ liệu thì loại, không bịa.** Mã thiếu lịch sử hay dữ liệu bị gắn cờ không bao giờ "đạt".
- **Mỗi mã đều ghi rõ giá trị thật của từng tiêu chí**, không chỉ đạt/trượt.

---

## 2. Nhãn độ tin cậy của từng quy tắc

Mỗi quy tắc dưới đây mang một nhãn (lấy từ skill `.claude/skills/sepa-vn-screening/`,
đã được kiểm duyệt chéo 3 vòng):

| Nhãn | Nghĩa |
|---|---|
| **[SÁCH]** | Có trong sách của Minervini / O'Neil (xác nhận qua bản tóm tắt độc lập) |
| **[TRANG GỐC]** | Lấy từ trang của chính tác giả / IBD / TradingView |
| **[THỨ CẤP]** | Chỉ thấy ở nguồn thứ cấp |
| **[TRIỂN KHAI]** | **Cách đo do hệ thống đặt ra** để biến mô tả định tính thành con số — *không phải phương pháp gốc*, chưa backtest trên dữ liệu Việt Nam |
| **UNVERIFIED** | Không xác minh được con số gốc |

---

## 3. Dữ liệu đầu vào

| Mục | Chi tiết |
|---|---|
| Nguồn giá | **DNSE OpenAPI** (`/price/ohlc`, khung ngày), giá DNSE tính bằng nghìn đồng → nhân 1.000 ra VND |
| Điều chỉnh quyền | Chuỗi giá DNSE **đã điều chỉnh** cho chia tách/thưởng cổ phiếu (đo thực tế qua đợt thưởng 5:1 của SSI). **Không cộng cổ tức tiền mặt** |
| Độ dài lưu | ~5 năm (`backfill_history_days: 1830`) |
| Đối chiếu | MA20/50/150/200, RSI, MACD, khối lượng của SSI **khớp TradingView** (người dùng đã kiểm tra) |
| Universe | 1.663 cổ phiếu thường, **mã 3 ký tự**, trên **HOSE, HNX, UPCoM** (`config/universe.yaml`). Không phái sinh, chứng quyền, ETF, trái phiếu. Mã có chữ số (PC1, HT1, L14…) được giữ vì là cổ phiếu thật |

**Tự sửa khi DNSE tính lại lịch sử:** mỗi lần cập nhật, hệ thống tải chồng lên 15 ngày đã
có; nếu giá cũ lệch quá 0,5% (DNSE vừa điều chỉnh lại toàn bộ lịch sử sau một đợt chia cổ
phiếu) thì tải lại toàn bộ chuỗi giá của mã đó.

**Gắn cờ biến động bất thường** (trong 300 phiên gần nhất): một phiên không thể biến động
vượt biên độ sàn. Nếu vượt → chuỗi giá bị gắn cờ "stale" và **bị loại khỏi lọc** cho tới khi
có người kiểm tra.

| Sàn | Giới hạn giá đóng cửa phiên này so với phiên trước |
|---|---|
| HOSE | ±7,5% (biên độ 7% + 0,5 dung sai làm tròn) |
| HNX | ±10,5% |
| UPCoM | −37,2% … +55,6% (giá tham chiếu UPCoM là **giá bình quân phiên trước**, không phải giá đóng cửa, nên chênh lệch giữa hai giá đóng cửa có thể lớn hơn 15%) |

Mã bị gắn cờ nhưng thanh khoản tốt (hiện có MCH, BVB, VVS, MZG — thường do **chuyển sàn**)
được liệt kê riêng khi lọc. Nếu bạn kiểm tra thấy hợp lệ, thêm vào `sepa_screen.gap_waivers`
kèm lý do để đưa chúng trở lại.

---

## 4. Phễu lọc — từng tầng

```
Toàn thị trường (1.663)
 │ 1. Dữ liệu sạch
 │    - có dữ liệu giá
 │    - không bị gắn cờ stale
 │    - ≥ 252 phiên lịch sử (mã mới niêm yết: xem mục 9.3)
 │    - phiên cuối không cũ quá 7 ngày (loại mã tạm ngừng / hủy niêm yết)
 │ 2. Thanh khoản  [TRIỂN KHAI — không có trong phương pháp gốc, bắt buộc ở VN]
 │    - giá trị giao dịch TB 20 phiên (ước tính = giá đóng cửa × khối lượng)
 │        ≥ 3 tỷ đồng/phiên ở mọi sàn (từ 29/09/2026; trước đó 5 tỷ HOSE/HNX, 10 tỷ UPCoM)
 │    - gắn HẠNG thanh khoản (chỉ là nhãn, không loại mã, không đổi điểm):
 │        Cao ≥ 10 tỷ · Khá 8–10 tỷ · Thấp 3–8 tỷ (khó mua bán lượng lớn, dễ kẹt khi giảm sàn)
 │    - ≥ 90% số phiên trong 60 phiên gần nhất có giao dịch
 │ 3. RS Rating — xếp hạng trên mọi mã đủ dữ liệu (mục 6)
 │ 4. Trend Template 8 tiêu chí (mục 5)
 │ 5. Nhận dạng nền giá (mục 7)
 │ 6. Chia nhóm A/B/C, xếp hạng, lấy đủ 80 (mục 9)
 ▼
Danh sách theo dõi (80)
```

Kết quả lượt lọc 25/09/2026: 1.663 → 774 đủ dữ liệu → 147 đủ thanh khoản → 15 đạt 8/8 →
8 gần đạt (B) → 1 mới niêm yết + 36 mở rộng (C) → **60**.

---

## 5. Trend Template 8 tiêu chí

Nguồn: Minervini, *Trade Like a Stock Market Wizard* (2013) và *Think & Trade Like a
Champion* (2017) **[SÁCH]**. Tất cả tính trên giá đóng cửa đã điều chỉnh, **đường trung bình
đơn giản (SMA)**, khung ngày.

| # | Tiêu chí | Cách hệ thống đo | Ngưỡng |
|---|---|---|---|
| TT1 | Giá trên MA150 **và** MA200 | `close > SMA150` và `close > SMA200` | > 0% |
| TT2 | MA150 trên MA200 | `SMA150 > SMA200` | > 0% |
| TT3 | MA200 đi lên ít nhất 1 tháng | `SMA200 hôm nay > SMA200 của 22 phiên trước` | > 0% (có báo thêm "đi lên 4–5 tháng" = 105 phiên, không bắt buộc) |
| TT4 | MA50 trên MA150 **và** MA200 | `SMA50 > SMA150` và `SMA50 > SMA200` | > 0% |
| TT5 | Giá trên MA50 | `close > SMA50` | > 0% |
| TT6 | Giá cao hơn đáy 52 tuần ít nhất X% | `close ≥ đáy thấp nhất 250 phiên × (1 + X)` | **X = 30%** (sách 2013; sách 2017 dùng 25% — có thể đổi trong config) |
| TT7 | Giá cách đỉnh 52 tuần không quá 25% | `close ≥ đỉnh cao nhất 250 phiên × 0,75` | ≤ 25% |
| TT8 | RS Rating ≥ 70 | xem mục 6 | ≥ 70 (sách ưu tiên 80–90+) |

- 52 tuần = **250 phiên** (Việt Nam có ~245–250 phiên/năm, không phải 252 như Mỹ).
- Mỗi tiêu chí lưu **giá trị thật** (ví dụ TT1 của VNM = −1,1% nghĩa là giá thấp hơn MA150/MA200 1,1%).
- Trend Template là **điều kiện cần**, không phải tín hiệu mua.

---

## 6. RS Rating

IBD giữ bí mật công thức RS Rating thật, nên hệ thống dùng cách tái tạo phổ biến **[THỨ CẤP]**:

```
P3  = giá hôm nay / giá 63 phiên trước  − 1     (~3 tháng)
P6  = giá hôm nay / giá 126 phiên trước − 1     (~6 tháng)
P9  = giá hôm nay / giá 189 phiên trước − 1     (~9 tháng)
P12 = giá hôm nay / giá 252 phiên trước − 1     (~12 tháng)

Điểm thô (biến thể A, mặc định) = 0,4·P3 + 0,2·P6 + 0,2·P9 + 0,2·P12
   (biến thể B = 2·Q1 + Q2 + Q3 + Q4, theo từng quý riêng — đổi bằng `rs.variant: B`)

RS Rating = thứ hạng phần trăm của điểm thô trong MỌI MÃ ĐỦ DỮ LIỆU (~785 mã), từ 1 đến 99
```

- RS 90 = mạnh hơn 90% số mã đủ dữ liệu trên thị trường (từ 11/10/2026; trước đó chỉ so trong nhóm đủ thanh khoản).
- Tính theo **giá**, không cộng cổ tức tiền mặt (giống IBD). Vì vậy mã cổ tức cao như VNM
  có RS thấp hơn tổng lợi nhuận thực.
- RS **chỉ cập nhật khi lọc lại**. Hằng ngày, monitor dùng thêm chỉ số "so với VN-Index
  63 phiên" (chênh lệch % tăng giá của mã so với chỉ số).

---

## 7. Mẫu hình nền giá (base)

### 7.1 Cách xác định nền

1. Tìm **đỉnh cao nhất** trong tối đa **325 phiên** (65 tuần) gần nhất. Phiên **giật** (mở cửa ở giá cao nhất, đóng
   cửa ở nửa dưới phiên, cao hơn mọi phiên lân cận ≥ 3%) được tính bằng giá đóng cửa và web ghi "đã bỏ phiên giật".
2. **"Bắt đầu nền"** = đỉnh trái: phiên sớm nhất có đỉnh cách đỉnh cao nhất ≤ 3% và đã điều chỉnh ≥ 3% sau đó, miễn nền
   tính từ đó không sâu quá 33%. Nền = các phiên từ đó tới hôm nay.
3. **Đáy nền** = giá thấp nhất trong nền. **Độ sâu** = (đỉnh − đáy) / đỉnh.
4. **Các nhịp co hẹp chính** đo từ đỉnh cao nhất: đáy thấp nhất sau đỉnh, rồi đỉnh cao nhất sau đáy đó… (đỉnh thấp dần,
   đáy cao dần); mỗi nhịp sâu ≥ 3%. Sau nhịp cuối, một **vùng chặt** (điều chỉnh < 3%, ≥ 3 phiên, ở nửa trên nền) là nhịp
   cuối cùng. Độ sâu mỗi nhịp = (đỉnh nhịp − đáy nhịp) / đỉnh nhịp. (Trước 11/10/2026 dùng zigzag 3%, đếm cả dao động
   nhỏ: KSV 37 nhịp.)
5. **Khối lượng cạn** = KL trung bình 5 phiên gần nhất < 70% KL trung bình 50 phiên **[UNVERIFIED]**.

### 7.2 Thứ tự gán nhãn

Hệ thống thử từng nhãn theo thứ tự, gán nhãn đầu tiên đạt:

| Thứ tự | Nhãn (trên web) | Điều kiện |
|---|---|---|
| — | **Sát đỉnh, chưa có nền** (`NEAR_HIGH`) | Đỉnh cao nhất mới xuất hiện trong < 15 phiên, giá trong khoảng −10% đến +5% so với pivot (đỉnh + 1 bước giá); hoặc (từ 12/10/2026) nền sâu ≤ 33% nhưng < 25 phiên và không đạt VCP / nền phẳng / cốc, giá cách đỉnh nền ≤ 10% |
| — | **Đã chạy xa** (`EXTENDED`) | Giá đã vượt pivot hơn 5% (quá vùng mua) |
| — | **Vừa giảm mạnh** (`SHARP_PULLBACK`) | Đỉnh mới trong < 15 phiên nhưng giá đã thấp hơn pivot > 10%; hoặc (từ 12/10/2026) nền < 25 phiên như trên với giá dưới đỉnh nền > 10% (HII: "nền" 19 phiên sau cú giảm 28%) |
| — | **Thủng nền** (`BROKEN`) | Giá đóng cửa dưới đáy nền |
| 1 | **Cốc tay cầm** (`CUP_HANDLE`) | Đạt toàn bộ quy tắc cốc **và** có tay cầm hợp lệ (7.4) |
| 2 | **VCP** | Đạt quy tắc VCP (7.3) |
| 3 | **Nền phẳng** (`FLAT_BASE`) | Sâu ≤ 15% và dài ≥ 25 phiên **[TRANG GỐC IBD]** |
| 4 | **Cốc chưa có tay cầm** (`CUP`) | Đạt quy tắc cốc (7.4), chưa có tay cầm |
| 5 | **Nền** (`BASE`) | Sâu ≤ 33% nhưng không đạt mẫu nào ở trên |
| 6 | **Điều chỉnh quá sâu** (`TOO_DEEP`) | Sâu > 33% |

### 7.3 VCP — Volatility Contraction Pattern (Minervini)

Giá đi từ **biến động lớn bên trái** sang **biến động nhỏ bên phải**; mỗi nhịp điều chỉnh nông
hơn nhịp trước; khối lượng cạn dần **[SÁCH]**.

| Quy tắc | Ngưỡng |
|---|---|
| Số nhịp co hẹp | 2 – 6 ("hai đến bốn, đôi khi năm hoặc sáu" [SÁCH]) |
| Nhịp đầu tiên | sâu 10% – 35% **[UNVERIFIED]** |
| Mỗi nhịp nông hơn nhịp trước | được phép lệch tối đa 1 điểm % (nhịp chính luôn thỏa) |
| Nhịp cuối | ≤ 10% **[TRIỂN KHAI]** |
| Nhịp cuối ở nửa trên của nền | đỉnh nhịp cuối ≥ giữa nền **[TRIỂN KHAI]** |
| Ký hiệu kiểu Minervini | ví dụ `19W 19/7 4T` = nền 19 tuần, nhịp sâu nhất 19%, nông nhất 7%, 4 nhịp |

### 7.4 Cốc tay cầm / cốc chưa có tay cầm (O'Neil)

Quy tắc được **siết lại ngày 27/09/2026** sau khi phát hiện TLG (tăng vọt rồi rơi chữ V) bị
gọi nhầm là cốc. Mọi điều kiện được hiển thị ✓/✗ trên web.

| # | Điều kiện | Ngưỡng | Nhãn |
|---|---|---|---|
| 1 | Độ sâu cốc | 12% – 33% (sau thị trường gấu có thể tới 40–50%) | [SÁCH/THỨ CẤP] |
| 2 | Độ dài | ≥ 35 phiên (≈ 7 tuần) | [THỨ CẤP] |
| 3 | **Đáy chữ U, cạnh trái thoai thoải** | Từ đỉnh tới **lần đầu** chạm vùng 1/4 dưới của cốc mất **≥ 15 phiên** (nhanh hơn = rơi thẳng kiểu chữ V) | [TRIỂN KHAI] |
| 4 | **Đáy tròn cần thời gian** | ≥ 10 phiên đóng cửa trong vùng 1/4 dưới của cốc | [TRIỂN KHAI] |
| 5 | **Xu hướng tăng trước nền** | Từ đáy **gần nhất** trong 130 phiên trước nền tới đỉnh: tăng ≥ 30% và kéo dài ≥ 20 phiên (không phải một cú vọt) | [SÁCH] 30% / [TRIỂN KHAI] cách đo |
| 6 | Đã hồi lên nửa trên của cốc | Giá hiện tại ≥ giữa cốc | [SÁCH] |
| 7 | **Tay cầm** (chỉ cần cho "Cốc tay cầm") | Sau **miệng cốc phải** (đỉnh cao nhất sau đáy) có ≥ 5 phiên điều chỉnh, sâu ≤ 12%, đáy tay cầm nằm ở nửa trên cốc, KL trung bình trong tay cầm thấp hơn KL trung bình của cốc | [SÁCH/THỨ CẤP] ngưỡng / [TRIỂN KHAI] cách đo |

Ví dụ thật (25/09/2026):
- **TLG → "Nền"**: từ đỉnh 58.270 (08/12/2025) xuống vùng đáy chỉ **7 phiên** → chữ V; chưa có tay cầm.
- **GMD → "Nền"**: rơi xuống vùng đáy trong **5 phiên** sau đỉnh 86.970 (02/03/2026).
- **HDB → "Cốc chưa có tay cầm"**: xuống vùng đáy từ từ trong 28 phiên, 26 phiên tạo đáy,
  trước đó tăng 61% trong 129 phiên; chỉ thiếu tay cầm.

### 7.5 Các mẫu khác (có trong skill, chưa tự động nhận dạng)

Nền chồng nền (base-on-base), nền đi lên (ascending base), hộp vuông (square box), cờ cán cao
/ power play, đáy đôi, ba tuần chặt (3-weeks-tight), pocket pivot. Định nghĩa đầy đủ ở
`.claude/skills/sepa-vn-screening/references/02-bases-risk-market.md`.

### 7.6 Điểm chất lượng nền (chỉ dùng để xếp hạng)

```
Điểm gốc:   VCP 45 · Cốc tay cầm 40 · Nền phẳng 35 · Cốc chưa có tay cầm 30 · Nền 15
 + 20        nếu khối lượng đã cạn
 + (15 − độ sâu nhịp cuối %)      nếu dương   (nền càng chặt càng cao)
 + 20 × (1 − khoảng cách dưới pivot/10%)      nếu giá cách pivot không quá 10%
Tối đa 100. Các nhãn khác (sát đỉnh, đã chạy xa, thủng nền…) = 0.
```

---

## 8. Pivot, stop, rủi ro

### 8.1 Pivot (điểm mua theo phương pháp)

| Nhãn | Pivot |
|---|---|
| VCP, Nền | **đỉnh của nhịp co hẹp cuối** (hoặc của vùng chặt cuối ở nửa trên nền) + 1 bước giá |
| Nền phẳng | đỉnh cao nhất **của cả nền** + 1 bước giá [TRANG GỐC IBD] |
| Cốc tay cầm | **đỉnh tay cầm (miệng cốc phải)** + 1 bước giá [O'Neil] |
| Cốc chưa có tay cầm | **đỉnh bên trái của cốc** + 1 bước giá [IBD] |
| Sát đỉnh | đỉnh gần nhất + 1 bước giá |

IBD dùng "+10 cent"; ở Việt Nam đổi thành **+1 bước giá**:

| Sàn | Bước giá |
|---|---|
| HOSE | giá < 10.000đ: 10đ · 10.000–49.950đ: 50đ · ≥ 50.000đ: 100đ |
| HNX, UPCoM | 100đ |

**Vùng mua**: tối đa 5% trên pivot; quá 5% = "đã chạy xa" **[THỨ CẤP]**.

Pivot làm tròn **lên**, stop làm tròn **xuống** nhưng không thấp hơn pivot − 8% làm tròn **lên**, nên rủi ro luôn ≤ 8%.
Nếu đỉnh trái của nền cao hơn pivot từ 5% web ghi "vùng cung treo", từ 10% thì cảnh báo. Trong **5 phiên** sau một phiên breakout (hoặc thủng stop),
dashboard giữ nguyên pivot và stop của nền đó và ghi "giữ từ breakout dd/mm"; giá đóng cửa ≤ stop thì ghi
**"đã thủng stop dd/mm"** thay cho rủi ro tới stop, và biểu đồ vẽ stop đó nét chấm màu xám. Nhờ vậy nhãn "Vừa breakout" /
"Mất hiệu lực" luôn đi cùng đúng pivot / stop đã tạo ra nó (sửa theo báo cáo kiểm thử 11/10/2026).

Từ 12/10/2026 (báo cáo kiểm thử lần 3): chỉ tính **thủng stop** khi giá đóng cửa xuống tới stop **sau** đáy mà stop dựa
vào (đáy nhịp cuối / tay cầm / nền). Giá đã nằm dưới stop từ trước khi nền thành hình (pivot − 8% nằm trên mọi giá
thấp hơn pivot quá 8%) thì ghi **"stop dự kiến nếu mua ở pivot"**, không phải thủng stop. Mã còn giữ pivot / stop theo
cách tính trước 11/10 hiện "giữ tới dd/mm; theo cách tính mới: nhãn, pivot, stop".

### 8.2 Stop tham chiếu

Minervini: cắt lỗ thường 7–8%, **tối đa 10%** **[SÁCH]**. Hệ thống dùng tối đa **8%**:

| Nhãn | Stop |
|---|---|
| VCP, Nền | cao hơn trong hai mức: đáy nhịp co hẹp cuối, hoặc pivot − 8% |
| Nền phẳng | cao hơn trong hai mức: đáy nền, hoặc pivot − 8% |
| Cốc tay cầm | cao hơn trong hai mức: đáy tay cầm, hoặc pivot − 8% |
| Cốc chưa có tay cầm | pivot − 8% |
| Sát đỉnh, đã chạy xa, vừa giảm mạnh, quá sâu, thủng nền | **không có stop** (không có điểm mua ở pivot) |

**Rủi ro** (cột "Rủi ro") = (pivot − stop) / pivot, luôn ≤ 8%.

### 8.3 Rủi ro kẹt T+2 (đặc thù Việt Nam)

Mua ngày T thì sớm nhất **chiều T+2** mới bán được. Trước đó có thể gặp tới **3 lần giảm sàn**
(phần còn lại ngày T, ngày T+1, sáng T+2). Mức lỗ khi bán được sớm nhất:

| Sàn | Mua ở giá tham chiếu | Mua ở giá trần (hay gặp khi mua breakout) |
|---|---|---|
| HOSE (±7%) | **19,6%** | 24,8% |
| HNX (±10%) | **27,1%** | 33,7% |
| UPCoM (±15%) | **38,6%** | 46,6% |

Công thức: `1 − (1 − b)³` (mua giá tham chiếu), `1 − (1 − b)³ / (1 + b)` (mua giá trần).
**Đây không phải giới hạn trên** — cổ phiếu trắng bên mua ở giá sàn thì không bán được. Mọi con
số đều vượt xa mức cắt lỗ 10% của Minervini.

---

## 9. Chia nhóm A / B / C và xếp hạng

### 9.1 Nhóm

| Nhóm | Điều kiện | Ý nghĩa |
|---|---|---|
| **A** | Đạt **đủ 8/8** Trend Template | Chuẩn SEPA |
| **B** | Đạt 6–7/8, **bắt buộc TT8 (RS ≥ 70)**, và **đang tạo nền** (VCP / nền phẳng / cốc / nền) | Gần đạt, thường bứt phá đầu tiên khi thị trường hồi |
| **C** | Lấp cho đủ 80 mã — **CHƯA đạt chuẩn SEPA** | Tab riêng "Theo dõi mở rộng" |

**Danh sách chính = A + B.** Nhóm C nằm ở tab riêng "Theo dõi mở rộng" trên web và bảng riêng
trong CLI: vẫn được monitor và thu tin, nhưng **không bao giờ trộn vào danh sách chính**.

Thứ tự lấy: **A trước, rồi B, rồi C**, tổng tối đa 80 (60 cho tới 29/09/2026). Trong nhóm A và B, sắp theo **tầng RS**
trước rồi mới tới điểm tổng hợp:

| Tầng RS | Ngưỡng (config `rs_tiers`) |
|---|---|
| Dẫn dắt mạnh | RS ≥ 90 |
| Dẫn dắt | 80–89 |
| Đạt | 70–79 |

**Cổng cơ bản** (mục 17): mã A/B có hạng cơ bản **FAIL** bị đưa khỏi danh sách chính (ghi lý do;
có thể vào nhóm C nếu đủ điều kiện). Thiếu dữ liệu BCTC thì **giữ nguyên**, chỉ gắn nhãn.

### 9.2 Nhóm C gồm

Từ 30/09/2026 nhóm C **chỉ nhận mã đang trong xu hướng tăng** (trước đó RS ≥ 30 và 2/8 tiêu chí đã
đủ, nên 22/52 mã C đang downtrend):

- giá **trên MA50, MA150 và MA200** (tiêu chí TT1 và TT5);
- RS ≥ 60, đạt ≥ 3/8 tiêu chí;
- **không gãy chart** (không "Thủng nền", không "Vừa giảm mạnh"). "Điều chỉnh quá sâu" vẫn được nhận
  nếu giá đã hồi lên trên cả ba đường MA.

Thứ tự trong nhóm C: **mã mới niêm yết** (xem 9.3) → **mã thuộc sóng ngành đang mạnh** (mục 23) →
các mã còn lại theo điểm tổng hợp.

**Không lấp cho đủ 80**: nếu chỉ 51 mã đạt, danh sách có 51 mã.

**Gãy chart rời danh sách chính**: mã nhóm A/B có nền "Vừa giảm mạnh" (lập đỉnh trong 3 tuần rồi giảm
> 10% dưới pivot) hoặc "Thủng nền" bị đưa khỏi danh sách chính dù vẫn đạt 8/8 tiêu chí — Trend
Template chậm hơn một cú gãy vừa xảy ra. Lý do được ghi ở dòng "Loại" (ngày 29/09: HII, SSB, VGR).

### 9.3 Mã mới niêm yết (IPO base của Minervini)

Mã có < 252 phiên nhưng ≥ 20 phiên, đủ thanh khoản, giá **cách đỉnh sau niêm yết ≤ 25%**.
TT1–TT6 và TT8 ghi **"chưa đủ dữ liệu"** (chấm xám trên web), **không tính là trượt**. Chỉ TT7
được đo. Hiện có DMX. (TCX, VCK, GEL, LPS, VPX bị loại vì đã cách đỉnh sau niêm yết > 25%.)

### 9.4 Điểm tổng hợp (cột "Điểm")

```
Điểm = (35·RS + 20·TT + 15·Gần đỉnh + 20·Chất lượng nền) / 90        [TRIỂN KHAI]

 RS            = RS Rating (1–99)
 TT            = số tiêu chí đạt / 8 × 100
 Gần đỉnh      = max(0, 100 − 4 × % cách đỉnh 52 tuần)     (cách đỉnh 25% → 0 điểm)
 Chất lượng nền = mục 7.6
```

Khi mã có hạng cơ bản (mục 17), thêm **10% × điểm cơ bản** (A+ 100, A 80, B 55, Yếu 30, Fail 0)
và chia cho 100. Mã chưa có dữ liệu BCTC dùng công thức trên (chia cho 90) — thiếu dữ liệu
không bị cộng hay trừ điểm. Sức mạnh ngành **không** tính vào điểm (trọng số 0 cho tới khi backtest).

---

## 10. Trạng thái thị trường

Dựa trên VN-Index **[TRIỂN KHAI — ngưỡng chưa kiểm định cho VN]**:

- **Ngày phân phối** = VN-Index giảm ≥ 0,2% với khối lượng cao hơn phiên trước **[TRANG GỐC IBD]**.
  Đếm trong 25 phiên gần nhất.

| Trạng thái | Điều kiện |
|---|---|
| **Đang điều chỉnh** | VN-Index dưới MA200 |
| **Xu hướng tăng** | VN-Index > MA50 > MA200 **và** < 5 ngày phân phối |
| **Tăng nhưng chịu áp lực** | ≥ 5 ngày phân phối, hoặc VN-Index dưới MA50 |
| **Trung tính** | các trường hợp còn lại |

Theo SEPA, khi thị trường đang điều chỉnh thì danh sách chỉ để **theo dõi**, chưa phải lúc mở vị
thế mới.

**Ngày follow-through (FTD)** [TRANG GỐC IBD, ngưỡng trong config `market.ftd`]:
1. Tìm đáy thấp nhất trong 90 phiên; phiên tăng đầu tiên sau đáy là ngày 1 của **nỗ lực hồi phục**.
2. Từ **ngày 4** trở đi, phiên VN-Index tăng **≥ 1,25%** với KL cao hơn phiên trước = **FTD**.
3. Nỗ lực hồi phục **thất bại** nếu VN-Index đóng cửa dưới đáy trước khi có FTD.
4. Sau FTD, hệ thống đếm **ngày phân phối kể từ FTD**; nhiều ngày phân phối → FTD bị coi là
   đang chịu áp lực.

Mọi nhãn trạng thái của cổ phiếu đều kèm **ghi chú thị trường**: khi "Đang điều chỉnh", nhãn
"Ở vùng pivot" hiện kèm "thị trường điều chỉnh — SEPA không mở vị thế mới"; khi "Chịu áp lực"
→ "giảm quy mô". Hiện tại (27/09/2026): **đang điều chỉnh**; FTD ngày 03/08/2026 đã bị 6 ngày
phân phối làm mất hiệu lực.

---

## 11. Theo dõi hằng ngày (monitor) — các sự kiện

`python -m src.main monitor` cập nhật giá 80 mã + đầu ngành + watchlist, rồi **tính lại trạng thái hôm nay
và hôm qua từ dữ liệu giá** (không dựa vào kết quả lưu trước) và báo khác biệt. Pivot dùng để
xét breakout là **pivot đã biết từ hôm qua**.

| Sự kiện | Điều kiện | Nhãn |
|---|---|---|
| **Breakout hợp lệ** | Đóng cửa vượt pivot hôm qua (hôm qua còn dưới) **và** đủ cả 3: KL ≥ 1,4 × TB 50 phiên (hoặc đóng cửa ở giá trần), đóng cửa ở nửa trên biên độ phiên (vị trí đóng cửa ≥ 0,5), chưa chạy quá pivot + 5% | [TRANG GỐC IBD] / [TRIỂN KHAI] vị trí đóng cửa |
| **Breakout yếu** | Vượt pivot nhưng thiếu ít nhất một điều kiện trên — ghi rõ điều kiện nào thiếu | [SÁCH] KL yếu dễ thất bại |
| **Đỉnh mới, chưa có nền** | Vượt đỉnh của mã "sát đỉnh" — không phải breakout khỏi nền chuẩn | [SÁCH] |
| **Breakout thất bại** | Một breakout trong 10 phiên gần nhất đóng cửa trở lại dưới pivot | [SÁCH] / [TRIỂN KHAI] 10 phiên |
| **Thủng stop** | Đóng cửa dưới stop tham chiếu | — |
| **Mất tiêu chí TT** | Mất một trong TT1–TT7 so với hôm qua | — |
| **Đạt lại TT** | Đạt lại đủ 7 tiêu chí giá | — |
| **Thủng MA50, KL lớn** | Đóng cửa cắt xuống MA50 với KL ≥ 1,4 × TB | [THỨ CẤP] |
| **Phiên giảm mạnh nhất** | Phiên giảm mạnh nhất trong 120 phiên | [SÁCH] / [TRIỂN KHAI] cửa sổ |
| **Nền sẵn sàng** | VCP / nền phẳng / cốc, KL đã cạn, giá cách pivot trong 3% | [TRIỂN KHAI] |
| **Cảnh báo climax** | Giá cao hơn MA200 ≥ 70%, hoặc 7/8 phiên tăng | [TRANG GỐC IBD] |

- Phiên **đóng cửa ở giá trần**: hệ thống ghi chú rằng KL có thể thấp giả tạo (thiếu người bán).
- Mã mới niêm yết: không báo sự kiện Trend Template cho tới khi đủ 252 phiên.
- Breakout khi thị trường không ở "Xu hướng tăng" được ghi chú thêm trạng thái thị trường.
- Sự kiện chỉ **mô tả điều giá và khối lượng đã làm**, không kèm khuyến nghị.

---

## 12. Biểu đồ và các đường chỉ báo

Bấm một mã trên web → tab **"Biểu đồ"** (mặc định). Biểu đồ dùng thư viện lõi của TradingView
(lightweight-charts), dữ liệu DNSE, chạy được cho **cả 3 sàn**.

### 12.1 Ba khung, đồng bộ trục thời gian

**Khung giá**

| Đường | Màu | Công thức |
|---|---|---|
| Nến | xanh tăng / đỏ giảm | giá VND |
| Khối lượng | cột mờ dưới đáy, xanh = phiên tăng, đỏ = phiên giảm | cổ phiếu |
| **MA20** | **tím** `#a855f7` | SMA 20 phiên (≈ 1 tháng) |
| **MA50** | **xanh dương** `#2563eb` | SMA 50 phiên (≈ 10 tuần) |
| **MA150** | **cam** `#f59e0b` | SMA 150 phiên (≈ 30 tuần) |
| **MA200** | **đỏ** `#ef4444` | SMA 200 phiên (≈ 40 tuần) |
| pivot | xanh nhạt, nét đứt | mục 8.1 |
| stop | hồng đỏ, nét đứt | mục 8.2 |

**Khung RSI (14)** — đường xanh ngọc `#14b8a6`, vạch 70 / 50 / 30. RSI của Wilder, trung bình
làm mượt kiểu Wilder (RMA) — giống mặc định TradingView.

**Khung MACD (12, 26, 9)** — MACD xanh `#0ea5e9` = EMA12 − EMA26; Signal cam `#f97316` =
EMA9 của MACD; histogram = MACD − Signal (xanh dương, đỏ âm). Tính theo VND nên không có số lẻ.

Dòng chú thích phía trên ghi ngày, giá mở / cao / thấp / đóng, % thay đổi, khối lượng và giá trị các
đường **tại cây nến dưới con trỏ** (không rê chuột thì là phiên gần nhất).

Nút **"Lớp hiển thị ▾"** chọn những gì được vẽ (trình duyệt nhớ lựa chọn, có nút "Về mặc định"):
MA20 / MA50 / MA150 / MA200; Pivot và stop; Hỗ trợ / kháng cự gần nhất; Trendline và kênh giá;
Nền giá; Khối lượng; RSI; MACD. **Mặc định**: MA50, MA150, MA200, pivot / stop, khối lượng, RSI —
các lớp còn lại tắt để biểu đồ dễ đọc. Khung RSI / MACD tắt thì ẩn hẳn.

### 12.2 Lớp vẽ nền giá

| Ký hiệu | Ý nghĩa |
|---|---|
| Mũi tên "Bắt đầu nền" | đỉnh cao nhất nền được đo từ đó |
| Mũi tên "Đáy −x%" | đáy nền và độ sâu |
| "Miệng cốc phải" / "Đáy tay cầm" | chỉ hiện với nhãn cốc |
| Đường ngang "đỉnh nền" / "giữa nền" / "đáy nền" | màu xám |
| Đường zigzag nét đứt | các nhịp dao động ≥ 6% (bản vẽ gọn; xếp hạng dùng ngưỡng 3%) |

Khung **"Vì sao là …"** bên dưới liệt kê ✓/✗ từng điều kiện của nhãn nền kèm số đo thật.

### 12.3 Tab TradingView

Widget chính thức của TradingView, bật sẵn MA20/50/150/200, RSI, MACD, khối lượng.
**Chỉ hiển thị được mã HNX và UPCoM** — TradingView không cấp dữ liệu HOSE cho biểu đồ nhúng
(bản quyền dữ liệu). Widget miễn phí cũng **không cho đổi màu MA**. Nút "Mở trên TradingView ↗"
mở mã trên tradingview.com cho mọi sàn. Từ 12/10/2026 nút chuyển sang tab này chỉ hiện với mã HNX / UPCoM.

### 12.3b Biểu đồ VN-Index

Bấm vào thẻ **VN-Index** ở đầu trang: khung chi tiết mở biểu đồ chỉ số (đơn vị điểm, 2 số thập phân) với cùng
các lớp (MA, khối lượng, RSI, MACD, hỗ trợ / kháng cự, trendline và kênh) và thêm lớp **"Ngày phân phối và
FTD"**: mũi tên đỏ "PP" ở mỗi ngày phân phối trong 25 phiên gần nhất, mũi tên xanh "FTD" ở ngày follow-through
(mục 10). Đầu khung ghi trạng thái thị trường, số ngày phân phối và ngày FTD.

### 12.3c Khung Ngày / Tuần / Tháng (05/10/2026)

Nút **Ngày | Tuần | Tháng** cạnh "Lớp hiển thị" đổi mỗi cây nến thành một phiên, một tuần (thứ Hai – thứ Sáu)
hoặc một tháng; trình duyệt nhớ lựa chọn, riêng cho cổ phiếu và cho VN-Index (từ 12/10/2026: khung Tháng chọn ở
VN-Index không còn mang sang chi tiết mã).

| | Ngày | Tuần | Tháng |
|---|---|---|---|
| Nến | 1 phiên | mở = phiên đầu tuần, cao / thấp = cực trị, đóng = phiên cuối, KL = tổng | như tuần, theo tháng |
| 4 đường MA | MA20 / 50 / 150 / 200 phiên | MA4 / 10 / 30 / 40 tuần (≈ MA20 / 50 / 150 / 200 ngày) | MA5 / 10 / 20 / 40 tháng |
| RSI 14, MACD 12/26/9 | trên giá đóng phiên | trên giá đóng tuần | trên giá đóng tháng |
| Lịch sử | ~2 năm gần nhất | toàn bộ đã lưu (từ 09/2015) | toàn bộ đã lưu (từ 09/2015) |

- MA10 tuần và MA40 tuần là hai đường MarketSmith vẽ trên biểu đồ tuần; MA30 tuần là đường giai đoạn của
  Weinstein. Số kỳ nằm ở `chart_timeframes` trong `config/thresholds.yaml`. MA tuần **gần bằng, không bằng hẳn**
  MA ngày (tuần nghỉ lễ có ít phiên hơn).
- Tuần / tháng đang chạy ghi **"chưa kết thúc"**: giá đóng chỉ là giá phiên gần nhất, nến còn thay đổi.
- Mũi tên (bắt đầu nền, đáy nền, ngày phân phối, FTD) nằm trên cây nến chứa phiên đó; nhiều ngày phân phối
  trong cùng một tuần / tháng gộp thành một mũi tên "PP×n".
- Pivot, stop, hỗ trợ / kháng cự là mức giá nên vẽ ở mọi khung (vùng hỗ trợ / kháng cự lấy từ biểu đồ ngày).
- **Trendline và kênh giá** (từ 06/10/2026) được đo **trên chính nến tuần / tháng**, cùng quy tắc như khung ngày
  (mục 21) nhưng số đếm tính bằng nến của khung đó [TRIỂN KHAI — tự chọn theo độ dài nến, không lấy từ nguồn]:

  | | Ngày | Tuần | Tháng |
  |---|---|---|---|
  | Tìm đỉnh / đáy trong | 250 phiên | 156 tuần (3 năm) | 120 tháng (10 năm) |
  | Đỉnh / đáy swing: cao / thấp nhất ± | 5 nến | 3 nến | 2 nến |
  | Hai điểm neo cách nhau ≥ | 10 phiên | 6 tuần | 4 tháng |
  | Giá đóng cửa được chọc qua đường ≤ | 1% | 2% | 3% |
  | Tia kéo dài về tương lai | 20 phiên | 8 tuần | 4 tháng |

  Ngày neo / ngày phá ghi theo nến: "tuần 14/09/2026" (phiên đầu tuần), "tháng 08/2026". Độ dốc vẫn ghi %/tháng.
  Nến tuần / tháng đang chạy cũng được dùng, nên đường có thể đổi khi tuần / tháng chưa kết thúc.
  Cài đặt: `chart_levels.trendline_by_tf` trong `config/thresholds.yaml`.
- Rà soát 06/10/2026 (`python scripts/audit_chart.py --round 3 --tf W` / `--tf M`, 85 mã theo dõi + VN-Index): mọi nến
  tuần / tháng khớp với nến gộp tay từ nến ngày; 169 trendline + 85 kênh tuần (1.759 mục) và 168 trendline + 84 kênh
  tháng (1.790 mục) đều đúng quy tắc — điểm neo là đỉnh / đáy swing, không giá đóng cửa nào xuyên giữa hai điểm neo,
  ngày phá, giá trị hiện tại, độ dài và ngày cuối của tia, hai cạnh kênh song song và chứa mọi nến. Khung ngày cùng
  lượt: 3.006 mục đều đúng (3 mục pivot/stop lệch 0,000000000001 do số thực — đã sửa cách so trong script).
- Màn hình lọc, trạng thái, sự kiện vẫn tính trên nến **ngày**; khung tuần / tháng chỉ để xem.
- Kiểm tra 05/10/2026: tuần 28/09–02/10 của GMD và VN-Index, tháng 10 của GMD — mở / cao / thấp / đóng,
  khối lượng, % thay đổi, MA10 / 30 / 40 tuần, MA10 / 20 tháng khớp với cách tính tay trực tiếp từ cơ sở dữ liệu;
  6 ngày phân phối tháng 9 hiện thành "PP×6".

### 12.4 Giao diện web (làm lại 03/10/2026)

**Bố cục**: thanh bên trái chia ba phần.
- **Cổ phiếu**: Danh sách chính (A + B) · Theo dõi mở rộng (C) · Của tôi · Đầu ngành · Tất cả theo dõi.
  Năm nhóm dùng **chung một bảng và một bộ lọc**; số bên cạnh là số mã.
- **Thị trường**: Ngành & sóng · Sự kiện · Tin tức.
- **Kiến thức**: Học (mục 27).

Đầu trang chỉ còn thẻ **VN-Index**: điểm số, % so với phiên trước, trạng thái thị trường (mục 10) kèm
số ngày phân phối, đường giá 60 phiên. Rê chuột lên thẻ để đọc ghi chú đầy đủ. Bên cạnh là ô chọn
**ngày lọc** (lượt lọc toàn thị trường đang xem). Trên điện thoại, thanh bên chuyển lên đầu trang.

**Bộ lọc**: ô tìm (mã hoặc tên công ty), **Trạng thái**, **Ngành** (gồm cả sóng ngành), **Sàn**,
**Thanh khoản**. Riêng danh sách chính có thêm **Mức đạt chuẩn** (A đạt đủ 8/8, B gần đạt). Tất cả nằm
trên **một hàng** (màn hình hẹp thì vuốt ngang). Mỗi ô mở một menu bo góc: dấu ✓ ở lựa chọn đang dùng,
số bên phải là số mã sẽ còn lại nếu chọn (lựa chọn không có mã nào được ẩn); danh sách dài (Ngành) có
ô tìm nhanh, gõ không dấu cũng được (VD "cang"). Dùng được bằng bàn phím: ↑ ↓, Enter, Esc.

**Ba kiểu bảng** (nút bên phải dòng đếm mã; bấm tiêu đề cột để sắp xếp, bấm lần ba để về thứ tự gốc):

| Kiểu | Cột |
|---|---|
| Tổng quan | Mã (nhóm, sóng ngành, tên công ty) · Giá và % hôm nay · RS · Xu hướng (8 chấm Trend Template) · Nền giá và khoảng cách tới pivot · Trạng thái (mục 16) · Khối ngoại (ròng phiên gần nhất và 5 phiên) · Thanh khoản · Cơ bản (hạng và lợi nhuận quý so với cùng kỳ) |
| Kỹ thuật | xếp hạng kiểu TradingView, RSI, Mom, AO, CCI, Stoch, mẫu nến (mục 25) |
| Dòng tiền | ngoại mua / bán / ròng (cổ phiếu và tỷ đồng), ngoại ròng 5 phiên, nội mua / bán (mục 26) |

Ở nhóm **Đầu ngành**, bảng xếp theo ngành (RS ngành cao trước), mỗi ngành một tiêu đề. Ở nhóm
**Của tôi**, ô thêm mã nằm ngay trên bảng và ghi chú hiện dưới tên công ty.

**Khung chi tiết** (bấm một mã): đầu khung có mã, tên, giá, các nhãn (nhóm trong lượt lọc đang xem,
RS, trạng thái, thanh khoản, sóng). Sáu thẻ: **Biểu đồ** · **Phân tích SEPA** (ba câu hỏi, Trend
Template, nền giá, chỉ báo kỹ thuật) · **Dòng tiền** · **Cơ bản & ngành** · **Tin tức & sự kiện** ·
**Kiểm tra vị thế** (mục 19).

---

## 13. Tin tức

`python -m src.main news` — tin theo mã từ **CafeF** (gồm công bố thông tin, kèm link PDF văn bản
gốc) và RSS của **CafeF, Vietstock**, chỉ cho các mã đang theo dõi (80 mã + đầu ngành + watchlist), 30 ngày.

- Bài trùng được gộp thành một **sự kiện**; phân loại theo **tiêu đề trước**: pháp lý, giao dịch
  nội bộ, cổ tức, tăng vốn/phát hành, kết quả kinh doanh, đại hội cổ đông, M&A, niêm yết, quản trị.
- Tin từ báo (cấp 3) gắn nhãn **INFERENCE** (chưa đối chiếu văn bản gốc); tin diễn đàn luôn
  là **RUMOUR**.
- Claude Code đọc và phân tích **trong phiên khi bạn hỏi** (không dùng Claude API).
- Mã trùng chữ thường (HCM, GAS, OIL, BIG, VND...) chỉ gắn khi có dấu hiệu ("mã HCM", "(HCM)", "HCM: ..."), tên
  công ty hoặc tên khác (`news.ticker_aliases`). Cụm "Sở giao dịch Chứng khoán TP.HCM" là HOSE, không phải Chứng
  khoán TP.HCM (`news.alias_exclusions`). Tin chỉ "nhắc tới" một mã như vậy mà không còn lưu bằng chứng bị gỡ.

---

## 14. Các lệnh

```bash
python -m src.main backfill                 # cập nhật giá toàn thị trường (lần đầu ~30 phút)
python -m src.main screen                   # LỌC LẠI toàn thị trường → danh sách 80 mã mới
python -m src.main monitor                  # hằng ngày sau 15:00: cập nhật + sự kiện cho các mã theo dõi
python -m src.main flow [--days 10]         # hằng ngày: khối ngoại mua / bán (số cổ phiếu) từ DNSE
python -m src.main news                     # hằng ngày: tin tức cho các mã theo dõi
python -m src.main news-brief GMD           # tóm tắt ≤ 7 sự kiện tin tức của một mã
python -m src.main start                    # cập nhật mọi thứ (lọc lại nếu có phiên mới) rồi mở web — xem run.md mục 1
python -m src.main start --plan             # xem trước bước nào sẽ chạy
python -m src.main start --no-update        # mở web ngay
python -m src.main backfill --recheck-gaps  # kiểm tra lại cờ biến động bất thường, không cần mạng
python -m src.main fundamentals             # BCTC quý từ CafeF cho các mã đủ thanh khoản (~6 phút / 150 mã)
python -m src.main industry                 # phân ngành từ Vietstock (làm mới sau 30 ngày)
python -m src.main backtest-sepa            # backtest các quy tắc (~5 phút), lưu data/backtest/
```

Sau khi chạy `fundamentals` hoặc `industry`, chạy lại `screen` để danh sách dùng dữ liệu mới.

---

## 15. Giới hạn cần biết

1. **Các cách đo [TRIỂN KHAI] chưa được backtest trên dữ liệu Việt Nam**: nhận dạng nền, hình
   dạng cốc, ngưỡng thanh khoản, điểm tổng hợp, trạng thái thị trường. Hãy xem biểu đồ để tự
   kiểm tra nhãn.
2. **Dữ liệu cơ bản là tầng A (CafeF, chưa đối chiếu BCTC gốc)**, và **ngân hàng / công ty
   chứng khoán chưa có hạng** vì CafeF hiển thị BCTC của họ lệch dòng (mục 17).
3. **RS và giá không cộng cổ tức tiền mặt.**
4. **Thị trường đang điều chỉnh** → nhóm A ít; nhóm C là phần lấp đủ, nhiều mã đang yếu.
5. **Chưa tự nhận dạng**: nền chồng nền, hộp vuông, đáy đôi, cờ cán cao, 3 tuần chặt, pocket
   pivot.
8. **Backtest (mục 20) chưa cho thấy lợi thế** ở phần lớn quy tắc như đang cài đặt — ngưỡng chưa
   được đổi, chờ bạn quyết định.
6. **Rủi ro kẹt T+2** làm mức cắt lỗ 7–8% trên lý thuyết không đảm bảo trong 2,5 ngày đầu.
7. **Không có chức năng đặt lệnh**, và kết quả lọc **không phải khuyến nghị đầu tư**.

---

## 16. Ba câu hỏi và 4 trạng thái

Mỗi mã trong danh sách được trả lời 3 câu (`src/quant/setup_status.py`, dùng chung cho screen,
monitor và web):

| Câu hỏi | Đạt khi |
|---|---|
| **Xu hướng** | 8/8 Trend Template (nhóm A); nhóm B là "gần đạt" |
| **Nền** | đang tạo nền đạt chuẩn: VCP / nền phẳng / cốc / cốc tay cầm / nền |
| **Điểm vào** | giá từ **pivot tới +5%** = vùng mua ✓; **−5% … 0** = "sắp tới pivot" ~ (config `entry.approaching_pct`); dưới −5% = "chưa tới", trên +5% = "đã chạy xa"; nền không đang hình thành = "không áp dụng" |

Từ đó ra **5 trạng thái** (nhãn trung tính — không phải tín hiệu mua):

| Trạng thái | Khi nào |
|---|---|
| **Ở vùng pivot** | xu hướng ✓, nền ✓, giá từ pivot tới +5%, chưa thủng stop |
| **Sắp tới pivot** | xu hướng ✓, nền ✓, giá dưới pivot tối đa 5% (từ 12/10/2026; trước đó gộp vào "Ở vùng pivot") |
| **Đang theo dõi** | còn thiếu nền hoặc điểm vào |
| **Vừa breakout** | có breakout hợp lệ trong 5 phiên gần nhất |
| **Mất hiệu lực** | breakout thất bại / thủng stop / mất Trend Template trong 5 phiên gần nhất |

Nếu trong 5 phiên có nhiều sự kiện, **sự kiện gần nhất quyết định**. Trạng thái luôn kèm ghi
chú thị trường (mục 10).

---

## 17. Cổng cơ bản (BCTC quý)

**Nguồn:** trang kết quả kinh doanh theo quý của **CafeF** (tầng A — trang tổng hợp, cấp nguồn 3).
Mọi số liệu lưu với nhãn **ESTIMATE** (chưa kiểm chứng) cho tới khi đối chiếu BCTC gốc.

Hai bẫy đã gặp, đều xử lý bằng cách **từ chối** chứ không đoán:
- Nhãn ghi "tỷ đồng" nhưng số là **đồng** (2.065.597.768.695) → đọc đúng đơn vị đồng.
- CafeF hiển thị mọi công ty theo mẫu doanh nghiệp sản xuất. **Ngân hàng và công ty chứng khoán**
  lệch dòng (SSI: dòng LNST công ty mẹ trống; VCB: "doanh thu thuần" vô nghĩa). Một quý chỉ được
  nhận khi **LNST = LNST công ty mẹ + cổ đông thiểu số (sai lệch ≤ 1%)** và doanh thu thuần > 0.
  Quý không qua kiểm tra bị bỏ, lý do được ghi lại → mã đó hiện "Chưa có dữ liệu" kèm lý do.

**LNST công ty mẹ thay cho EPS** (CafeF để trống EPS; số cổ phiếu thay đổi theo thời gian nên đây
là xấp xỉ).

**Hạng** (ngưỡng trong config `sepa_screen.fundamentals`):

| Hạng | Điều kiện (quý gần nhất so với cùng kỳ năm trước) |
|---|---|
| **A+** | LNST ≥ +25%, doanh thu ≥ +15%, **và** tăng trưởng LNST tăng tốc 3 quý liền |
| **A** | LNST ≥ +25%, doanh thu ≥ +10% |
| **B** | LNST hoặc doanh thu ≥ +10% |
| **Yếu** | các trường hợp còn lại |
| **Fail** | lỗ trong quý gần nhất; hoặc LNST giảm ≥ 20%; hoặc cả LNST và doanh thu cùng giảm |
| **Chưa có dữ liệu** | < 5 quý tin cậy; thiếu quý cùng kỳ; hoặc cùng kỳ năm trước lỗ (tăng trưởng từ số âm không xác định) |

**Code 33** (Minervini) được đánh dấu khi LNST, doanh thu **và** biên lợi nhuận cùng tăng tốc 3 quý.

Tác động: **Fail → rời danh sách chính** (mục 9.1); hạng tính vào điểm tổng hợp với trọng số 10%.
"Chưa có dữ liệu" **không bao giờ** được coi là đạt hay trượt.

---

## 18. Sức mạnh ngành

**Phân ngành:** cây ngành của **Vietstock** (giống GICS), đọc từ trang hồ sơ doanh nghiệp — 3
cấp mã 2 / 4 / 6 chữ số, ví dụ MST: *Công nghiệp › Hàng hóa công nghiệp › Kỹ thuật xây dựng*.
Mặc định dùng **cấp 2 (nhóm ngành)** — cấp này tách được *Tổ chức tín dụng* (ngân hàng) khỏi
*Dịch vụ tài chính* (chứng khoán). Chỉ đọc cho các mã đủ thanh khoản, làm mới sau 30 ngày.
Mã không đọc được phân ngành **không bị gán ngành theo tên**.

Với mỗi ngành, trên các mã đủ thanh khoản:

| Chỉ số | Cách tính |
|---|---|
| **RS ngành** | trung bình điểm RS thô của các mã → xếp hạng phần trăm 1–99 giữa các ngành có **≥ 3 mã** |
| **Độ rộng** | % mã đóng cửa trên MA50 / trên MA200 |
| **Mã dẫn dắt** | các mã của ngành đang ở danh sách chính (A/B) |

RS ngành ≥ 80 được đánh dấu "ngành dẫn dắt". **[TRIỂN KHAI]** — chỉ hiển thị, **trọng số 0**
trong điểm tổng hợp cho tới khi backtest cho thấy nó có ích trên thị trường Việt Nam.

---

## 19. Kiểm tra vị thế

Thẻ **"Kiểm tra vị thế"** trong khung chi tiết (làm lại 03/10/2026). Bạn nhập **giá vốn trung bình**
(và số lượng nếu muốn); server đặt vị thế cạnh dữ liệu hệ thống đã đo — `src/quant/position_check.py`,
ngưỡng ở `position_check` trong `config/thresholds.yaml`. Giá vốn và số lượng chỉ lưu trong trình duyệt.

**Cách nhập**: theo **đồng**, kiểu Việt Nam hay kiểu số đều được — `68.500`, `68500`, `68,500` đều là
68.500 đ; `1.000` là 1.000 cổ phiếu. Nếu giá vốn chênh **hơn 5 lần** giá hiện tại
(`position_check.cost_unit_check_ratio`) — gần như luôn là nhầm đơn vị, ví dụ gõ 68,5 cho 68.500 đ —
hệ thống **hỏi lại trước khi tính**: nút "Dùng 68.500 đ" (khi nhân 1.000 ra gần giá hiện tại) hoặc
"Giữ … đ và kiểm tra". Hệ thống không tự đoán đơn vị.

**Dòng tóm tắt** đổi theo tình huống:

| Tình huống | Câu tóm tắt và quy tắc đi kèm |
|---|---|
| Lỗ quá 8% | "đã vượt mức lỗ tối đa 8%": giá tương ứng mức 8%, giá hiện thấp hơn bao nhiêu, cần tăng bao nhiêu % để về hòa vốn [SÁCH Minervini: không gồng lỗ] |
| Lỗ dưới 8% | "còn x% nữa là chạm mức lỗ tối đa": giá mức 8%, hỗ trợ gần nhất nằm trên hay dưới mức đó |
| Lãi dưới 16% | khoảng cách tới mức 8%; lãi chưa đủ để dời stop lên hòa vốn |
| Lãi từ 16% (2 × 8%) | nhắc quy tắc dời stop lên tối thiểu hòa vốn, vị trí MA50 [SÁCH: bảo vệ lợi nhuận] |

**Bảng các mốc giá** (cao xuống thấp), mỗi mốc kèm % so với giá vốn và so với giá hiện tại: giá hiện
tại, giá vốn, mức lỗ tối đa 8%, stop tham chiếu, pivot nền giá, MA50, MA200, hỗ trợ / kháng cự gần
nhất (cùng vùng với biểu đồ, mục 21).

**Các mục**:
- **Xu hướng**: số tiêu chí Trend Template đạt và ý nghĩa từng tiêu chí thiếu; giá so với MA50 / MA200;
  tín hiệu bán SEPA trong 10 phiên (thủng stop, breakout thất bại, thủng MA50 với khối lượng lớn, phiên
  giảm mạnh nhất, mất tiêu chí, climax).
- **Diễn biến gần đây**: % giá 5 phiên, số phiên giảm, khối lượng so với 50 phiên trước; khối ngoại
  ròng 5 phiên (mục 26).
- **Điều kiện gia tăng theo SEPA**: liệt kê điều kiện chưa đủ — vị thế đang lỗ (SEPA không bình quân
  giá xuống), thị trường điều chỉnh, giá chưa ở điểm mua mới, chưa đủ 8/8.
- **Cơ bản, thị trường, tin tức**: hạng BCTC và lợi nhuận / doanh thu quý gần nhất, trạng thái thị
  trường, rủi ro thanh khoản và kẹt T+2 (kèm số tiền nếu có số lượng), tin 7 ngày.

Mỗi dòng có dấu ✓ / ! / ✗ và ghi rõ quy tắc. **Không có kết luận "mua thêm / bán / giữ"** — đây là bảng
đối chiếu, quyết định thuộc về bạn.

---

## 20. Backtest các quy tắc trên dữ liệu Việt Nam

`python -m src.main backtest-sepa` — 1.209 mã, tín hiệu 15/09/2016 – 25/09/2026. Chỉ dùng dữ liệu
tới ngày tín hiệu; vào lệnh giá mở cửa phiên sau; bán giá đóng cửa sau 5/10/20/60 phiên; trừ 0,6%
phí khứ hồi. So với **nền** = mọi mã đủ thanh khoản ở ngày bất kỳ (+1,59% sau 60 phiên).
Báo cáo đầy đủ: `data/backtest/sepa_2026-09-27.md`.

Kết quả chính (chênh lệch so với nền, điểm %; `*` = lợi nhuận tuyệt đối của tín hiệu khác 0 có ý nghĩa thống kê, |t| ≥ 2 — bản thân chênh lệch so với nền chưa được kiểm định riêng):

| Quy tắc | N | Hơn nền T+20 | Hơn nền T+60 |
|---|---|---|---|
| Giữ mã đạt 8/8 Trend Template | 6.049 | −0,14 | −0,54 |
| Lần đầu đạt 8/8, tầng "Dẫn dắt mạnh" (RS ≥ 90) | 205 | −1,38 | −5,04 |
| TT6 ở 25% thay vì 30% | — | khác biệt không đáng kể | |
| Breakout VCP hợp lệ | 56 | −3,94 * | −3,67 |
| Breakout mọi loại nền, **yếu** | 2.574 | −0,92 * | −2,44 * |
| Breakout hợp lệ + 8/8 TT, thị trường **Xu hướng tăng** | 210 | +0,28 | **+2,54** * |
| Breakout hợp lệ + 8/8 TT, thị trường **Chịu áp lực** | 220 | −3,19 * | **−6,26** * |

Đọc kết quả:
- **Phần lớn quy tắc như đang cài đặt không cho thấy lợi thế** so với mua ngẫu nhiên một mã đủ
  thanh khoản. Đạt Trend Template một mình không đủ.
- Điều duy nhất nhất quán: **breakout hợp lệ của mã 8/8 khi thị trường đang tăng** tốt hơn nền;
  **cùng tín hiệu đó khi thị trường chịu áp lực thì tệ hơn rõ rệt** — đúng với nguyên tắc "chỉ
  mua khi thị trường cho phép" của SEPA.
- Breakout **yếu** (thiếu KL / đóng cửa thấp) kém hơn nền — ủng hộ việc tách hợp lệ / yếu.

Giới hạn: **thiên lệch sống sót** (không có mã đã hủy niêm yết — làm mọi con số đẹp hơn thực tế);
giá không cộng cổ tức tiền mặt; nhiều nhóm có mẫu nhỏ (< 100). **Ngưỡng chưa được đổi** theo kết
quả này — đó là quyết định của bạn.

---

## 21. Hỗ trợ / kháng cự, kênh giá song song

Bật trong menu **"Lớp hiển thị"** của biểu đồ (mục 12.1): **Hỗ trợ / kháng cự gần nhất**,
**Trendline và kênh giá**, **Pivot và stop**, **Nền giá** (đỉnh / giữa / đáy nền, đường nối các nhịp co
hẹp, mốc "Bắt đầu nền", "Đáy"). Phần mô tả bằng chữ bên dưới biểu đồ chỉ hiện khi lớp tương ứng bật.

**Hỗ trợ / kháng cự gần nhất** — `src/quant/levels.py` [TRIỂN KHAI]:
- Lấy các mức giá từng đảo chiều: đỉnh/đáy swing (chỉ dùng khi đã được xác nhận — không nhìn
  trước), và các mức giá đã bị phá qua (đỉnh/đáy 20 và 60 phiên).
- Các mức cách nhau ≤ 1,5% gộp thành **một vùng** rộng ±0,75% (vùng, không phải một đường).
- Chỉ vẽ **vùng gần giá nhất mỗi phía**: một dải xanh (hỗ trợ) và một dải đỏ (kháng cự), kèm số lần
  giá chạm vùng và lần chạm gần nhất.

**Trendline & kênh song song** — `src/quant/channel.py` [TRIỂN KHAI], giống cách vẽ tay:
- **Trendline hỗ trợ** nối 2 đáy swing, **trendline kháng cự** nối 2 đỉnh swing (đỉnh/đáy cần 5 nến
  mỗi bên, trong 250 phiên gần nhất). Giữa 2 điểm neo **không có giá đóng cửa nào xuyên qua đường**
  (cho phép lệch 1%). Lấy đường có điểm neo thứ hai **gần nhất**; nếu nhiều đường, chọn đường được
  chạm nhiều nhất rồi đến đường dài nhất.
- Sau điểm neo thứ hai, đường chạy tới **phiên đầu tiên giá đóng cửa xuyên qua (bị phá)**; nếu chưa
  bị phá thì kéo dài thêm 20 phiên về phía trước thành tia (ngày cuối là ước tính).
- **Kênh song song** (tô hồng, đường giữa nét đứt) dựng trên trendline gần nhất trong hai đường: cạnh
  còn lại song song, đi qua đỉnh cao nhất (với trendline hỗ trợ) hoặc đáy thấp nhất (với trendline
  kháng cự) trong khoảng của đường → mọi nến nằm trong kênh. Ghi: độ dốc %/tháng, độ rộng kênh, và
  **giá đang ở bao nhiêu % biên độ** (0 = cạnh dưới, 100 = cạnh trên) — hoặc ngày giá đã phá kênh.
- Trendline còn lại vẽ màu xanh lá mạ.

Trendline, kênh và vùng mô tả **nơi giá từng đảo chiều, không dự báo** nơi giá sẽ đảo chiều.

(Phần "Điểm của bạn" — tự vẽ đường mua / bán / cắt lỗ — đã bỏ ngày 03/10/2026 theo yêu cầu.)

---

## 22. Đầu ngành và lọc theo ngành

**Tab "Đầu ngành"**: với mỗi nhóm ngành (Vietstock cấp 2), lấy **3 mã có giá trị giao dịch lớn
nhất** (config `sepa_screen.industry.leaders_per_industry`) trong số mã đủ thanh khoản — gồm cả mã
mới niêm yết. Giá trị giao dịch được dùng thay cho vốn hóa vì cơ sở dữ liệu chưa có vốn hóa, nên
mã vốn hóa lớn nhưng ít giao dịch có thể không lọt top (ví dụ VCK — Chứng khoán VPS — chỉ đứng thứ 8
nhóm chứng khoán ngày 28/09 với ~62 tỷ/phiên, sau VIX, SSI, VCI, VND, SHS, HCM, TCX). Ngành xếp theo RS ngành.

Các mã đầu ngành **không phải ứng viên SEPA** (trừ khi mang nhãn A/B cạnh mã). Chúng được
lưu cùng lượt lọc (bảng `screen_industry_leaders`, không sửa lượt cũ) và **được theo dõi sự kiện,
thu tin** cùng 80 mã.

**Lọc theo ngành**: ô "Ngành" của bảng (mục 12.4), dùng được ở mọi nhóm mã.

---

## 23. Sóng ngành (nhóm bạn tự định nghĩa)

Phân ngành Vietstock quá thô cho các "sóng" thị trường: cao su (GVR, PHR, DPR, DRI) nằm chung "Hóa chất"
với DGC, DCM, DPM; cảng (GMD, VSC, PHP, VGR) nằm chung "Hạ tầng giao thông" với ACV, CII. Vì vậy các sóng
được khai báo trong **`config/themes.yaml`** — danh sách mã do bạn tự sửa, thêm sóng mới được. Khởi đầu:
**Dầu khí**, **Cao su**, **Cảng & vận tải biển**.

Với mỗi sóng, trên các mã đủ thanh khoản — `src/quant/themes.py` [TRIỂN KHAI]:

| Chỉ số | Cách tính |
|---|---|
| **RS nhóm** | trung bình điểm RS thô của các mã → xếp hạng phần trăm **so với mọi ngành** (cần ≥ 2 mã) |
| **1 tháng / 3 tháng** | lợi nhuận trung vị 21 / 63 phiên |
| **Trên MA50** | % mã đóng cửa trên MA50 |
| **Đang mạnh** | RS nhóm ≥ 80 |

Tác động: mã thuộc sóng đang mạnh **và** đạt điều kiện nhóm C được **xếp đầu nhóm C**. Sóng ngành
**không bao giờ nâng một mã lên A/B** — chuẩn SEPA giữ nguyên. Trên web: nhãn cam tên sóng cạnh mã,
phần "Sóng ngành" trong ô lọc "Ngành", và bảng sóng ở đầu tab "Ngành & sóng".

Ngày 29/09/2026: Dầu khí RS 99, Cảng & vận tải biển RS 93, Cao su RS 80 — cả ba đang mạnh. Mã dẫn sóng
đã ở danh sách chính: PVP, PET, PPT, GMD, PHP, DRI, TRC. Đầu nhóm C: PVT, BSR, PVS, GVR, GAS, VOS, HAH —
BSR/PVT còn thiếu TT4 (MA50 chưa vượt MA150/MA200) và nền trước đó quá sâu: nhịp hồi chữ V chưa tạo nền.

---

## 24. Danh mục của tôi

Nhóm **"Của tôi"** ở thanh bên: các mã **bạn tự chọn** để theo dõi, dù bộ lọc có chọn hay không
(VD: PHP trước khi hạ ngưỡng thanh khoản, hoặc VSC để xem nó có hồi phục không).

- **Thêm**: gõ mã vào ô "Thêm mã" phía trên bảng (có thể kèm ghi chú) rồi bấm "+ Thêm", hoặc bấm **☆** ở
  đầu dòng của bất kỳ nhóm nào hay cạnh tên mã trong khung chi tiết.
  **★** = đã có trong danh mục; bấm lại để bỏ.
- Chỉ nhận mã cổ phiếu 3 ký tự đang niêm yết trên HOSE/HNX/UPCoM; mã sai bị từ chối kèm lý do.
- Bảng giống các nhóm khác (ba kiểu Tổng quan / Kỹ thuật / Dòng tiền); RS "–" nếu mã dưới ngưỡng thanh
  khoản; nhãn A / B / C / "đầu ngành" cho biết mã có trong danh sách lọc không; ghi chú hiện dưới tên công ty.
- Các mã trong danh mục **được theo dõi sự kiện (monitor) và thu tin (news) hằng ngày** cùng danh sách lọc.
- Danh mục lưu trong cơ sở dữ liệu (bảng `user_watchlist`), giữ nguyên qua các lần lọc lại.
- Đây là danh sách theo dõi, không phải khuyến nghị.

Đây là thao tác ghi **duy nhất** của trang web. Nó chỉ nhận lệnh từ chính trang dashboard trên máy
bạn — một trang web khác mở trong trình duyệt không thể thêm/bớt mã.

---

## 25. Chỉ báo kỹ thuật kiểu TradingView

Nút **"Kỹ thuật"** phía trên bảng (mục 12.4) chuyển sang bảng giống bộ lọc *Technicals* của TradingView,
cho bất kỳ nhóm mã nào ở thanh bên (kể cả Của tôi, Đầu ngành, Tất cả theo dõi); khung chi tiết (thẻ
"Phân tích SEPA") có bảng từng phiếu. Tính hằng ngày trong bước
theo dõi (`src/quant/tv_ratings.py`, `src/quant/candles.py`; tham số `tv_ratings`, `candles` trong
`config/thresholds.yaml`).

| Cột | Ý nghĩa |
|---|---|
| Xếp hạng kỹ thuật | trung bình của hai xếp hạng bên cạnh |
| Xếp hạng MA | 15 phiếu: SMA và EMA 10/20/30/50/100/200, Ichimoku (đường cơ sở 26), VWMA 20, Hull MA 9 — tích cực khi đường MA nằm dưới giá |
| Xếp hạng dao động | 11 phiếu: RSI 14, Stoch 14-3-3, CCI 20, ADX 14, AO, Momentum 10, MACD 12-26, Stoch RSI, Williams %R, Bull Bear Power, Ultimate Oscillator |
| RSI, Mom, AO, CCI, %K, %D | giá trị chỉ báo phiên gần nhất |
| Mẫu hình | mẫu nến phiên gần nhất (doji, búa, sao băng, nhấn chìm, harami, sao mai/sao hôm…, theo skill 03); bỏ qua phiên kịch trần / sàn; cần phiên sau xác nhận |

Điểm = (phiếu tích cực − phiếu tiêu cực) / số phiếu, trong [−1, 1]: > 0,5 **Tích cực mạnh**, 0,1–0,5
**Tích cực**, ±0,1 **Trung tính**, ngược lại Tiêu cực / Tiêu cực mạnh (tương ứng "Mua mạnh… Bán mạnh" trên
TradingView — đổi tên vì dự án không dùng nhãn mua/bán).

Đối chiếu ngày 03/10/2026 với ảnh bộ lọc TradingView: giá trị RSI / CCI / Stoch khớp từng số ở các mã
không có điều chỉnh cổ tức (lệch nhỏ ở mã TradingView điều chỉnh cổ tức tiền mặt); xếp hạng tổng và MA
khớp gần hết; xếp hạng dao động khớp khoảng 15/19 — TradingView không công bố đủ chi tiết một số quy tắc
(cách xác định xu hướng cho Stoch RSI / Bull Bear Power), nên có thể lệch 1 phiếu. Quy tắc Momentum theo
dấu và Stoch cần cắt nhau được chọn vì khớp TradingView nhiều nhất.

Đây là chỉ báo **ngắn hạn**, không phải quy tắc SEPA, chưa kiểm định trên dữ liệu Việt Nam: chỉ hiển thị,
không tính điểm, không đổi nhóm A/B/C.

---

## 26. Khối ngoại và khối nội

`python -m src.main flow [--days 10] [--tickers PHP]` lấy từ DNSE (`/price/{mã}/foreign-trading`,
bảng khớp lệnh) **khối lượng cổ phiếu** khối ngoại mua và bán mỗi phiên cho các mã đang theo dõi, lưu
vào `flow_daily` (`foreign_buy_volume`, `foreign_sell_volume`, cùng giá trị mua / bán). Phiên đã có thì
bỏ qua. Tác vụ hằng ngày chạy lệnh này sau `monitor`.

- **Khối ngoại ròng** = mua − bán (cổ phiếu); giá trị ròng tính bằng tỷ đồng.
- **Khối nội** = tổng khối lượng khớp của phiên − phần khối ngoại (nội mua = tổng − ngoại mua, nội bán
  = tổng − ngoại bán). DNSE không tách cá nhân / tổ chức / tự doanh trong nước, nên đây chỉ là phần
  còn lại sau khối ngoại; khối nội ròng luôn bằng khối ngoại ròng đổi dấu.
- Bảng **Dòng tiền** hiện phiên gần nhất có dữ liệu; ngày màu vàng là dữ liệu cũ hơn phiên giá.
- Thẻ **Dòng tiền** của mỗi mã: tổng ngoại ròng 1 / 5 / 10 / 20 **phiên giao dịch** (cổ phiếu, tỷ đồng, số phiên
  mua ròng), biểu đồ cột ngoại ròng từng phiên, bảng mọi phiên. Phiên DNSE không có số liệu khối ngoại để trống và
  ô tổng ghi "k/n phiên có số liệu" (trước 12/10/2026 "5 phiên" là 5 dòng có số liệu, có thể trải nhiều tuần).

Chỉ hiển thị, không tính vào điểm hay nhóm.

## 27. Học: mẫu nến và mô hình giá

Mục **Học** ở thanh bên, hai cột, mỗi mẫu có hình vẽ minh họa (`src/web/static/learn.js`), cách nhận
biết, ý nghĩa, cần xác nhận gì:

- **Kiến thức SEPA cốt lõi** (12/10/2026): Trend Template 8 tiêu chí, bốn giai đoạn của một cổ phiếu, pivot và
  vùng mua, cắt lỗ 7–8%, quản trị vốn, thị trường chung.

- **Mẫu hình nến** — nến đơn: thân dài (marubozu), con quay, doji, doji chuồn chuồn, doji bia mộ, búa,
  người treo cổ, búa ngược, sao băng; cụm đảo chiều tăng: nhấn chìm tăng, harami tăng, đường xuyên, sao
  mai, ba chàng lính trắng; cụm đảo chiều giảm: nhấn chìm giảm, harami giảm, mây đen che phủ, sao hôm,
  ba con quạ đen. Nhãn "tự nhận dạng" = hệ thống phát hiện mẫu này ở cột Mẫu nến (mục 25). Số liệu
  Bulkowski là thống kê thị trường Mỹ.
- **Mô hình giá** — nền giá trong xu hướng tăng: cốc tay cầm, VCP, nền phẳng, cờ cao hẹp, tam giác tăng;
  đảo chiều: hai đáy (W), vai đầu vai, **cây thông** (đỉnh nhọn: tăng dựng đứng nhiều phiên trần rồi
  giảm ngược về chân sóng — ví dụ HID 09/2026, có nút mở biểu đồ HID). Ngưỡng của các nền lấy từ
  skill `sepa-vn-screening` (tài liệu 02).

Hình vẽ là minh họa, không theo tỉ lệ thật; không mẫu nào chắc chắn đúng.

## 28. Logo công ty

`python -m src.main logos [--tickers PHP] [--refresh]` tải logo của các mã đang theo dõi từ Vietstock
(`https://finance.vietstock.vn/image/<mã>`, cùng ảnh trang hồ sơ doanh nghiệp của họ dùng) về
`data/logos/`, **mỗi mã một lần** — trang web lấy logo từ máy bạn, không gọi Vietstock mỗi lần mở.
Tác vụ hằng ngày chạy lệnh này sau `news` nên mã mới (lọc lại, thêm vào Của tôi) tự có logo hôm sau.

- Định dạng được đọc từ chính tệp ảnh (Vietstock luôn ghi "jpeg" dù nhiều logo là PNG).
- Mã Vietstock không có logo được ghi nhớ trong `data/logos/_missing.json` và chỉ hỏi lại sau 30 ngày
  (`logos.retry_missing_days`); trên web, mã đó hiện ô màu với chữ viết tắt.
- Logo nằm trên nền trắng (logo được thiết kế cho nền trắng), kể cả ở chế độ tối.
- Chỉ để nhận diện nhanh: không ảnh hưởng điểm, nhóm hay bất kỳ phép tính nào.

## 29. Toàn thị trường

Mục **Toàn thị trường** ở thanh bên: mọi mã HOSE / HNX / UPCoM có dữ liệu giá (~1.470 mã), cùng bảng và
cùng kiểu menu lọc với các nhóm khác.

- **Cột**: Mã · Giá và % · Phiên (ngày giá của mã; vàng = cũ hơn phiên mới nhất) · RS · Xu hướng (8 chấm
  Trend Template) · Trên MA (50 / 150 / 200, xanh = giá trên đường) · Cách đỉnh 52 tuần · Nền giá ·
  Thanh khoản · Cơ bản.
- **Lọc**: Xu hướng (đạt 8/8, gần đạt 6–7/8, trên MA200, dưới MA200, dữ liệu cần kiểm tra), Ngành (gồm
  sóng ngành), Sàn, Thanh khoản (gồm "Dưới 3 tỷ"), ô tìm. Lọc và sắp xếp chạy ngay trong trình duyệt.
- Hiện 150 mã mỗi lần; "Hiện thêm" / "Hiện tất cả".

**Cách tính** — `src/quant/market_view.py`, dùng chính các hàm của bộ lọc SEPA:
- **RS** lấy từ lượt lọc gần nhất (cùng thang với danh sách). Mã dưới ngưỡng thanh khoản 3 tỷ không có RS,
  nên tiêu chí TT8 hiện màu xám "chưa đo được", không tính là trượt — vì vậy mã dưới ngưỡng không thể
  "đạt 8/8", giống bộ lọc.
- Mã có chuỗi giá **bị gắn cờ** (vd chưa điều chỉnh quyền, niêm yết lại) chỉ hiện giá, nhãn "dữ liệu cần
  kiểm tra" — không tính chỉ báo trên dữ liệu nghi sai.
- Mã niêm yết chưa đủ 252 phiên chỉ đo được TT7 (như mục 9.3).

**Thời gian**: dựng bảng ~15 giây (`python -m src.main market`). Phần tốn thời gian là tải giá mới cho toàn
thị trường (`backfill`, mỗi mã một lượt gọi DNSE, ~30–45 phút) — tác vụ hằng ngày làm việc này sau các bước
chính, nên bảng có giá phiên mới nhất vào khoảng 16:30.

Đây là bảng để nghiên cứu, **không phải danh sách ứng viên**: không đổi lượt lọc, điểm hay nhóm. Muốn lọc
SEPA đầy đủ (nhóm A/B/C) vẫn dùng `python -m src.main screen`.

## 30. Rà soát biểu đồ (04/10/2026)

`python scripts/audit_chart.py --round 1|2|3` — 14 mã (cả 3 sàn, có mã không theo dõi) và VN-Index; kết quả
lưu ở `data/audit/`.

| Vòng | Cách kiểm | Kết quả |
|---|---|---|
| 1. Tính lại độc lập | MA20/50/150/200, RSI 14 (Wilder), MACD 12/26/9 viết lại bằng vòng lặp từ định nghĩa (tài liệu 04), so từng điểm với số gửi lên biểu đồ | Khớp cả 14/14; lệch chỉ do làm tròn (≤ 0,005). Tải 520 phiên và cách khởi tạo EMA làm lệch < 1e-9 |
| 2. Nguồn thứ hai | Giá VNDirect so với giá DNSE 260 phiên, tính lại chỉ báo trên giá VNDirect | Giá khớp 258–260/260 ở 13/14 mã; ABB lệch ~0,4% trước ngày chia cổ tức tiền (VNDirect điều chỉnh cổ tức tiền, DNSE thì không) → MA200 lệch 0,11% |
| 3. Các đường kẻ | Mỗi đường kiểm theo quy tắc định nghĩa nó: vùng (độ rộng, phía, % cách giá), trendline (điểm neo là đỉnh / đáy swing, không đóng cửa xuyên qua, ngày phá, giá trị hôm nay, tia), kênh (song song, cạnh đối diện chứa mọi nến), nền (đỉnh, đáy, giữa, độ sâu), pivot / stop | 465/465 đúng |

Sửa sau rà soát:
- **Khung RSI lệch 14 phiên khi kéo / phóng to biểu đồ** (các khung đồng bộ theo số thứ tự nến, khung RSI thiếu
  14 phiên đầu). Nay mọi khung có đủ một điểm mỗi phiên; đã kiểm lại trên trình duyệt: ba khung cùng khoảng ngày.
- Chữ "chạm N lần" ở vùng hỗ trợ / kháng cự thực ra là **số phiên giá đi qua vùng** (số lần đảo chiều thật ở vùng
  chỉ 0–4). Nay ghi đúng nghĩa và ghi vùng được hình thành từ đâu (đỉnh / đáy swing, mức từng vượt lên / thủng xuống).
- MACD có số thập phân cho cổ phiếu giá thấp và chỉ số; chú thích hiện thêm MACD / Signal / Hist.

Lưu ý còn lại:
- **Khối lượng VN-Index của DNSE** cao hơn VNDirect 11–40% ở phần lớn các phiên nhưng bằng nhau ở vài phiên
  (03/09, 14–15/09) — có thể do lúc tính lúc không tính giao dịch thỏa thuận. Ngày phân phối so khối lượng hôm nay
  với hôm trước nên bị ảnh hưởng: 7 ngày theo DNSE, 8 ngày theo VNDirect (02/10). Chưa xác minh được bằng nguồn
  thứ ba; chưa đổi gì.
- Cột khối lượng tô xanh / đỏ theo **giá đóng cửa so với phiên trước** (cách IBD đếm ngày tăng / giảm), còn
  TradingView mặc định tô theo màu nến (đóng cửa so với mở cửa) — nên một nến đỏ có thể có cột khối lượng xanh.
- Râu nến được phép chọc qua cạnh trendline của kênh (chỉ giá đóng cửa bị giới hạn 1%).

## 31. Kết hợp DNSE và SSI

Từ 04/10/2026 hệ thống có thêm nguồn **SSI FastConnect Data** (chỉ dữ liệu; phần đặt lệnh của SSI không bao giờ
được kết nối). Phân công:

| Việc | Nguồn |
|---|---|
| Lịch sử dài, chuỗi giá chuẩn để tính chỉ báo, lọc, biểu đồ | **DNSE** (đã kiểm chứng, đã điều chỉnh cổ tức bằng cổ phiếu) |
| Ảnh chụp cuối ngày toàn sàn: giá tham chiếu, khớp lệnh và thỏa thuận tách riêng, khối ngoại | **SSI** (một lượt gọi cho cả sàn) |
| Khối lượng VN-Index khớp lệnh, số mã tăng / giảm | **SSI** |
| Đối chiếu hằng ngày | cả hai |

`python -m src.main crosscheck` so sánh (báo cáo `data/audit/crosscheck_<ngày>.json`):
- **Giá đóng cửa** từng phiên; nếu DNSE khớp *giá đã điều chỉnh* của SSI thì đó là mã có quyền về sau, không phải lỗi.
- **Khối lượng**: DNSE khớp với khối lượng khớp lệnh hay khớp lệnh + thỏa thuận của SSI.
- **Khối ngoại** mua / bán.
- **Khối lượng VN-Index** của DNSE từng phiên là loại nào, và số **ngày phân phối** theo từng định nghĩa
  (DNSE, SSI khớp lệnh, SSI khớp + thỏa thuận, tổng cổ phiếu HOSE).
- **Ngày có quyền**: giá tham chiếu khác giá đóng cửa hôm trước (UPCoM: giá bình quân hôm trước); kèm DNSE đã
  điều chỉnh chuỗi giá chưa — chưa thì nên tải lại mã đó.

Đây mới là bước **chạy song song để kiểm chứng**: không có gì trong dữ liệu DNSE bị thay. Sau vài tuần đọc báo cáo,
nếu bạn đồng ý, bước tiếp theo là cập nhật hằng ngày toàn thị trường bằng ảnh chụp SSI (vài giây thay vì ~29 phút),
khối ngoại toàn thị trường từ SSI và đếm ngày phân phối trên khối lượng khớp lệnh. Cách lấy khóa: `run.md` mục 6;
chi tiết kỹ thuật: `docs/SSI.md`.

## 32. % thay đổi theo giá tham chiếu (06/10/2026)

Bảng giá các công ty chứng khoán tính % thay đổi so với **giá tham chiếu**, không phải giá đóng cửa phiên trước:

| Sàn | Giá tham chiếu |
|---|---|
| HOSE, HNX | giá đóng cửa phiên trước (trừ ngày giao dịch không hưởng quyền: sàn hạ tham chiếu) |
| UPCoM | **giá khớp bình quân** của phiên trước — khác giá đóng cửa gần như mỗi ngày |

Ví dụ PHP phiên 05/10/2026: đóng 49.500, đóng cửa phiên trước 46.700 → +6,00%; giá tham chiếu ≈ 47.300 → bảng giá
ghi **+4,65%**.

`python -m src.main refprices` đọc giá tham chiếu / trần / sàn của các mã đang theo dõi từ DNSE (lệnh chỉ đọc
`/price/{mã}/secdef`, 1 lượt / mã, ~1,5 phút) và lưu theo phiên (bảng `reference_prices`). `start` và tác vụ 15:30
tự chạy bước này.

- DNSE công bố giá của phiên kế tiếp **vào buổi tối** (20:46 ngày 05/10/2026 cho phiên 06/10; một số mã HNX lúc 08:00
  sáng hôm sau). Đọc lúc 15:30 vẫn là giá của phiên vừa đóng; đọc sau ~20:45 là giá của phiên mai. Mỗi giá được gắn
  với phiên theo thời điểm DNSE công bố: trước 15:00 ngày làm việc → phiên hôm đó; sau đó → ngày làm việc kế tiếp.
- Web (bảng, trang chi tiết, chú thích nến ngày, Toàn thị trường) tính % **so với giá tham chiếu** khi đã đọc được giá
  của đúng phiên đó; rê chuột lên % để xem giá tham chiếu.
- **Phiên bị lỡ, mã UPCoM** (06/10/2026): giá tham chiếu = **giá khớp bình quân phiên giao dịch trước** của mã, làm
  tròn tới bước giá 100 đ (quy tắc của sàn); trần / sàn = ±15% quanh đó. Giá bình quân lấy từ dữ liệu khớp lệnh của
  DNSE (`avgPrice`, 1 lượt / mã / phiên). Mỗi lần chạy, quy tắc được **kiểm lại** với giá DNSE công bố của các mã UPCoM
  đang theo dõi (06/10/2026: 13/13 khớp; PHP bình quân 02/10 = 47.275 → 47.300 = TC trên bảng giá phiên 05/10, nên
  PHP 05/10 = **+4,65%**); khớp dưới 90% thì lần đó không bù, mã nào lệch thì bỏ mã đó. Giá DNSE công bố luôn được ưu
  tiên. Rê chuột lên % thấy "giá bình quân phiên … làm tròn 100 đ".
- `refprices --market` bù như trên cho phiên mới nhất của **mọi mã UPCoM** (~230 lượt, ~5 phút) để mục Toàn thị trường
  cũng khớp bảng giá; `start` và tác vụ 15:30 tự chạy.
- HOSE / HNX: giá tham chiếu = giá đóng cửa phiên trước, trừ ngày giao dịch không hưởng quyền — không suy ra được từ
  giá, nên không bù; % so với giá đóng cửa trùng bảng giá trừ những ngày đó (với cổ tức bằng cổ phiếu, DNSE đã điều
  chỉnh giá cũ nên vẫn trùng; với cổ tức tiền mặt thì lệch đúng phần cổ tức).
- Vẫn không có giá tham chiếu (vd mã UPCoM không có lệnh khớp phiên trước, tạm ngừng giao dịch) → % so với giá đóng
  cửa phiên trước, ghi rõ; mã UPCoM khi đó có **gạch chấm** dưới %. Không đoán.
- **Mã có quyền (thưởng / chia cổ phiếu)** — 06/10/2026: tối trước ngày GDKHQ, DNSE điều chỉnh lại **toàn bộ lịch sử giá**
  (GMD thưởng 2:1, GDKHQ 07/10: mọi giá cũ × 2/3, nến 06/10 ghi 52.000 trong khi khớp thực tế 78.000; cùng tối có BIC,
  KDH, MWG, PIA, PPS, TV1, WTC). Trước đây bước kiểm "nến đã chốt chưa" so nến (đã điều chỉnh) với khớp lệnh (chưa điều
  chỉnh), thấy lệch nên **bỏ mất nến 06/10** của 8 mã đó; giá tham chiếu đã lưu (thang cũ) so với giá mới cho GMD −33%.
  Đã sửa:
  - Nến lệch khớp lệnh theo **cùng một hệ số ở cả mở / cao / thấp / đóng** là nến đã điều chỉnh, được giữ (nến chưa chốt
    không lệch đều như vậy).
  - Mỗi lần DNSE điều chỉnh, hệ số và **phiên cuối còn ở thang cũ** được ghi vào bảng `price_adjustments`. Giá tham chiếu
    lưu nguyên như sàn công bố; khi tính %, giá đóng cửa của phiên thang cũ được đưa về thang cũ (làm tròn theo bước giá)
    để so với giá tham chiếu gốc — ra đúng con số bảng giá (GMD 06/10: +0,26%; MWG +2,15%; TV1 +0,49%; WTC +2,06%).
  - Lưới an toàn: % so với tham chiếu vượt biên độ sàn (+1 điểm) là không thể → coi như khác thang giá, dùng giá đóng cửa.
- Lưu ý khi xem mã sắp GDKHQ: từ tối trước ngày GDKHQ, **giá trên dashboard là giá đã điều chỉnh** (GMD 52.000), giống biểu
  đồ của DNSE / TradingView; bảng giá hôm đó vẫn ghi giá thật (78.000). Từ phiên GDKHQ hai bên trùng lại. % thay đổi thì
  luôn theo bảng giá.
- Cờ "đóng cửa ở giá trần" (ghi chú khối lượng khi breakout) dùng giá trần thật khi có.
- Biểu đồ tuần / tháng: % so với giá đóng cửa tuần / tháng trước (như TradingView).
- Việc lọc, ngày tăng / giảm, ngày phân phối vẫn so với giá đóng cửa phiên trước như phương pháp gốc.

## 33. Lịch sử giá 11 năm (06/10/2026)

Trước đây mỗi mã chỉ lưu ~5 năm (từ 22/09/2021); biểu đồ tháng vì thế chỉ có ~60 nến và đường MA40 tháng mới bắt đầu
từ cuối 2024. Nay `collection.backfill_history_days: 4000` (~11 năm — toàn bộ DNSE có, từ 15/09/2015, vẫn đúng 1 lượt
gọi mỗi mã). Mã mới niêm yết sau 2015 có lịch sử từ ngày niêm yết.

- `python -m src.main backfill --deepen` tải bù **một lần** cho các mã đang lưu ít hơn (~30 phút toàn thị trường). Mỗi mã
  được đọc trọn chuỗi và **thay cả chuỗi** (để phần cũ và mới cùng một lần điều chỉnh cổ tức bằng cổ phiếu của DNSE);
  lượt đọc ngắn hơn cái đang lưu thì không dùng. Mã đã kiểm được ghi vào `data/history_depth.json`, lần sau bỏ qua.
- Cập nhật hằng ngày vẫn như cũ (chỉ đọc phần mới). Việc lọc chỉ đọc 1–2 năm gần nhất nên kết quả lọc không đổi vì có
  thêm lịch sử cũ.
- Bản sao cơ sở dữ liệu trước khi tải bù: `data/agent.db.bak-20261006`.
- Kết quả 06/10/2026: 1.315 mã được thêm lịch sử (2,66 triệu phiên, 49 phút, không mã nào lỗi); 1.508 mã có giá, cơ sở
  dữ liệu ~400 MB. Mã có lịch sử từ 2015: 781; còn lại bắt đầu từ ngày niêm yết. Giá từ 07/2025 tới nay của mọi mã
  **không đổi** (so từng phiên với bản sao) nên danh sách lọc, trạng thái, cảnh báo không đổi. 18 mã UPCoM gần như không
  giao dịch (thanh khoản < 0,15 tỷ, không mã nào trong danh sách theo dõi) bị gắn cờ "dữ liệu cần kiểm tra" vì 300 phiên
  gần nhất của chúng kéo về 2020–2024 và chạm phiên nhảy giá quá biên độ; 6 mã được gỡ cờ.
