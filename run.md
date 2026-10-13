# Hướng dẫn tự chạy (không cần Claude)

Mở thư mục `D:\vn-stock-agent` trong VS Code → **Terminal → New Terminal**, rồi gõ các lệnh dưới đây.
Công cụ nghiên cứu — không có chức năng đặt lệnh, kết quả lọc không phải khuyến nghị.

---

## 1. Một lệnh cho tất cả

```bash
python -m src.main start          # cập nhật mọi thứ rồi mở web http://127.0.0.1:8765 (Ctrl+C để dừng)
```

`start` chạy lần lượt mục 1 và mục 2 cũ, xong hết mới mở web:

| Bước | Thời gian | Bỏ qua khi |
|---|---|---|
| Tải giá toàn thị trường | ~30 phút | phiên mới nhất đã có giá (VD tác vụ 15:30 đã chạy) |
| KL khớp lệnh & độ rộng VN-Index, lịch nghỉ lễ | vài giây | phiên mới nhất đã có và lịch đã đọc hôm nay |
| Đối chiếu VN-Index với HOSE (báo cáo `data/audit/vnindex_check_<ngày>.json`) | vài giây | đã có báo cáo phiên đó |
| Phân ngành, BCTC mã mới, **lọc lại toàn thị trường** | vài phút | đã lọc sau khi phiên mới nhất đóng cửa |
| Theo dõi danh sách (sự kiện, trạng thái) | ~2 phút | không lọc lại và đã có trạng thái phiên đó |
| Giá tham chiếu / trần / sàn (mã theo dõi + mọi mã UPCoM) | ~2–6 phút | đã đọc trong 3 giờ qua và danh sách không đổi |
| Khối ngoại, logo mã mới | vài giây – 2 phút | (chỉ lấy phần còn thiếu) |
| Tin tức | ~8 phút | đã thu trong 4 giờ qua |
| Bảng "Toàn thị trường" | ~15 giây | đã có giá mới nhất |

- Lần đầu trong ngày có phiên mới: tối đa ~1 giờ (nếu chưa ai tải giá); chạy lại cùng ngày: vài giây là mở web.
- "Phiên mới nhất" là phiên đã đóng cửa: trước 15:05 là phiên hôm trước.
- Một bước lỗi không làm dừng các bước sau; cuối cùng in bảng tóm tắt bước nào xong / lỗi / bỏ qua.
- `python -m src.main start --plan` — xem trước bước nào sẽ chạy, không chạy gì.
- `python -m src.main start --no-update` — mở web ngay, không cập nhật.
- Ctrl+C trong lúc cập nhật → dừng; chạy lại `start` sẽ tiếp tục (bước đã xong được bỏ qua).
- Web đang mở sẵn ở cửa sổ khác → `start` vẫn cập nhật rồi nhắc bạn tải lại trang.
- `start` chiếm terminal → mở thêm terminal (nút **+**) để chạy lệnh khác. Đổi cổng: `--port 8766`.

**Trên trang web:**
- Thanh bên trái chọn nhóm mã (Danh sách chính, Theo dõi mở rộng, Của tôi, Đầu ngành, Tất cả theo dõi) — cùng một bảng.
- Nút **Tổng quan / Kỹ thuật / Dòng tiền** đổi cột của bảng; bấm tiêu đề cột để sắp xếp.
- Bấm một mã → biểu đồ (nút **Ngày / Tuần / Tháng** đổi khung nến; **Lớp hiển thị** bật / tắt MA, hỗ trợ / kháng cự, trendline, RSI, MACD…),
  phân tích SEPA, dòng tiền 20 phiên, cơ bản, tin tức, **Kiểm tra vị thế** (nhập giá vốn).
- Tab **Học**: mẫu nến và mô hình giá có hình vẽ.
- Mục **Toàn thị trường**: mọi mã có giá (~1.470), lọc theo Xu hướng / Ngành / Sàn / Thanh khoản, bấm tiêu đề cột
  để sắp xếp; hiện 150 mã mỗi lần, nút "Hiện thêm". Đây là bảng để nghiên cứu, không phải danh sách ứng viên.

## 2. Chạy tay từng bước (nếu cần)

`start` đã làm các bước này; chạy tay khi muốn làm riêng một bước:

```bash
python -m src.main backfill           # giá toàn thị trường (~45 phút)
python -m src.main index-session      # KL khớp lệnh / thỏa thuận, số mã tăng / giảm của VN-Index + lịch giao dịch DNSE
python -m src.main index-check        # so nến VN-Index với số liệu chính thức của HOSE (chỉ báo cáo, không đổi gì)
python -m src.main backtest-swing     # kiểm chứng mẫu đánh ngắn hạn ≤ 2 tuần trên ~1.460 mã (~1 phút, không gọi API)
python -m src.main industry           # phân ngành mã mới đủ thanh khoản (mã đã có tự bỏ qua)
python -m src.main fundamentals       # BCTC quý mã mới đủ thanh khoản (mã đã có tự bỏ qua)
python -m src.main screen             # lọc lại → danh sách mới (tối đa 80 mã)
python -m src.main monitor --no-fetch # trạng thái cho danh sách mới
```

`backfill` kết thúc với "PARTIAL" là bình thường (một số mã bị gắn cờ dữ liệu / không giao dịch).

## 3. Lệnh khác

```bash
python -m src.main news-brief PNJ          # tóm tắt ≤ 7 sự kiện tin tức của một mã (file trong data/briefs/)
python -m src.main flow --days 45          # thu bù khối ngoại 45 ngày (mã đã có phiên nào thì bỏ qua phiên đó)
python -m src.main logos                   # tải logo công ty cho các mã đang theo dõi (mã đã có thì bỏ qua)
python -m src.main logos --all             # logo cho mọi mã có giá (lần đầu ~60 phút)
python -m src.main market                  # dựng lại bảng "Toàn thị trường" (~15 giây)
python -m src.main refprices               # giá tham chiếu / trần / sàn các mã theo dõi (DNSE, ~1,5 phút)
python -m src.main refprices --market      # + bù giá tham chiếu UPCoM toàn thị trường từ giá bình quân phiên trước (~5 phút)
python -m src.main refprices --all         # đọc giá DNSE công bố cho mọi mã có giá (~1.500 request; DNSE cho 1.000/giờ nên hơn 1 giờ)
python scripts/audit_chart.py --round 1    # rà soát đường chỉ báo (vòng 2: so với SSI hoặc VNDirect, vòng 3: các đường kẻ)
python -m src.main ssi --days 5            # lấy dữ liệu SSI (cần khóa SSI, xem mục 6)
python -m src.main crosscheck --days 10    # đối chiếu DNSE với SSI, báo cáo trong data/audit/
python -m src.main backfill --tickers PHP  # cập nhật giá vài mã
python -m src.main backfill --deepen       # tải bù lịch sử 11 năm cho mã đang lưu ít hơn (một lần, ~30 phút)
python -m src.main screen --dry-run        # lọc thử, không lưu
python -m src.main backtest-sepa           # kiểm định lại các quy tắc (~5 phút)
python -m src.main doctor                  # kiểm tra môi trường, nguồn dữ liệu
python -m pytest -q                        # chạy bộ kiểm thử
```

## 4. Tự chỉnh, không cần sửa code

| Muốn | Làm |
|---|---|
| Thêm / bỏ mã theo dõi | bấm ☆ trên web, hoặc tab **Của tôi** |
| Sửa sóng ngành (dầu khí, cao su, cảng…) | `config/themes.yaml` |
| Đổi ngưỡng lọc (thanh khoản, số mã, RS…) | `config/thresholds.yaml` → rồi chạy lại `screen` |
| Danh mục theo dõi cố định | `config/watchlist.yaml` |

## 5. Việc vẫn cần Claude

- Đọc sâu, diễn giải tin tức (`news-brief` chỉ gom tin).
- Đọc BCTC gốc PDF — đặc biệt ngân hàng / chứng khoán (chưa có hạng cơ bản).
- Phân tích tổng hợp một mã; sửa quy tắc hoặc thêm tính năng.

## 6. Kết nối SSI (đối chiếu dữ liệu, không đặt lệnh)

1. Có tài khoản SSI → đăng ký dịch vụ **FastConnect Data** tại quầy hoặc qua môi giới.
2. Vào iBoard → **Dịch vụ API** → tạo khóa. Chép **ConsumerID** và **ConsumerSecret** (PrivateKey không cần).
3. Mở file `.env` trong thư mục dự án, thêm hai dòng (thay bằng khóa của bạn, không dán vào chat với ai):
   ```
   SSI_CONSUMER_ID=...
   SSI_CONSUMER_SECRET=...
   ```
4. Chạy `python -m src.main doctor` — dòng `source:crosscheck` phải báo **LIVE**.
5. Chạy `python -m src.main ssi --days 5` rồi `python -m src.main crosscheck --days 5` và đọc báo cáo.

Từ đó tác vụ 15:30 tự lấy dữ liệu SSI và đối chiếu mỗi ngày. DNSE vẫn là nguồn chính; chỉ đổi sang dùng SSI
cho phần cập nhật hằng ngày sau khi bạn xem báo cáo vài tuần và đồng ý. Chi tiết: `docs/SSI.md`.

## 7. Bản web cho người khác xem (chỉ đọc, người bạn chọn)

Máy bạn vẫn tính toán như cũ; sau tác vụ 15:30, một bản **chỉ đọc** (HTML + số liệu đã tính, không có
database, không có khóa) được đẩy lên Cloudflare Pages. Chỉ email bạn cho phép mới mở được. Cài một lần:

1. Tạo tài khoản Cloudflare (miễn phí). **Workers & Pages → Create → Pages → Upload assets**, đặt tên dự án
   `sepa-vn` (trùng `site.pages_project` trong `config/thresholds.yaml`). Địa chỉ: `https://sepa-vn.pages.dev`.
2. **My Profile → API Tokens → Create Token**, quyền *Account → Cloudflare Pages → Edit*. Chép token và
   **Account ID** (trang Workers & Pages) vào `.env` (không dán vào chat với ai):
   ```
   CLOUDFLARE_API_TOKEN=...
   CLOUDFLARE_ACCOUNT_ID=...
   ```
3. **Zero Trust → Access → Applications → Add → Self-hosted**: tên miền `sepa-vn.pages.dev` và
   `*.sepa-vn.pages.dev`; policy **Allow**, *Include → Emails*: email của bạn và của người được xem
   (họ đăng nhập bằng mã gửi qua email). Gói miễn phí tới 50 người.
4. Thử: `python -m src.main export-site` rồi `powershell -ExecutionPolicy Bypass -File scripts\deploy_site.ps1 -Branch preview`
   (bản xem thử ở `preview.sepa-vn.pages.dev`), mở bằng một email được phép và một email không được phép.
5. Từ đó tác vụ 15:30 tự xuất và đẩy bản mới (~16:45). Ngày dữ liệu chưa đủ mới, bản cũ được giữ nguyên.

Trên bản web không có "Của tôi", "Kiểm tra vị thế", và biểu đồ chi tiết chỉ có cho các mã đang theo dõi.
Trước khi chia sẻ, hãy đọc điều khoản DNSE OpenAPI (và các nguồn tin) về việc cho người khác xem dữ liệu.

## Lưu ý

- **Tự chạy hằng ngày:** tác vụ Windows **"SEPA - cap nhat hang ngay"** chạy `monitor`, `flow`, `news`, `logos`,
  rồi tải giá **toàn thị trường** (`backfill`, ~30–45 phút) và dựng bảng "Toàn thị trường" (`market`).
  Cả lượt mất khoảng 1 giờ (giới hạn của tác vụ: 2 giờ)
  lúc **15:30 thứ 2–6** (script `scripts\daily.ps1`). Log: `data\logs\daily_<ngày>.log`.
  - Máy phải bật và bạn đã đăng nhập Windows. Nếu lỡ giờ (máy tắt), tác vụ tự chạy bù khi máy bật lại.
  - Ngày lễ không có phiên: tác vụ vẫn chạy nhưng không có dữ liệu mới, không sao.
  - Chạy tay ngay: `powershell -ExecutionPolicy Bypass -File scripts\daily.ps1`
  - Xem / tắt: mở **Task Scheduler** → *Task Scheduler Library* → "SEPA - cap nhat hang ngay".
- Lọc lại toàn thị trường **không** tự chạy — chỉ khi bạn gõ lệnh ở mục 2.
- Tài liệu đầy đủ về quy tắc lọc: `C.md`.
