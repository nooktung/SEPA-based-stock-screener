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
| Tải giá toàn thị trường | ~30 phút | phiên mới nhất đã có giá (VD tác vụ 15:10 đã chạy) |
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
- Thanh bên trái chọn nhóm mã (Danh sách chính, Theo dõi mở rộng, Của tôi, Đầu ngành) — cùng một bảng.
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

Từ đó tác vụ 15:10 tự lấy dữ liệu SSI và đối chiếu mỗi ngày. DNSE vẫn là nguồn chính; chỉ đổi sang dùng SSI
cho phần cập nhật hằng ngày sau khi bạn xem báo cáo vài tuần và đồng ý. Chi tiết: `docs/SSI.md`.

## 7. Bản web cho người khác xem (chỉ đọc, cần đăng nhập)

Máy bạn vẫn tính toán như cũ. Sau tác vụ 15:10, một bản **chỉ đọc** (HTML + số liệu đã tính, không có
database, không có khóa) được đẩy lên Cloudflare Pages: **https://sepa-vn.pages.dev**. Muốn xem phải đăng nhập
bằng tài khoản bạn tạo; không có tài khoản thì không thấy gì (chỉ logo công ty là ảnh công khai).

**Đã cài (13/10/2026):** dự án Pages `sepa-vn`, kho KV `MYLIST` (gắn vào dự án), `.env` có `CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_KV_MYLIST_ID`; tác vụ "SEPA - danh sach cua toi" (mỗi 30 phút, 07:00–22:30).
Token cần quyền *Cloudflare Pages: Edit* và *Workers KV Storage: Edit*.

**Tài khoản** (bạn tự gõ mật khẩu; mật khẩu không được lưu ở đâu dưới dạng chữ, chỉ lưu bản băm):

```
python -m src.main site-user add admin --role owner     # tài khoản của bạn (danh sách = danh sách trên máy này)
python -m src.main site-user add ban1                   # người xem
python -m src.main site-user list
python -m src.main site-user remove ban1
```

Ô mật khẩu trong cửa sổ dòng lệnh không hiện gì khi gõ; nếu khó gõ, dùng cửa sổ nhỏ (ô mật khẩu hiện dấu chấm, có
"Hiện mật khẩu"): `powershell -ExecutionPolicy Bypass -File scripts/site_user_gui.ps1 -Name admin -Role owner`
(người xem: `-Name ban1 -Role viewer`). Nên tắt bộ gõ tiếng Việt khi gõ mật khẩu.

Mỗi lần thêm / xóa / đổi mật khẩu (chạy lại `add` với cùng tên), mọi người phải đăng nhập lại, và thay đổi có
hiệu lực từ lần đẩy kế tiếp: `powershell -ExecutionPolicy Bypass -File scripts/deploy_site.ps1` (hoặc chờ tác vụ
15:10). Đăng nhập giữ 7 ngày; sai 8 lần từ một địa chỉ thì khóa 15 phút. Mật khẩu nên dài (≥ 12 ký tự).

**Đẩy tay:** `python -m src.main export-site` (hoặc `export-site --update`: chỉ ghi phần đổi khi thêm mã, vài giây), rồi
`deploy_site.ps1` (bản chính) hoặc `deploy_site.ps1 -Branch preview`
(bản thử ở `preview.sepa-vn.pages.dev`). Ngày dữ liệu chưa đủ mới, `export-site` từ chối và bản cũ được giữ nguyên.

**Danh sách của tôi trên web: mỗi tài khoản một danh sách riêng** (từ 14/10/2026), tối đa 40 mã
(`site.mylist_max_per_account`). Ai đăng nhập cũng bấm ☆ / ô thêm mã để sửa danh sách của chính mình, không thấy
danh sách của người khác. Danh sách của `admin` chính là "Danh sách của tôi" trên dashboard máy này (thêm trên máy
cũng ghi lên web). Máy đọc danh sách của mọi tài khoản và tính kỹ thuật, dòng tiền, tin cho mọi mã trong đó: mã thêm
trên máy có dữ liệu ngay (1–2 phút); mã thêm trên web có dữ liệu sau lần đọc kế tiếp (≤ 30 phút nếu máy đang bật)
hoặc sau tác vụ 15:10, trong lúc chờ trang ghi "Đang chờ dữ liệu".

Trên bản web không có "Kiểm tra vị thế", và biểu đồ chi tiết chỉ có cho các mã đang theo dõi.
Trước khi chia sẻ, hãy đọc điều khoản DNSE OpenAPI (và các nguồn tin) về việc cho người khác xem dữ liệu.

## Lưu ý

- **Tự chạy hằng ngày:** tác vụ Windows **"SEPA - cap nhat hang ngay"** chạy `monitor`, `flow`, `news`, `logos`,
  rồi tải giá **toàn thị trường** (`backfill`, ~30–45 phút) và dựng bảng "Toàn thị trường" (`market`).
  Cả lượt mất khoảng 1 giờ (giới hạn của tác vụ: 3 giờ; mỗi mã thêm vào danh sách của các tài khoản tốn thêm ~10 giây)
  lúc **15:10 thứ 2–6** (script `scripts\daily.ps1`). Log: `data\logs\scheduled_daily_<ngày>.log`.
  - Máy phải bật và bạn đã đăng nhập Windows. Nếu lỡ giờ (máy tắt), tác vụ tự chạy bù khi máy bật lại.
  - Ngày lễ không có phiên: tác vụ vẫn chạy nhưng không có dữ liệu mới, không sao.
  - Chạy tay ngay: `powershell -ExecutionPolicy Bypass -File scripts\daily.ps1`
  - Xem / tắt: mở **Task Scheduler** → *Task Scheduler Library* → "SEPA - cap nhat hang ngay".
- Lọc lại toàn thị trường **không** tự chạy — chỉ khi bạn gõ lệnh ở mục 2.
- Tài liệu đầy đủ về quy tắc lọc: `C.md`.


Cuối tuần (thứ Bảy/Chủ nhật theo giờ Việt Nam), `start` luôn thu tin tức mới dù vừa chạy trong 4 giờ qua. Phiên giá vẫn là phiên giao dịch đã đóng gần nhất. Tin được đọc từ các nguồn tại lúc thực thi bước `news`, độc lập với ngày phiên giá. `--no-update` chỉ mở dashboard nên không thu tin.


### Tốc độ xuất web (20/10/2026)

`site.export_workers` trong `config/thresholds.yaml` mặc định 4, giới hạn theo CPU và tối đa 4 tiến trình; đặt 1 để xuất tuần tự. Chỉ song song phần tính chart cục bộ, không tăng tốc độ gọi nguồn dữ liệu. Toàn bộ nến ngày/tuần/tháng và dòng tiền vẫn được xuất; điều kiện độ mới và quét khóa cấu hình vẫn chạy. Phép đo trên máy hiện tại: 1.472 mã, 7.430 file (~893 MB), toàn bộ xuất web 215,8 giây. Đây là thời gian xuất, không phải tổng thời gian cập nhật/tải dữ liệu.

## Lịch tự động từ 20/10/2026

- Mỗi 30 phút, 07:00–22:30 hằng ngày: đồng bộ danh sách, thu tin tức mới và `export-site --update` + deploy. Không chỉ chạy khi danh sách đổi. Tin theo thời điểm thu, độc lập phiên giá.
- 15:10 thứ Hai–thứ Sáu: chạy thẳng `start --update-only --refresh-news`, giữ đối chiếu SSI nếu có khóa, sau đó xuất bản web. Mục tiêu trước 16:00; phụ thuộc nguồn dữ liệu và máy đang bật/kết nối mạng.
- Hai tác vụ dùng `pythonw -m src.scheduled daily|news` để chạy ẩn, dùng khóa chung và tôn trọng khóa pipeline. Lượt tin nhường khi đang cập nhật. Các kiểm tra độ mới và bí mật trước xuất bản vẫn giữ nguyên.
- Đăng ký lại bằng `scripts/register_task.ps1` và `scripts/register_mylist_task.ps1`. Log: `data/logs/scheduled_daily_<ngày>.log`, `data/logs/scheduled_news_<ngày>.log`.

### Danh sách cá nhân: thêm mã có dữ liệu ngay (20/10/2026)

Web ghép danh sách riêng với dữ liệu chung của Toàn thị trường: mã chưa thuộc bảng theo dõi vẫn hiện giá, ngày phiên và mở chart đã xuất bản. Giá mới hơn được dùng kèm ngày phân tích cũ nếu có; không tạo phân tích giả khi thiếu dữ liệu. Thêm/xóa dùng ngay danh sách trả về từ thao tác lưu thành công, không chờ đọc lại KV hoặc xuất bản lại. Ghi chú lấy theo tài khoản hiện tại.

Máy chỉ tải giá và tính trạng thái cho mã mới nếu dữ liệu đó còn thiếu/cũ; khối ngoại và logo tiếp tục dùng cơ chế chỉ lấy phần thiếu. Phân tích bổ sung được công bố qua lượt đồng bộ tự động.

### Tối ưu kiểm tra BCTC (21/10/2026)

- Đã có đủ quý đang cần: dùng số liệu đã lưu cho đến quý tiếp theo; vẫn tính lại hạng tăng trưởng bằng dữ liệu trong máy.
- Nguồn trả báo cáo cũ/chưa có quý mới: chỉ kiểm tra một lần trong ngày theo giờ Việt Nam, thử lại từ ngày hôm sau. Lỗi mạng hoặc lần đọc chưa hoàn tất không được coi là đã kiểm tra thành công.
- Đủ bảy quý liền trước trong máy: chỉ đọc trang CafeF gần nhất và bổ sung số liệu, giữ toàn bộ lịch sử cũ. Lịch sử thiếu hoặc trang mới không đọc được sẽ tải thêm lịch sử như trước.
- Khi cần đọc bản điều chỉnh ngay: `python -m src.main fundamentals --tickers HPG --refresh`. Tùy chọn này bỏ qua bộ nhớ đệm và đọc lại hai trang lịch sử.
- Đo lượt chạy lại ngày 21/10: 202 mã, bỏ qua 200, tải bổ sung 1 mã, tổng 26,67 giây. Đây là phép đo lượt chạy lại trong ngày, không phải cam kết thời gian cho lượt đầu ngày hoặc toàn bộ quy trình cập nhật.
