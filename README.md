# 📌 NoticeDline - Tool Tự Động Đồng Bộ Lịch Học (TKB Portal) & Deadline UTH Moodle lên Google Calendar

> Dành riêng cho sinh viên **Trường Đại học Giao thông vận tải TP.HCM (UTH)**.
> Giúp bạn tự động đồng bộ toàn bộ lịch học và bài tập về điện thoại/laptop, không bao giờ lo quên giờ vào lớp hay trễ hạn nộp bài!

---

### 🌟 Các tính năng nổi bật:
1. 📅 **Tự động quét Thời khóa biểu (TKB) từ [portal.ut.edu.vn](https://portal.ut.edu.vn/calendar)**: Bắt trọn mọi buổi học trong tuần/học kỳ, hiển thị tên môn, mã lớp, tiết học, giờ học, phòng học, kèm link lớp LMS và link Zoom/Teams.
2. 🔎 **Quét sâu từng môn học (Deep Course Scanner)**: Tự động truy cập vào từng môn học trên Moodle (`courses.ut.edu.vn` & `thnn.ut.edu.vn`) để quét sạch 100% bài tập / trắc nghiệm (Assignment & Quiz) — **giải quyết triệt để tình trạng bỏ sót bài do giảng viên không đưa lên Lịch chung**.
3. 🔔 **Chuông thông báo thông minh**:
   - ⏰ **Thời khóa biểu**: Báo trước **2 tiếng** và **1 tiếng** trước giờ vào lớp.
   - ⏳ **Deadline bài tập**: Báo trước **3 ngày, 2 ngày, 1 ngày, 10 tiếng, 5 tiếng**.
   - 🔕 **Chống làm phiền**: Tự động tắt chuông nếu bài tập đã hoàn thành.
4. 🎨 **Màu sắc trực quan trên Google Calendar**:
   - 🔵 **Xanh dương (Blueberry)**: Buổi học Thời khóa biểu.
   - 🟢 **Xanh lá cây (Basil)**: Bài tập / Quiz đã hoàn thành `✅ [ĐÃ LÀM]` (kèm điểm số nếu có).
   - 🔴 **Đỏ (Tomato)**: Bài tập sắp đến hạn nhưng chưa làm `⏳ [CHƯA LÀM]` hoặc buổi học TKB bị tạm ngưng/báo nghỉ.

---

## 📖 HƯỚNG DẪN CÀI ĐẶT TỪ A - Z (DÀNH CHO NGƯỜI MỚI BẮT ĐẦU)

---

### GIAI ĐOẠN 1: Chuẩn bị
   **Cài đặt thư viện cần thiết**:
   - Mở cửa sổ **Terminal** (hoặc **PowerShell** / **Command Prompt**) tại thư mục dự án vừa tải về.
   - Chạy lệnh sau để tự động cài đặt toàn bộ thư viện:
     ```bash
     pip install -r requirements.txt
     ```

---

### GIAI ĐOẠN 2: Cấu hình tài khoản sinh viên (`config.json`)

Trong thư mục dự án có sẵn file cấu hình mẫu là `config.example.json`:

1. **Tạo file cấu hình**:
   - Nhấp chuột phải vào file **`config.example.json`** → chọn **Copy** → **Paste**.
   - Đổi tên file vừa paste thành **`config.json`**.

2. **Điền thông tin của bạn**:
   - Mở file **`config.json`** bằng Notepad hoặc VS Code.
   - Điền MSSV và Mật khẩu của bạn vào 2 dòng đầu tiên:
     ```json
     {
       "moodle_username": "MSSV_CỦA_BẠN",
       "moodle_password": "MẬT_KHẨU_MOODLE_HOẶC_PORTAL"
     }
     ```
   - Bấm **Ctrl + S** để lưu lại.

---

### GIAI ĐOẠN 3: Chuẩn bị chìa khóa kết nối (`credentials.json`)

Để tool được quyền ghi sự kiện vào Google Calendar của bạn, cần có file `credentials.json`. Bạn chọn **1 trong 2 cách** sau:

#### 👉 Cách 1: Nhanh nhất (Chỉ 1 phút - Khuyên dùng)
Nếu người chia sẻ tool cho bạn đã tạo sẵn Google Cloud Project:
1. Gửi địa chỉ **Gmail** của bạn cho người chia sẻ để họ thêm vào danh sách *Test Users*.
2. Nhận file **`credentials.json`** từ người chia sẻ và dán trực tiếp vào thư mục dự án này.
3. *Xong! Bạn chuyển ngay sang Giai đoạn 4.*

---

#### 👉 Cách 2: Tự tạo Google Cloud Project riêng từ A - Z
Nếu bạn muốn tự quản lý dự án độc lập:
1. **Truy cập Google Cloud Console**:
   - Vào [https://console.cloud.google.com/](https://console.cloud.google.com/) và đăng nhập tài khoản Google của bạn.
2. **Bật Google Calendar API**:
   - Chọn menu **APIs & Services** → chọn **Library** ở cột bên trái.
   - Tìm kiếm **`Google Calendar API`** → Bấm vào kết quả → Bấm nút xanh **Enable** (Bật).
3. **Cấu hình OAuth consent screen**:
   - Chọn menu **OAuth consent screen** ở cột bên trái → Bấm **Get started**.
   - **App name**: Điền `NoticeDline`.
   - **User support email**: Chọn email của bạn.
   - **Audience**: Chọn **External** → Nhấn **Create/Save**.
   - ⚠️ **Thêm Test Users (Rất quan trọng)**: Ở menu bên trái chọn **Audience** → kéo xuống mục **Test users** → bấm **+ Add users** → nhập chính địa chỉ Gmail của bạn vào → Lưu lại.
4. **Tải file `credentials.json`**:
   - Ở cột bên trái chọn **Clients** (hoặc Credentials) → bấm **+ Create Client** (OAuth client ID).
   - **Application type**: Chọn **Desktop app**.
   - **Name**: Điền `NoticeDline Client` → bấm **Create**.
   - Cửa sổ hiện ra → bấm **Download JSON**.
   - **Đổi tên file vừa tải thành `credentials.json`** và copy vào ngay thư mục dự án này.

---

### GIAI ĐOẠN 4: Chạy thử nghiệm lần đầu & Cấp quyền Google

1. **Khởi chạy tool**:
   - Mở Terminal / PowerShell tại thư mục dự án và gõ lệnh:
     ```bash
     python main.py
     ```

2. **Xác thực tài khoản Google**:
   - Trình duyệt web sẽ tự động mở lên trang đăng nhập của Google.
   - Bạn chọn đúng tài khoản Gmail muốn nhận lịch.
   - Nếu thấy cảnh báo *"Google chưa xác minh ứng dụng này"*, bấm **Nâng cao (Advanced)** → chọn **Đi tới NoticeDline (Không an toàn)**.
   - Tích chọn cấp quyền truy cập Lịch → Bấm **Tiếp tục / Cho phép (Continue/Allow)**.

3. **Kết quả**:
   - Màn hình hiển thị: *"The authentication flow has completed. You may close this window"*.
   - Quay lại màn hình Terminal, bạn sẽ thấy tool tự động quét và đồng bộ toàn bộ buổi học TKB cùng các deadline lên Google Calendar!
   - File `token.json` sẽ tự động được sinh ra để ghi nhớ phiên đăng nhập vĩnh viễn (lần sau chạy sẽ không hỏi lại nữa).

---

### GIAI ĐOẠN 5: Tự động hóa 24/7 trên GitHub Actions (Miễn phí, không cần mở máy)

Nếu bạn không muốn mỗi ngày phải bật máy tính chạy thủ công, hãy để máy chủ GitHub tự động chạy thay bạn mỗi 3 tiếng:

1. **Đưa code lên GitHub cá nhân**:
   - Tạo một Repository mới trên GitHub của bạn ở chế độ **Private** (Riêng tư).
   - Tải toàn bộ thư mục code này lên Repository đó *(yên tâm vì `.gitignore` đã tự động ẩn file mật khẩu `config.json` và `token.json` nên tuyệt đối an toàn)*.

2. **Lấy mã token Google**:
   - Trên máy tính cá nhân của bạn, mở Terminal tại thư mục tool và chạy:
     ```bash
     python encode_token.py
     ```
   - Sao chép toàn bộ chuỗi ký tự Base64 hiển thị trên màn hình.

3. **Cài đặt Secrets trên GitHub**:
   - Mở Repo của bạn trên GitHub → chọn tab **Settings** → chọn **Secrets and variables** → **Actions**.
   - Bấm nút **New repository secret** để thêm 3 biến sau:
     - 🔑 **`GOOGLE_TOKEN_BASE64`**: Dán chuỗi mã hóa vừa copy ở Bước 2.
     - 🔑 **`MOODLE_USERNAME`**: Điền MSSV của bạn.
     - 🔑 **`MOODLE_PASSWORD`**: Điền mật khẩu Moodle/Portal của bạn.

4. **Kiểm tra hoạt động**:
   - Vào tab **Actions** trên GitHub → chọn workflow **`Auto Sync Moodle Deadlines to Google Calendar`**.
   - Bấm nút **Run workflow** (màu xanh bên phải) để chạy thử ngay lập tức.
   - Khi hiện dấu tick xanh ✅ là thành công! Kể từ nay, cứ mỗi 3 tiếng GitHub sẽ tự động cập nhật bài tập mới nhất về Google Calendar trên điện thoại của bạn.

---

## ❓ CÂU HỎI THƯỜNG GẶP (FAQ)

- **Q: Tôi chỉ muốn xem các bài tập CHƯA LÀM, ẩn bớt bài đã xong cho đỡ rối mắt?**
  - **A:** Mở file `config.json`, đổi `"skip_completed_events": true`.
- **Q: Muốn đổi thời gian chuông báo trước khi vào lớp thì làm thế nào?**
  - **A:** Trong `config.json`, sửa `"timetable_reminder_minutes": [120, 60]` thành số phút bạn muốn (ví dụ `[60, 30]` là báo trước 1 tiếng và 30 phút).
- **Q: Tại sao sau 60 ngày GitHub Actions ngừng tự động chạy?**
  - **A:** Đây là chính sách tiết kiệm tài nguyên của GitHub đối với các repo không có commit mới sau 60 ngày. Bạn chỉ cần vào tab **Actions** bấm **Enable workflow** lại là được.
