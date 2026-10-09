# Mẫu hình nền giá, điểm vào, cơ bản, thoát lệnh/rủi ro, cổng thị trường

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

## 3. Mẫu hình nền giá trong SEPA và CAN SLIM

**Nguồn chính**: TLSMW, TTLC; William J. O'Neil, *How to Make Money in Stocks* (HTMMS, bản 4, 2009); các bài IBD (đăng lại trên Yahoo Finance/Nasdaq). Nhiều con số O'Neil chỉ đối chiếu được qua nguồn thứ cấp (LuxAlgo, ChartMill…) — đã đánh dấu.

### 3.0 Khái niệm chung

- **Nền giá (base)**: vùng điều chỉnh/đi ngang sau một đợt tăng trước đó.
- **Xu hướng tăng trước đó (prior uptrend)**: O'Neil yêu cầu cổ phiếu đã tăng **khoảng ≥ 30%** trước khi hình thành nền (cup with handle) [THỨ CẤP — LuxAlgo trích O'Neil].
- **Pivot / buy point**: O'Neil: đỉnh cao nhất của vùng xác định + **10 cent** [TRANG GỐC — IBD]. Minervini: "điểm mua tối ưu, mức giá kêu gọi hành động", hình thành khi kết thúc vùng tích lũy [SÁCH — TTLC].
- **Khối lượng breakout**: tối thiểu **40–50% trên mức trung bình** (IBD so với trung bình 50 ngày) [TRANG GỐC — IBD/Yahoo; THỨ CẤP — LuxAlgo]. Minervini nhấn mạnh khối lượng tăng mạnh khi breakout nhưng **không tìm thấy con số % riêng của Minervini — UNVERIFIED**.
- **Vùng mua (buy zone)**: tối đa **5% trên buy point**; không mua nếu đã vượt quá 5% (extended) [THỨ CẤP — IBD qua Yahoo, LuxAlgo].
- **Đếm nền (base count)**: nền thứ 3 trở đi ("late-stage") thất bại thường xuyên hơn; một nền mới chỉ được tính là giai đoạn tiếp theo nếu cổ phiếu đã tăng **≥ 20%** từ buy point trước [TRANG GỐC — IBD/Nasdaq "base-on-base"; THỨ CẤP — LuxAlgo].
- **Reset số đếm nền (base-count reset)**: nếu **đáy của nền hiện tại thủng (undercut) đáy của nền trước**, số đếm được đặt lại và nền mới hoàn thành trở thành **nền giai đoạn 1**; trong thị trường gấu, số đếm của mọi cổ phiếu thường được reset [THỨ CẤP — IBD qua Nasdaq "Why Counting Bases Is A Crucial Skill", MarketSmith India]. Code [TRIỂN KHAI]: `if base_low[k] < base_low[k-1]: stage = 1`; `elif gain_from_prev_pivot >= 0.20: stage += 1`; `else: base_on_base (giữ nguyên stage)`.
- **Độ dài tối thiểu**: O'Neil — trừ high tight flag, flat base (5–6 tuần) và square box (4–7 tuần), các nền đáng tin nhất cần tối thiểu **7–8 tuần** [THỨ CẤP — Scribd "Base Counting Notes" trích O'Neil].

### 3.1 VCP — Volatility Contraction Pattern (Mẫu hình co hẹp biến động)

**Nguồn**: TLSMW ch. 10; TTLC. Nguồn thứ cấp: Deepvue, TraderLion (403 trong phiên này), finermarketpoints, LuxAlgo.

| Thành phần | Quy tắc | Độ tin cậy |
|---|---|---|
| Bản chất | Giá đi từ **biến động lớn ở bên trái** nền sang **biến động nhỏ ở bên phải**; mỗi lần co hẹp cho thấy nguồn cung bị hấp thụ | [SÁCH] |
| Tiền điều kiện | Cổ phiếu phải vượt Trend Template (Stage 2) | [SÁCH] |
| Số lần co hẹp (T count) | "**Hai đến bốn**, đôi khi **năm hoặc sáu**" — cả câu được quy cho sách của Minervini | [SÁCH] qua tóm tắt (Elearnmarkets TLSMW; LuxAlgo trích TLSMW/TTLC). Config: cho phép 2–6, điển hình 2–4 |
| Chuỗi độ sâu | Mỗi đợt điều chỉnh nông hơn đợt trước (ví dụ minh họa 20% → 10% → 5%); mỗi đáy cao hơn đáy trước | "nông dần" [SÁCH]; "≈ một nửa" chỉ là **ví dụ minh họa**, không phải quy tắc — UNVERIFIED; "đáy nâng dần" [THỨ CẤP] |
| Độ sâu đợt đầu | Thường **10–35%** trong thị trường tăng; sâu hơn (tới ~60%) có thể chấp nhận trong/sau thị trường gấu. Một scanner trích "rulebook" 15–35% (ưu tiên 15–25%) | **[THỨ CẤP], được quy cho Minervini nhưng không tìm thấy trích dẫn trang — UNVERIFIED**; không gắn [SÁCH] |
| Thời gian | Nền kéo dài từ vài tuần đến nhiều tháng; nguồn thứ cấp nói 3–65 tuần | **UNVERIFIED** |
| Khối lượng | Khối lượng co lại cùng với giá; **cạn kiệt (dry-up)** rõ rệt gần điểm pivot. Các nhịp giảm sau có khối lượng thấp hơn các nhịp giảm trước | [SÁCH] ("volume contracts reasonably") + [THỨ CẤP] cho chi tiết |
| Pivot | Vùng giá chặt (tight) cuối cùng ở bên phải; buy point = đỉnh của vùng co hẹp cuối cùng | [SÁCH] |
| Ký hiệu "footprint" | `{số tuần}W {đợt sâu nhất}/{đợt nông nhất} {số lần}T`, ví dụ `19W 19/7 4T` = nền 19 tuần, co hẹp lớn nhất 19%, nhỏ nhất 7%, 4 lần | [THỨ CẤP — nhiều nguồn thống nhất; Minervini dùng trong sách và mạng xã hội] |

**Tiêu chí đo được đề xuất** [TRIỂN KHAI]:
```
contractions = danh sách các nhịp (swing_high -> swing_low) trong nền, xác định bằng zigzag/pivot với ngưỡng tối thiểu
depth_i = (swing_high_i - swing_low_i) / swing_high_i
Điều kiện:
  2 <= len(contractions) <= 6
  depth_1 trong [depth_min, depth_max]            # config, mặc định 0.10–0.35
  depth_{i+1} < depth_i  với mọi i                  # nông dần (có thể cho dung sai)
  swing_low_{i+1} > swing_low_i                     # đáy nâng dần (THỨ CẤP)
  depth_last <= last_contraction_max                # ví dụ <= 0.10, config
  mean(volume trong nhịp cuối) < mean(volume trong nhịp đầu)
  vol_dryup: min(volume 5–10 phiên cuối) < vdu_ratio * SMA50(volume)   # vdu_ratio trong config; UNVERIFIED số cụ thể
pivot = max(high) của nhịp co hẹp cuối
```

**Điểm vào "cheat" và 3C** [SÁCH + THỨ CẤP]:
- **3C = Cup Completion Cheat**: điểm vào sớm hơn trong một nền dạng cup, **trước khi** có tay cầm (handle). Bốn bước (TTLC, qua Elearnmarkets): (1) nhịp giảm (điều chỉnh trong Stage 2), (2) nỗ lực phục hồi chưa xác nhận, (3) **nghỉ (pause) nằm gọn trong 5–10% từ đỉnh đến đáy**, khối lượng cạn kiệt và giá chặt, (4) mua khi giá vượt đỉnh của vùng nghỉ [SÁCH].
- **Phân vùng theo chiều dọc của cup** [THỨ CẤP — profitvisionlab, UNVERIFIED từ sách]: Handle (1/3 trên), Cheat (1/3 giữa), Low cheat (1/3 dưới). Khoảng cách stop tham khảo 5–8% / 3–6% / 2–4% — **UNVERIFIED**.
- **Low cheat**: cheat hình thành ở 1/3 dưới của nền; pivot thấp hơn; stop gần hơn [THỨ CẤP].

**Dấu hiệu thất bại** [SÁCH + THỨ CẤP]: nền "rộng hơn với nhiều dao động lên xuống" (không co hẹp); điều chỉnh sâu hơn **2–3 lần** mức giảm của chỉ số chung [SÁCH — TLSMW]; breakout trên khối lượng thấp; giá quay lại dưới pivot và thủng đáy nhịp cuối; sau breakout không giữ được MA20 [SÁCH — TLSMW theo Elearnmarkets].

### 3.2 Các mẫu hình nền O'Neil/IBD (Minervini tham chiếu)

| Mẫu hình | Tiền điều kiện | Độ sâu | Thời gian | Quy tắc hình dạng | Khối lượng | Buy point | Dấu hiệu lỗi | Nguồn |
|---|---|---|---|---|---|---|---|---|
| **Cup with handle (Cốc tay cầm)** | Tăng trước ≥ ~30% | Cup: **12–33%** thị trường bình thường (một số nguồn: 12–15% đến 33%); có thể 40–50%+ sau thị trường gấu | Cup **7–65 tuần** (thường 3–6 tháng); tối thiểu ~7 tuần tính cả handle | Đáy chữ U (không phải V); handle nằm ở **nửa trên** của nền; handle **dốc xuống**, giảm **≤ 8–12%** từ đỉnh handle (trong thị trường tăng); handle ≥ **1 tuần**; handle nên trên MA50 (10 tuần) | Khối lượng cạn trong handle; breakout ≥ 40–50% trên trung bình | Đỉnh handle + 10 cent | Handle **nêm lên (wedging up)** dọc theo đáy; handle ở nửa dưới nền; handle quá sâu; đáy V | [SÁCH/THỨ CẤP] HTMMS qua LuxAlgo, ChartingLens, Yahoo/IBD |
| **Cup without handle** | Như trên | Giảm tới **30–33%** từ đỉnh 52 tuần/đỉnh lịch sử | "Ít nhất **6 tuần**" theo một bài IBD-trích dẫn; quy tắc cup chung của O'Neil ≈ **7 tuần** tối thiểu, cup ngắn hơn dễ thành chữ V — **UNVERIFIED, tham số hóa** (`cup_nohandle_min_weeks`, mặc định 7) | Phục hồi không có nhịp giảm đáng kể nào | Breakout ≥ 40–50% trên trung bình | Đỉnh **bên trái** của cup + 10 cent | Như cup with handle | [TRANG GỐC] IBD qua Nasdaq/kết quả tìm kiếm |
| **Flat base (Nền phẳng)** | Thường là nền thứ hai: cổ phiếu vừa breakout từ nền sâu hơn, tăng ≥ 20% | **≤ ~15%** | **≥ 5 tuần** (5–6 tuần) | Đi ngang trong biên hẹp | Yên ắng trong nền; breakout ≥ 40% trên trung bình 50 ngày | Giá cao nhất trong nền + 10 cent | Nền sâu > 15%; biên độ rộng | [TRANG GỐC] IBD qua Yahoo, LuxAlgo |
| **Double bottom (Hai đáy — W)** | Có đợt tăng trước đủ điều kiện | Tương tự các điều chỉnh trung gian, không quá cực đoan (thường ≤ ~33%) — **UNVERIFIED con số chính xác** | ≥ ~**7 tuần** | Hình W; **đáy thứ hai thấp hơn (undercut) đáy thứ nhất** (rũ bỏ stop) nhưng không sụp | Breakout trên trung bình | **Đỉnh giữa của chữ W** + 10 cent | Đáy hai không undercut; W lệch; đỉnh giữa quá thấp | [TRANG GỐC] IBD qua Yahoo; LuxAlgo |
| **High tight flag (Cờ cao hẹp) / Power Play (Minervini)** | IBD: tăng **100–120% trong 4–8 tuần**. Minervini: tăng **≥ 100% trong ≤ 8 tuần** trên khối lượng lớn, thường có chất xúc tác mạnh | IBD: điều chỉnh **10–25%**. Minervini: **≤ 20%** (cổ phiếu giá thấp có thể tới **25%**) | IBD: **3–5 tuần**. Minervini: **3–6 tuần** (một số chỉ 10–12 ngày) | Đi ngang chặt; Minervini yêu cầu thể hiện đặc tính VCP | Khối lượng cạn trong cờ | Đỉnh của cờ + 10 cent (IBD); pivot của vùng chặt (Minervini) | Cờ sâu > 25%; cờ kéo dài; nới lỏng | [SÁCH] TLSMW/TTLC qua Elearnmarkets & Stockopedia; [TRANG GỐC] IBD qua Yahoo |
| **Base-on-base (Nền chồng nền)** | Cổ phiếu breakout nhưng **không tăng được 20%** trước khi tạo nền mới | Theo loại nền thành phần | Theo loại nền thành phần | Nền thứ hai nằm **cao hơn** nền đầu (như hai bậc thang); nền thứ hai thường là flat base. **Tính là một giai đoạn** khi đếm nền | Như trên | Theo quy tắc của nền thứ hai | Nếu cổ phiếu đã tăng > 20% thì hai nền là **riêng biệt**, không phải base-on-base | [TRANG GỐC] IBD qua Nasdaq |
| **Ascending base (Nền đi lên)** | Giữa một đợt tăng của cổ phiếu dẫn dắt | **Ba nhịp giảm**, mỗi nhịp **10–20%** | **9–16 tuần** | Mỗi nhịp tạo đỉnh cao hơn và đáy cao hơn (bậc thang) | Breakout trên trung bình rõ rệt | Đỉnh của nhịp giảm thứ ba + 10 cent | Mua khi đã vượt > 5% | [TRANG GỐC] IBD qua Yahoo; LuxAlgo |
| **Square box (Hộp vuông)** | Sau khi tăng từ cup with handle hoặc double bottom; thường là nền giai đoạn muộn (third-stage) | **10–15%** (không quá 15%) | **4–7 tuần** | Đi ngang, không nghiêng; khối lượng yên ắng | Breakout trên trung bình | Đỉnh cao nhất trong vùng + 10 cent | Dễ nhầm với flat base (flat base ≥ 5 tuần) | [TRANG GỐC] IBD qua Yahoo (bài "Square-Box Structure Forms Fast") — O'Neil đưa vào HTMMS bản 4 (2009) |
| **Primary base / IPO base** (Minervini) | Cổ phiếu mới niêm yết | Điều chỉnh không nên vượt **2–3 lần** mức giảm của thị trường chung | Cổ phiếu mới phải chứng tỏ sức mạnh "ít nhất vài tháng" | — | — | Pivot của nền đầu tiên | — | [SÁCH] TLSMW qua Elearnmarkets |

**Chú thích "+10 cent"**: IBD dùng USD. Ở Việt Nam thay bằng **+1 bước giá** (xem mục 6) [TRIỂN KHAI].

### 3.2b Hành vi giá "chặt" (tight action) và các điểm vào bổ sung

| Khái niệm | Quy tắc | Code | Nguồn |
|---|---|---|---|
| **3-weeks-tight (Ba tuần chặt)** | Ba giá đóng cửa **tuần** liên tiếp gần như bằng nhau, mỗi tuần đóng trong khoảng **~1% (có khi tới 1,5%)** so với tuần trước; biên độ hẹp, khối lượng yên ắng; thường xuất hiện sau breakout hoặc khi kéo về MA10 tuần. Buy point = **mức cao nhất trong biên độ trong tuần (intra-week high) của 3 tuần** + 10 cent (VN: + 1 bước giá) — **không** dùng giá đóng cửa cao nhất; nên mua bổ sung với tỷ trọng nhỏ | `wc = weekly_close`; `abs(wc[-1]/wc[-2]-1) <= tol and abs(wc[-2]/wc[-3]-1) <= tol`, `tol` mặc định 0,01 (tối đa 0,015); `buy_point = max(weekly_high[-3:]) + tick` | [TRANG GỐC] IBD qua Yahoo ("Brief Pause Before A Breakout") và Nasdaq |
| **Tight closes / độ chặt pivot (Minervini)** | Vùng pivot có các phiên đóng cửa sát nhau, biên độ phiên thu hẹp, khối lượng cạn — thành phần định tính của VCP và 3C | [TRIỂN KHAI] `std(close[-5:])/mean(close[-5:]) <= x` hoặc `(max(close[-5:])-min(close[-5:]))/max(close[-5:]) <= y`; x, y trong config | Khái niệm [SÁCH]; ngưỡng **UNVERIFIED** |
| **Shakeout / undercut & rally (Minervini)** | Giá thủng nhanh một đáy rõ ràng trong nền (rũ bỏ stop) rồi lấy lại trên đáy đó; điểm vào khi lấy lại mức bị thủng | [TRIỂN KHAI] `low[t] < prior_swing_low and close[t or t+k] > prior_swing_low` | Khái niệm có trong TTLC — **UNVERIFIED chi tiết** |
| **Shakeout +3 (O'Neil)** | Nền có nhịp rũ bỏ xuống dưới đáy trước; buy point đặt **trên đáy rũ bỏ một khoảng cố định ("+3" — theo đơn vị giá USD)** | Không chuyển được trực tiếp sang VND; nếu dùng phải quy đổi theo % | **UNVERIFIED** — không mở được nguồn O'Neil trong phiên này |
| **Pocket pivot (Morales & Kacher)** | Phiên tăng có khối lượng **lớn hơn khối lượng lớn nhất của mọi phiên giảm trong 10 phiên trước**, xảy ra trong nền mang tính xây dựng hoặc gần MA10/MA50, không bị kéo giãn (extended) | `up_day and vol[t] > max(vol[t-10:t][down_days])` | [THỨ CẤP] — *Trade Like an O'Neil Disciple* (2010); S&C V.30:13; LuxAlgo. Không phải quy tắc Minervini |

### 3.3 Tiêu chí cơ bản (Fundamentals) mà SEPA tìm

| Tiêu chí | Quy tắc | Nguồn/độ tin cậy |
|---|---|---|
| Tăng trưởng EPS | Các siêu cổ phiếu thường có EPS tăng **30–40% hoặc hơn**; lãnh đạo mới có thể có doanh thu tăng ba chữ số | [SÁCH] TLSMW qua Elearnmarkets |
| Ngưỡng tối thiểu | Thường cần EPS tăng **≥ 20–25%** so với cùng kỳ trong 1–3 quý gần nhất | **[THỨ CẤP] — UNVERIFIED** là con số trực tiếp của Minervini |
| Tăng tốc (acceleration) | Tốc độ tăng EPS và doanh thu tăng dần theo quý (ví dụ 10% → 30% → 50%) | [SÁCH] (khái niệm) + [THỨ CẤP] (ví dụ) |
| **Code 33** | **Ba quý liên tiếp tăng tốc** đồng thời ở **EPS, doanh thu và biên lợi nhuận** | [SÁCH] TLSMW — trích qua Deepvue (X) |
| Biên lợi nhuận | Biên lợi nhuận mở rộng (broadening margins) | [SÁCH] |
| Bất ngờ lợi nhuận | Earnings surprise dương; các tổ chức ưa thích | [SÁCH] |
| Chất lượng tăng trưởng | Tăng EPS nên đi kèm tăng doanh thu, không chỉ nhờ cắt giảm chi phí | [THỨ CẤP] |
| So sánh O'Neil (CAN SLIM) | C: EPS quý gần nhất tăng ≥ 25% so với cùng kỳ; A: EPS năm tăng ≥ 25% mỗi năm trong 3 năm | [SÁCH] HTMMS — xác nhận qua Wikipedia (CAN SLIM) và màn lọc AAII (phản biện vòng 1) |

**Công thức đo [TRIỂN KHAI]**: `eps_yoy_q = EPS_q / EPS_{q-4} - 1` (chỉ khi `EPS_{q-4} > 0`; nếu EPS cùng kỳ âm → đánh dấu "không xác định", không tính %). Acceleration: `eps_yoy_q > eps_yoy_{q-1} > eps_yoy_{q-2}`. Biên: `net_margin_q - net_margin_{q-4}`. Hồ sơ ngành (sector profile) — ví dụ công ty chứng khoán — cần chỉ số riêng (xem CLAUDE.md).

### 3.4 Quy tắc thoát lệnh và quản trị rủi ro của SEPA

| Quy tắc | Giá trị | Nguồn |
|---|---|---|
| Cắt lỗ tối đa | **10%** ("uncle point") — không bao giờ vượt | [SÁCH] TTLC |
| Mức cắt lỗ thông thường | **7–8%**; thị trường khó khăn thắt lại **5–6%** | [SÁCH] TLSMW |
| Lỗ trung bình | **≤ 5–6%** | [SÁCH] TTLC |
| Rủi ro mỗi lệnh | **1,25–2,5% vốn** | [SÁCH] TTLC |
| Chốt lời trung bình | Thông thường **15–20%**; thị trường xấu **10–12%** | [SÁCH] TLSMW |
| Dời stop về hòa vốn | **Một quy tắc với HAI điều kiện đồng thời (AND)**: khi cổ phiếu tăng **≥ 3 lần rủi ro ban đầu VÀ lãi vượt mức lãi trung bình** của người giao dịch → dời stop về hòa vốn. Code: `if gain >= 3*initial_risk and gain > avg_gain: stop = max(stop, entry)`. Biến thể "tăng 2 lần lãi trung bình → khóa lãi bằng lãi trung bình" **không tìm thấy trong nguồn đã kiểm — UNVERIFIED**, không cài mặc định | [SÁCH] TTLC qua Elearnmarkets |
| Tập trung danh mục | **Tổng 10–12 mã; tài khoản nhỏ 4–8 mã** (số lượng phụ thuộc quy mô tài khoản, không phải "tối ưu/tối đa"); không vị thế > 50%; vị thế tốt nhất 20–25% | [SÁCH] TTLC qua Elearnmarkets |
| **Tỷ lệ thắng/thua điều chỉnh (adjusted win/loss ratio)** | **Dạng tỷ số**: `ratio = (PWT × AG) / (PLT × AL)`; **`ratio > 1` = kỳ vọng dương**. PWT/PLT: tỷ lệ lệnh thắng/thua; AG/AL: lãi/lỗ trung bình (%). **Không dùng** dạng hiệu `(PWT×AG) − (PLT×AL)` (kỳ vọng kiểu Van Tharp, ngưỡng 0) cho quy tắc của Minervini — khác đơn vị và ngưỡng | [SÁCH] TTLC qua Elearnmarkets (mục "Expectancy Formula") |
| **Bán khi mạnh (sell into strength)** | Khi giá tăng tốc với tốc độ/độ dốc lớn hơn bất kỳ lúc nào trước đó (climax). Định nghĩa đo được: xem "tín hiệu đỉnh climax" bên dưới | [SÁCH] TTLC (định tính) |
| **Bán khi yếu** | Phiên/tuần giảm **lớn nhất** kể từ khi bắt đầu Stage 2; không giữ được MA20 sau breakout; thủng stop | [SÁCH] |
| O'Neil — cắt lỗ | 7–8% dưới buy point | [THỨ CẤP] LuxAlgo trích HTMMS |
| O'Neil — **chốt lời 20–25%** | Chốt phần lớn vị thế khi lãi **20–25%** từ buy point | [TRANG GỐC] IBD (bài square box khuyến nghị 20–25%) |
| O'Neil — **quy tắc giữ 8 tuần** | Nếu cổ phiếu tăng **≥ 20% trong vòng ≤ 3 tuần** kể từ breakout → **giữ ít nhất 8 tuần** tính từ ngày breakout (ghi đè quy tắc chốt 20–25%). Ngoại lệ trong bài: nếu thị trường chung điều chỉnh mạnh trong tuần 4–5 thì không bắt buộc giữ máy móc; stop vẫn có hiệu lực | [TRANG GỐC] IBD qua Yahoo ("Know When To Invoke The 8-Week Hold") — đã đọc trực tiếp |

**Tín hiệu đỉnh climax (O'Neil) — định nghĩa đo được** [TRANG GỐC IBD + THỨ CẤP — IBD qua Yahoo "Vertical Logic…", MarketSmith India; ngưỡng code là TRIỂN KHAI]:
- Sau đợt tăng nhiều tháng, **tăng nhanh 25–50% hoặc hơn** trong **1–3 tuần** (cách diễn đạt trong sách O'Neil, qua tóm tắt) hoặc **2–3 tuần** (bài IBD "Vertical Logic…") — tham số `climax_window_weeks`, lưu cả hai: `close/close.shift(5..15) - 1 >= 0.25` và cổ phiếu đã ở Stage 2 ≥ N tháng.
- **Giá cao hơn MA200 (40 tuần) từ 70–100% trở lên**: `close / SMA200 - 1 >= 0.70` (cảnh báo), `>= 1.00` (mạnh) [TRANG GỐC — IBD "Vertical Logic…"].
- **7 trên 8 phiên tăng** (thường kết thúc bằng exhaustion gap): `sum(close.diff()[-8:] > 0) >= 7` [TRANG GỐC — IBD "Vertical Logic…"].
- **Phiên tăng giá lớn nhất** (theo điểm/%) kể từ đầu đợt tăng: `ret[t] == max(ret[stage2_start:t])`.
- **Khoảng trống kiệt sức (exhaustion gap)**: mở cửa gap lên trên đỉnh phiên trước với khối lượng lớn sau đợt tăng dài; nhiều gap liên tiếp = chặng cuối.
- **Khối lượng cực lớn** ở đỉnh.
- **Đóng cửa dưới MA50 với khối lượng lớn** (`close < SMA50 and volume > 1.4*vol_sma50`) — tín hiệu bán khi yếu thường được IBD nêu [THỨ CẤP].

### 3.5 Cổng trạng thái thị trường chung (market-direction gate)

Cả Minervini và O'Neil nhấn mạnh phần lớn breakout thất bại khi thị trường chung đang điều chỉnh (chữ "M" của CAN SLIM). Screener **phải** có đầu vào trạng thái thị trường; khi thị trường ở trạng thái "correction", kết quả sàng lọc chỉ là danh sách theo dõi, không phải ứng viên.

| Khái niệm | Định nghĩa | Nguồn |
|---|---|---|
| **Distribution day** | Chỉ số (Nasdaq/S&P 500) **giảm ≥ 0,2%** với **khối lượng cao hơn phiên trước**. Mức nghiêm trọng: giảm ≥ 1% với khối lượng trên trung bình = phân phối rõ ràng | [TRANG GỐC] IBD qua Yahoo ("Watch For Distribution Days") |
| Hết hiệu lực | Sau **25 phiên**; hoặc sớm hơn khi chỉ số tăng đủ xa so với **giá đóng cửa** ngày phân phối (hai bài IBD qua Yahoo xác nhận cơ sở "giá đóng cửa" nhưng không nêu con số). **Mặc định: 5% tính theo giá đóng cửa**; tùy chọn config: **6% tính theo intraday** (mô tả quy cho IBD). Vẫn còn mở (D6) | [THỨ CẤP] aistockselection; optionstradingiq — **TRANH CÃI (D6)** |
| Ngưỡng cảnh báo | Bài IBD: "thường cần **5 hoặc 6 ngày phân phối rõ ràng trong vài tuần** trước khi chúng gây vấn đề cho đợt tăng" [TRANG GỐC]. Việc **ánh xạ** con số này sang trạng thái "uptrend under pressure" → "market in correction" là **suy luận của người nghiên cứu** [TRIỂN KHAI]/[THỨ CẤP], không phải quy tắc nêu trên trang IBD. Code đề xuất: `count_dd(25 phiên) >= 5` → pressure; ngưỡng chuyển "correction" là tham số | Câu trích: IBD qua Yahoo; ánh xạ: [TRIỂN KHAI] |
| **Stalling day (ngày chững lại)** | Chỉ số **tăng nhưng đóng cửa gần đáy phiên với mức tăng rất nhỏ**, khối lượng lớn — phân phối ngầm. Một định nghĩa lượng hóa phổ biến: tăng **≤ 0,4%**, khối lượng **≥ 95%** phiên trước, đóng cửa ở **nửa dưới** biên độ phiên | Định tính [TRANG GỐC] IBD qua Yahoo; ngưỡng số [THỨ CẤP] (script TradingView "IBD Market School") — **UNVERIFIED là quy tắc IBD** |
| **Rally attempt** | Ngày 1 = phiên đầu tiên chỉ số đóng cửa cao hơn sau khi lập đáy mới trong đợt điều chỉnh. Nếu ngày 2–3 chỉ số **thủng đáy vừa lập** → nỗ lực thất bại, đếm lại | [TRANG GỐC] IBD qua Yahoo ("Follow-Through Day Tells You That It's Go Time") |
| **Follow-through day (FTD)** | Từ **ngày thứ 4 trở đi** của rally attempt (tốt nhất ngày 4–7), chỉ số tăng **mạnh** với **khối lượng cao hơn phiên trước** (không cần trên trung bình). Ngưỡng % **thay đổi theo thời gian**: ban đầu **1%** → **2%** (HTMMS 2002) → **1,7%** (khoảng 2008) → hiện hành "**ít nhất 1% đến 1,25%**, là mục tiêu di động". Tham số hóa `ftd_min_gain`, **mặc định 1,25%** | [TRANG GỐC] IBD qua Yahoo; lịch sử ngưỡng: Quantifiable Edges, aistockselection (theo phản biện vòng 2) |
| Thất bại FTD | Chỉ số thủng đáy của rally attempt sau FTD → FTD thất bại | [THỨ CẤP] |

Áp dụng cho VN-Index/VN30: định nghĩa có thể chuyển thẳng, nhưng các ngưỡng (0,2%, 1–1,7%, 5 ngày) **chưa được kiểm định cho VN** — tham số hóa (mục 6).

---

