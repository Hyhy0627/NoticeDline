# 📌 NoticeDl - Tool Đồng Bộ Lịch & Deadline UTH Moodle lên Google Calendar

Công cụ giúp sinh viên **Trường Đại học Giao thông vận tải TP.HCM (UTH)** tự động thu thập danh sách bài tập/deadline từ **Moodle (`courses.ut.edu.vn`)** và đồng bộ sang **Google Calendar** kèm thông báo nhắc nhở (trước 1 ngày, 3 giờ, 1 giờ).

---

## 📖 BẢNG TỔNG HỢP TOÀN BỘ CÁC BƯỚC THỰC HIỆN (TỪ A - Z)

---

### GIAI ĐOẠN 1: Chuẩn bị dự án trên máy cá nhân
Tải mã nguồn (hoặc Clone từ GitHub) về máy tính của bạn, sau đó mở Terminal tại thư mục dự án và cài đặt thư viện:
```bash
pip install -r requirements.txt
```

Các file chính trong dự án:
- `main.py`: File khởi chạy chính.
- `moodle_fetcher.py`: Đăng nhập & cào danh sách deadline từ UTH Moodle.
- `gcal_notifier.py`: Tương tác Google Calendar API & đặt nhắc nhở.
- `config.json`: Lưu thông tin cấu hình cá nhân.
- `encode_token.py`: Script mã hóa Token cho GitHub Secrets.
- `.github/workflows/sync.yml`: Tự động đồng bộ 24/7 trên đám mây.

---

### GIAI ĐOẠN 2: Tạo Google Cloud Credentials (`credentials.json`)

#### Bước 2.1: Truy cập Google Cloud Console
- Truy cập địa chỉ: [https://console.cloud.google.com/](https://console.cloud.google.com/) và đăng nhập tài khoản Gmail của bạn.

#### Bước 2.2: Bật Google Calendar API
1. Tại màn hình chính (Quick access), chọn **`APIs & Services`** *(hoặc gõ `Google Calendar API` trên thanh tìm kiếm đỉnh trang)*.
2. Chọn **Library** (Thư viện) ở menu cột bên trái.
3. Tìm kiếm **`Google Calendar API`** $\rightarrow$ Nhấn vào kết quả $\rightarrow$ Nhấn nút **Enable** (Bật).

#### Bước 2.3: Cấu hình Màn hình đồng ý OAuth (Google Auth Platform)
1. Chọn menu **`OAuth consent screen`** *(hoặc **Google Auth Platform**)* ở cột bên trái.
2. Nhấn nút màu xanh **`Get started`** ở giữa màn hình.
3. Điền các thông tin cơ bản:
   - **App name**: Nhập `NoticeDl`
   - **User support email**: Chọn địa chỉ Gmail của bạn.
   - **Audience** *(User Type)*: Chọn **External** (Ngoại bộ).
   - **Developer contact information**: Điền địa chỉ Gmail của bạn.
   - Nhấn **Create** (hoặc Save).
4. **Thêm Email dùng thử (Rất quan trọng)**:
   - Ở menu bên trái, chọn **`Audience`** $\rightarrow$ Kéo xuống mục **Test users** $\rightarrow$ Bấm **`+ Add users`**.
   - Nhập địa chỉ Gmail của bạn vào $\rightarrow$ Nhấn **Save**.

#### Bước 2.4: Tạo và tải file `credentials.json`
1. Ở menu bên trái, chọn **`Clients`** *(hoặc Credentials)*.
2. Phía trên cùng, bấm nút **`+ Create Client`** *(hoặc Create Credentials $\rightarrow$ OAuth client ID)*.
3. **Application type**: Chọn **Desktop app** (Ứng dụng máy tính).
4. **Name**: Điền `NoticeDl Client` $\rightarrow$ Nhấn **Create**.
5. Hộp thoại hiện ra $\rightarrow$ Nhấn nút **`Download JSON`** để tải file về máy.
6. **Đổi tên file vừa tải thành `credentials.json`** và di chuyển vào thư mục gốc của dự án.

---

### GIAI ĐOẠN 3: Cấu hình tài khoản & Chạy khởi tạo lần đầu

#### Bước 3.1: Điền thông tin Moodle UTH
Tạo file **`config.json`** (bằng cách sao chép từ `config.example.json`) và điền tài khoản Moodle UTH của bạn:
```json
{
  "moodle_username": "MSSV_CỦA_BẠN",
  "moodle_password": "MẬT_KHẨU_MOODLE",
  "google_calendar_id": "primary",
  "show_status_in_title": true,
  "use_color_tags": true,
  "mute_reminders_for_completed": true,
  "skip_completed_events": false,
  "reminder_minutes": [1440, 180, 60]
}
```

> **Tính năng tự động nhận diện bài ĐÃ LÀM / CHƯA LÀM:**
> - **Tiêu đề**: Tự động gắn tag `✅ [ĐÃ LÀM]` hoặc `⏳ [CHƯA LÀM]` ở đầu tiêu đề sự kiện.
> - **Màu sắc trên Google Calendar**:
>   - 🟢 **Xanh lá cây (Basil)**: Dành cho bài đã nộp/đã làm xong (kèm điểm số nếu có).
>   - 🔴 **Đỏ (Tomato)**: Dành cho bài trắc nghiệm/bài tập sắp hết hạn nhưng chưa làm.
>   - 🔵 **Xanh lam (Peacock)**: Dành cho sự kiện thông tin / điểm danh.
> - **Chống làm phiền**: Tự động tắt chuông nhắc nhở nếu bài đó đã làm xong.
> - **Tùy chọn ẩn bài đã làm**: Nếu chỉ muốn lịch hiển thị việc cần làm, đặt `"skip_completed_events": true`.

#### Bước 3.2: Thực thi lệnh chạy thử
Mở Terminal / PowerShell tại thư mục dự án và gõ:
```bash
python main.py
```

#### Bước 3.3: Cấp quyền xác thực Google
- Trình duyệt tự động mở trang đăng nhập Google.
- Bạn chọn tài khoản Gmail $\rightarrow$ Nhấn **Tiếp tục (Continue) / Allow** để xác nhận cấp quyền.
- File `token.json` sẽ tự động được sinh ra và bài tập sẽ được đẩy sang Google Calendar ngay lập tức!

---

### GIAI ĐOẠN 4: Thiết lập tự động đồng bộ 24/7 trên GitHub (Miễn phí)

Nếu không muốn bật máy tính mà lịch vẫn tự đồng bộ mỗi 3 tiếng:

1. Đưa toàn bộ thư mục dự án này lên **Private Repository** trên GitHub của bạn.
2. Chạy lệnh mã hóa token trên máy cá nhân:
   ```bash
   python encode_token.py
   ```
3. Copy chuỗi mã hóa hiển thị trên màn hình.
4. Trên GitHub Repo, vào **Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions** $\rightarrow$ **New repository secret**:
   - Secret 1: `GOOGLE_TOKEN_BASE64` (Dán chuỗi vừa mã hóa ở Bước 2).
   - Secret 2: `MOODLE_USERNAME` (Nhập MSSV của bạn).
   - Secret 3: `MOODLE_PASSWORD` (Nhập mật khẩu Moodle).
5. Kịch bản `.github/workflows/sync.yml` sẽ tự động quét và cập nhật bài tập mới về điện thoại của bạn mỗi 3 tiếng 24/7.
