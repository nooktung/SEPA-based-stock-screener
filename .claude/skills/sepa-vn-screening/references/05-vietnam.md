# Điều chỉnh cho thị trường Việt Nam (ADAPTATION)

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

## 6. Ghi chú điều chỉnh cho thị trường Việt Nam — **ADAPTATION, KHÔNG PHẢI PHƯƠNG PHÁP GỐC**

> Toàn bộ mục này là đề xuất điều chỉnh của người nghiên cứu. Minervini/O'Neil/Bulkowski xây dựng và kiểm định trên thị trường Mỹ, không có biên độ giá, thanh toán T+1/T+2 với khả năng bán ngay, cho phép bán khống. Mọi con số dưới đây phải là tham số trong `config/thresholds.yaml`.

### 6.1 Cấu trúc thị trường (dữ kiện)

| Dữ kiện | Giá trị | Nguồn |
|---|---|---|
| Biên độ dao động giá ngày | **HOSE ±7%**, **HNX ±10%**, **UPCoM ±15%** so với giá tham chiếu | SSI (quy định HSX/HNX/UPCoM), VinaCapital (2022), TCBS Help |
| Ngày giao dịch đầu tiên / sau tạm ngừng | HOSE **±20%**; HNX **±30%**; UPCoM **±40%** (ngày đầu hoặc sau tạm ngừng ≥ 25 ngày) | SSI (cả ba trang quy định) |
| **Giá tham chiếu** | HOSE/HNX: **giá đóng cửa phiên trước** (xác định bởi phiên khớp lệnh định kỳ đóng cửa ATC). **UPCoM: bình quân gia quyền (VWAP) các giá khớp lô chẵn** trong phiên khớp lệnh liên tục của ngày giao dịch trước — **không phải giá đóng cửa** | SSI (UPCoM, HNX) |
| Giá trần/sàn | **Lấy trực tiếp từ dữ liệu sàn** (DNSE `security_definition`: giá trần/sàn/tham chiếu) khi feed có cung cấp; **không tự tính**. Chỉ khi feed thiếu mới dùng fallback `ref × (1 ± band)` làm tròn theo bước giá (các nguồn thứ cấp nói trần làm tròn xuống, sàn làm tròn lên, nhưng **ví dụ minh họa của chúng mâu thuẫn nhau**, và chưa tìm thấy văn bản quy chế của sàn) — fallback phải gắn cờ | [THỨ CẤP] hodl.vn; U22 còn mở |
| **Giờ giao dịch HOSE** | ATO 09:00–09:15; liên tục 09:15–11:30 và 13:00–14:30; **ATC 14:30–14:45**; thỏa thuận 09:00–15:00 | SSI (HSX, sau chuyển đổi hệ thống KRX) |
| **Giờ giao dịch HNX** | Liên tục 09:00–11:30 và 13:00–14:30; **ATC 14:30–14:45**; sau giờ (PLO) 14:45–15:00; thanh toán "trước 13h00 ngày T+2" | SSI (HNX) |
| **Giờ giao dịch UPCoM** | Liên tục 09:00–11:30 và 13:00–15:00; **không có ATC**; chỉ lệnh LO | SSI (UPCoM) |
| **Lô giao dịch** | Lô chẵn **100 cổ phiếu** trên cả ba sàn; lô lẻ 1–99 được phép (UPCoM: không có lô lẻ ngày đầu/ngày giao dịch lại cho đến khi có giá tham chiếu) | SSI |
| Thanh toán | **T+2**; từ 29/08/2022 cổ phiếu mua ngày T về tài khoản **trong phiên sáng/đầu giờ chiều T+2** và bán được **phiên chiều T+2**. Khung giờ theo nguồn: HSC (2022) 11:30–12:30; SSI/HNX "trước 13h00 ngày T+2"; UBCKNN (theo phản biện) 11:30–12:00 → dùng tham số `settle_available_time`, mặc định 13:00 | HSC, VSD, VietnamNet (2022), SSI |
| **Rủi ro chậm thanh toán** | Thanh toán T+2 là **thông lệ, không phải cam kết**: ngày **27/02/2026**, sự cố kết nối VSDC khiến cổ phiếu mua ngày 25/02 chưa về tài khoản đúng hạn; dịch vụ chỉ khôi phục **sau 14:00** ngày 27/02 | Vietstock (02/2026) |
| Bước giá HOSE | < 10.000đ: 10đ; 10.000–49.950đ: 50đ; ≥ 50.000đ: 100đ (từ 12/9/2016; **vẫn áp dụng sau chuyển đổi KRX** theo trang SSI) | CafeF (2016), SSI (HSX) |
| Bước giá HNX/UPCoM | **100đ** cho mọi mức giá | SSI (trang quy định HNX và UPCoM — xác nhận ở phản biện vòng 2); hodl.vn |
| **Không prefunding (Thông tư 68/2024/TT-BTC, hiệu lực 02/11/2024)** | Chỉ áp dụng cho **nhà đầu tư nước ngoài là tổ chức** (được mua không cần đủ tiền khi đặt lệnh). **Không** thay đổi việc nhà đầu tư cá nhân chỉ bán được cổ phiếu từ chiều T+2 | Thời báo Tài chính Việt Nam (theo phản biện) |
| **T+0 / bán khống** | Tính đến 2026: **chưa triển khai** giao dịch trong ngày (T+0) và bán khống có bảo đảm cho cổ phiếu cơ sở; lộ trình dự kiến 2026–2028 | UBCKNN qua kinhtechungkhoan.vn; movetoasia (theo phản biện) |

### 6.2 Quy tắc cần điều chỉnh

1. **Giá phải điều chỉnh quyền trước mọi phép tính** (MA, 52 tuần, RS, độ sâu nền). Chia cổ tức cổ phiếu/thưởng/phát hành quyền làm giá "sụt" giả. Theo CLAUDE.md của dự án: cổ tức cổ phiếu nhỏ (ví dụ 20:1, −4,8%) **nằm trong biên ±7%** và không phát hiện được bằng gap → bắt buộc dùng giá tham chiếu `basicPrice`. Nếu `verify_continuity()` trả kết quả không rỗng → chuỗi bị đánh dấu stale; **không chạy Trend Template trên chuỗi stale**.

2. **Cắt lỗ và T+2**: Minervini giả định có thể thoát ngay khi chạm stop. Ở VN, cổ phiếu mua ngày T chỉ bán được từ **chiều T+2**. Trước lần bán đầu tiên có thể có **ba lần giảm sàn** chồng lên vị thế: (i) phần còn lại của ngày T (T có thể đóng cửa ở giá sàn), (ii) ngày T+1, (iii) phiên sáng T+2 cho đến khi bán (sàn T+2 = 0,93 × giá đóng cửa T+1). Vì giá tham chiếu mỗi ngày là giá đóng cửa hôm trước (HOSE/HNX), mức sàn **cộng dồn theo cấp số nhân**:

   | Sàn | Mua tại giá tham chiếu ngày T: `1 − (1−b)³` | Mua tại giá trần ngày T (thường gặp khi mua breakout): `1 − (1−b)³/(1+b)` |
   |---|---|---|
   | HOSE (b = 7%) | **≈ 19,6%** | **≈ 24,8%** |
   | HNX (b = 10%) | **≈ 27,1%** | **≈ 33,7%** |
   | UPCoM (b = 15%) | **≈ 38,6%** | **≈ 46,6%** |

   Lưu ý bắt buộc:
   - Đây là **mức lỗ khi bán được sớm nhất trong kịch bản giảm sàn liên tục, KHÔNG phải cận trên cứng**. Nếu cổ phiếu **"trắng bên mua" ở giá sàn** thì không bán được và lỗ tiếp tục tăng sau T+2. Sự cố thanh toán (ví dụ VSDC 27/02/2026) có thể đẩy thời điểm bán sớm nhất **muộn hơn chiều T+2**.
   - **UPCoM**: giá tham chiếu là **VWAP phiên trước**, không phải giá đóng cửa → phép cộng dồn trên chỉ là **xấp xỉ**.
   - Làm tròn giá sàn lên theo bước giá khiến lỗ thực tế thấp hơn một chút; bỏ qua ở mức ước lượng này.
   - Mọi con số đều **vượt xa mức tối đa 10% của Minervini**. Đề xuất [TRIỂN KHAI]: báo cáo cho mỗi ứng viên cả `risk_to_stop` (theo khoảng cách stop) **và** `locked_worst_case` (theo bảng trên, tùy sàn và giá mua), định cỡ vị thế theo con số lớn hơn khi người dùng chọn chế độ thận trọng. Hệ thống **không** đặt lệnh — đây chỉ là thông tin rủi ro cho người dùng quyết định.

3. **"+10 cent" → "+1 bước giá"**: buy point = đỉnh vùng + 1 tick theo bảng bước giá của sàn tương ứng tại mức giá đó.

4. **Khối lượng breakout khi tăng trần**: phiên đóng cửa **trần với dư mua** thường có khối lượng khớp **thấp giả tạo** (thiếu người bán) — quy tắc "≥ 40–50% trên trung bình" có thể loại sai một breakout mạnh. Đề xuất: nếu `close == ceiling_price`, đánh giá thêm dư mua trần (nếu dữ liệu có) và không coi volume thấp là bằng chứng phủ định; gắn cờ "volume không xác định được". **Lưu ý cơ chế giá đóng cửa**: trên HOSE/HNX giá đóng cửa do **phiên ATC (14:30–14:45)** quyết định, nên một mã có thể giao dịch ở giá trần cả phiên liên tục nhưng đóng cửa dưới trần (và ngược lại). Quy tắc `close == ceiling` phải dùng giá đóng cửa chính thức sau ATC, không phải giá khớp cuối phiên liên tục. UPCoM **không có ATC**, và "giá tham chiếu" hôm sau là VWAP chứ không phải giá đóng cửa → kiểm tra liên tục giá đã điều chỉnh (`verify_continuity`) **không được giả định ref == prev_close** trên UPCoM.

5. **Nến và biên độ**: phiên trần/sàn tạo nến gần marubozu (`close == high` do bị chặn), không phải do cân bằng cung cầu tự do. Mẫu hình phụ thuộc gap/biên độ (abandoned baby, kicker, star) có ngữ nghĩa khác khi một nến bị chặn biên. Đề xuất: gắn cờ `limit_hit` cho nến chạm trần/sàn và **loại** chúng khỏi nhận dạng mẫu hình, hoặc báo cáo riêng. Tỷ lệ % của Bulkowski **không áp dụng trực tiếp** cho VN — chỉ là tham chiếu.

6. **Vũ trụ RS Rating**: xếp hạng phần trăm phụ thuộc vũ trụ. Đề xuất: (a) vũ trụ = tất cả mã HOSE+HNX+UPCoM **đã qua bộ lọc thanh khoản**; (b) lưu cả `rs_rating_all` và `rs_rating_liquid`. Benchmark cho biến thể A: VN-Index (hoặc VNAllShare). Mã có < 252 (hoặc ~250) phiên dữ liệu: **loại**, không nội suy.

7. **Bộ lọc thanh khoản** (không có trong phương pháp gốc, nhưng bắt buộc ở VN): ví dụ `SMA(giá trị giao dịch, 20) >= X tỷ VND` và `số phiên có giao dịch trong 60 phiên >= Y%`. X, Y là tham số — **không có chuẩn nguồn; UNVERIFIED**. Mã UPCoM thanh khoản thấp dễ tạo "VCP" giả do không có giao dịch (biên độ hẹp vì không ai giao dịch, không phải vì cung cạn).

8. **Số phiên/năm**: 52 tuần ≈ 245–250 phiên ở VN (không phải 252) — tham số hóa; 1 tháng ≈ 20–22 phiên.

9. **Biến động nền**: VN-Index có biến động và cơ cấu ngành khác S&P 500 (ngân hàng, bất động sản, chứng khoán chiếm tỷ trọng lớn). Độ sâu nền "bình thường" (12–33% cho cup; ≤ 15% cho flat base) có thể cần nới — **phải kiểm định lại bằng backtest (Phase 13)**, không tự ý đổi.

10. **Bearish patterns**: vì nhà đầu tư cá nhân **không thể bán khống cổ phiếu cơ sở** (dữ kiện, mục 6.1), mẫu hình giảm chỉ dùng để **thoát/tránh**, không để mở vị thế.

11. **Lô 100 cổ phiếu**: kích thước vị thế đề xuất phải làm tròn xuống bội số 100 (lô lẻ có thanh khoản kém hơn); với cổ phiếu giá cao và tài khoản nhỏ, làm tròn có thể làm lệch đáng kể % rủi ro mỗi lệnh (1,25–2,5%) — báo cáo % rủi ro **sau** làm tròn.

12. **Trạng thái thị trường VN**: áp dụng cổng mục 3.5 cho VN-Index (và VN30/HNX-Index như tham chiếu phụ). Khối lượng chỉ số dùng khối lượng khớp lệnh (không gồm thỏa thuận) — [TRIỂN KHAI], cần xác nhận nguồn dữ liệu DNSE có tách khối lượng thỏa thuận hay không.

13. **Hồ sơ ngành**: tiêu chí cơ bản của Minervini (EPS/doanh thu/biên) áp dụng kém cho ngân hàng và công ty chứng khoán (lãi tự doanh làm EPS dao động lớn) — dùng sector profile như CLAUDE.md mô tả.

14. **Ngày phân phối cho VN-Index**: có thể áp dụng định nghĩa IBD (−0,2% với volume cao hơn phiên trước) nhưng ngưỡng 0,2% chưa được kiểm định cho VN — tham số hóa.

---

