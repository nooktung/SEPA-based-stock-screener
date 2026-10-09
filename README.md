# SEPA-based stock screener — cổ phiếu Việt Nam

Công cụ **nghiên cứu** cổ phiếu HOSE / HNX / UPCoM theo phương pháp SEPA (Mark Minervini):
- lấy dữ liệu giá từ DNSE OpenAPI;
- lọc toàn thị trường ra danh sách theo dõi;
- theo dõi sự kiện hằng ngày;
- hiển thị trên dashboard chạy trên máy (http://127.0.0.1:8765).

**Không phải bot giao dịch:** không đặt lệnh, các lệnh mua/bán của DNSE và SSI bị chặn trong code. Kết quả là bằng chứng
để nghiên cứu, không phải khuyến nghị mua bán.

## Cần có

- Windows, [Python 3.11+](https://www.python.org/downloads/) (lúc cài nhớ tick "Add python.exe to PATH") và
  [Git](https://git-scm.com/download/win).
- Cặp khóa **DNSE OpenAPI** (API key + secret), tạo trên EntradeX / developers.dnse.com.vn.

## Cài đặt (một lần trên mỗi máy)

```powershell
git clone https://github.com/nooktung/SEPA-based-stock-screener.git D:\vn-stock-agent
cd D:\vn-stock-agent
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` làm các việc sau:
- tạo môi trường riêng `.venv` và cài thư viện;
- tạo file `.env` từ `.env.example`;
- chạy test;
- tạo database trống.

Sau đó mở `.env` và điền khóa (file này không bao giờ được đưa lên GitHub):

```
DNSE_API_KEY=...
DNSE_API_SECRET=...
```

## Chạy lần đầu

```powershell
.\.venv\Scripts\Activate.ps1          # mỗi lần mở terminal mới
python -m src.main universe           # danh sách ~1.660 mã HOSE/HNX/UPCoM
python -m src.main start              # tải dữ liệu, lọc, rồi mở dashboard
```

Lần đầu `start` cần khoảng 30–60 phút vì phải tải đủ:
- 11 năm giá của mọi mã;
- phân ngành;
- báo cáo tài chính;
- tin tức.

Những lần sau chỉ tải phần còn thiếu, nên chạy nhanh.

Nếu `Activate.ps1` bị chặn, chạy thẳng: `.venv\Scripts\python -m src.main start`.

## Dùng hằng ngày

| Lệnh | Việc |
|---|---|
| `python -m src.main start` | Cập nhật phiên mới nhất rồi mở dashboard (bỏ qua bước đã làm) |
| `python -m src.main start --no-update` | Mở dashboard ngay, không cập nhật |
| `python -m src.main start --plan` | Xem trước bước nào sẽ chạy |
| `python -m src.main doctor` | Kiểm tra cấu hình và nguồn dữ liệu |
| `python -m pytest -q` | Chạy toàn bộ test |

**Tự cập nhật lúc 15:30 thứ Hai–Sáu** (tùy chọn): chạy một lần

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
```

Nếu máy đang tắt hoặc ngủ lúc 15:30, tác vụ sẽ tự chạy bù khi máy bật lại. Log nằm trong `data\logs\`.

## Cập nhật code mới từ GitHub

```powershell
git pull
.venv\Scripts\python -m pip install -r requirements.txt
python -m src.main start
```

## Chuyển sang máy khác mà giữ lịch sử

Database `data\agent.db` (~400 MB) **không có trên GitHub**. Nó chứa:
- các lượt lọc;
- danh mục "Của tôi";
- trạng thái theo dõi;
- tin tức đã thu.

Muốn giữ những thứ này thì chép tay file đó (USB, OneDrive) vào `data\` của máy mới, sau khi chạy `setup.ps1` và trước
khi `start`.

## Tài liệu

- [run.md](run.md): mọi lệnh, giải thích từng bước.
- [congthuc.md](congthuc.md): mọi công thức và ngưỡng hệ thống dùng, có nguồn.
- [C.md](C.md): hướng dẫn đọc dashboard.
- [CLAUDE.md](CLAUDE.md): ghi chú kỹ thuật cho người phát triển.
- [docs/](docs/): ghi chú về DNSE, SSI, kiểm định mẫu hình.
- Ngưỡng và nguồn dữ liệu chỉnh trong `config/thresholds.yaml` và `config/sources.yaml`, không cần sửa code.
