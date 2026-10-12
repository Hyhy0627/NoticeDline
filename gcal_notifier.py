"""
gcal_notifier.py - Bộ máy tương tác Google Calendar API cho NoticeDl.

Chức năng chính:
1. Xác thực Google OAuth2 đa môi trường (file token.json cục bộ, token Base64 từ GitHub Secrets, hoặc mở trình duyệt cấp quyền).
2. Tự động làm mới (refresh) token khi hết hạn để duy trì kết nối vĩnh viễn.
3. Đồng bộ sự kiện (buổi học Thời khóa biểu, deadline bài tập / Quiz Moodle) lên Google Calendar:
   - Chống trùng lặp tuyệt đối bằng privateExtendedProperty (moodle_uid).
   - Tự động gắn nhãn trạng thái [ĐÃ LÀM] / [CHƯA LÀM] vào tiêu đề.
   - Gán màu sắc chuẩn:
     * 9  (Blueberry - Xanh dương): Buổi học Thời khóa biểu.
     * 10 (Basil - Xanh lá): Bài tập / Quiz đã hoàn thành.
     * 11 (Tomato - Đỏ): Bài tập chưa làm / Buổi học TKB bị tạm ngưng.
     * 7  (Peacock - Xanh lam): Sự kiện thông tin chung.
   - Thiết lập chuông báo thông minh:
     * Thời khóa biểu: Báo trước 2 tiếng (120 phút) và 1 tiếng (60 phút).
     * Bài tập chưa làm: Báo trước 3 ngày, 2 ngày, 1 ngày, 10 giờ, 5 giờ.
     * Bài tập đã làm: Tự động tắt chuông báo (mute) để không làm phiền.
"""

import sys
import os
import json
import base64
import logging
import time
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Tắt cảnh báo cache không cần thiết từ googleapiclient
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

# Quyền hạn yêu cầu từ Google Calendar: Đọc và ghi sự kiện
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar'
]

class GoogleCalendarNotifier:
    """Quản lý kết nối và đẩy sự kiện từ Moodle/Portal sang Google Calendar."""

    def __init__(
        self,
        calendar_id="primary",
        credentials_file="credentials.json",
        token_file="token.json",
        token_base64=None,
        show_status_in_title=True,
        use_color_tags=True,
        mute_reminders_for_completed=True
    ):
        """
        Khởi tạo GoogleCalendarNotifier.
        
        Args:
            calendar_id: ID lịch Google ('primary' là lịch mặc định của tài khoản).
            credentials_file: Đường dẫn file client secrets OAuth do Google cấp (credentials.json).
            token_file: Đường dẫn file lưu phiên đăng nhập (token.json hoặc token_MSSV.json).
            token_base64: Chuỗi token đã mã hóa Base64 (dùng khi chạy trên GitHub Actions).
            show_status_in_title: Bật/tắt thêm tag [ĐÃ LÀM] / [CHƯA LÀM] vào tên sự kiện.
            use_color_tags: Bật/tắt đổi màu sắc sự kiện (xanh dương, xanh lá, đỏ).
            mute_reminders_for_completed: Tự động tắt chuông báo nếu bài tập đã hoàn thành.
        """
        self.calendar_id = calendar_id
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.token_base64 = token_base64
        self.show_status_in_title = show_status_in_title
        self.use_color_tags = use_color_tags
        self.mute_reminders_for_completed = mute_reminders_for_completed
        self.service = self._authenticate()

    def _authenticate(self):
        """Xác thực Google API (hỗ trợ cả môi trường Local và GitHub Actions)."""
        creds = None

        # 1. Kiểm tra file token cục bộ trước nếu tồn tại trên máy
        if os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            except Exception as e:
                logging.warning(f"Lỗi đọc file {self.token_file}: {e}")

        # 2. Nếu không có file token cục bộ, kiểm tra token Base64 / JSON (cho GitHub Actions / Docker)
        if not creds:
            env_token_b64 = self.token_base64 or os.getenv("GOOGLE_TOKEN_BASE64")
            env_token_json = os.getenv("GOOGLE_TOKEN_JSON")

            if env_token_b64:
                try:
                    token_data = json.loads(base64.b64decode(env_token_b64).decode("utf-8"))
                    creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                    logging.info(f"Xác thực thành công bằng token base64 cho '{self.token_file}'.")
                except Exception as e:
                    logging.warning(f"Không thể giải mã token base64: {e}")
            elif env_token_json:
                try:
                    token_data = json.loads(env_token_json)
                    creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                    logging.info("Xác thực thành công bằng GOOGLE_TOKEN_JSON từ biến môi trường.")
                except Exception as e:
                    logging.warning(f"Lỗi đọc GOOGLE_TOKEN_JSON: {e}")

        # 3. Refresh token nếu hết hạn
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logging.info(f"Đã làm mới (refresh) Google OAuth Token thành công: {self.token_file}")
                # Lưu lại token mới
                token_dir = os.path.dirname(os.path.abspath(self.token_file))
                if token_dir:
                    os.makedirs(token_dir, exist_ok=True)
                with open(self.token_file, "w", encoding="utf-8") as token:
                    token.write(creds.to_json())
            except Exception as e:
                logging.warning(f"Không thể refresh token ({self.token_file}): {e}")
                creds = None

        # 4. Nếu chưa có token hợp lệ và có file credentials.json -> Chạy OAuth Flow trên trình duyệt
        if not creds:
            if os.path.exists(self.credentials_file):
                print("\n" + "=" * 65)
                logging.info(f"🌐 Đang mở trình duyệt để xác thực tài khoản Google ({self.token_file})...")
                logging.info("👉 Vui lòng chọn đúng tài khoản Google và bấm 'Tiếp tục / Cho phép (Allow)'!")
                print("=" * 65)
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
                # Lưu token lại
                token_dir = os.path.dirname(os.path.abspath(self.token_file))
                if token_dir:
                    os.makedirs(token_dir, exist_ok=True)
                with open(self.token_file, "w", encoding="utf-8") as token:
                    token.write(creds.to_json())
                logging.info(f"✅ Đã lưu token xác thực vào file: {self.token_file}")
            else:
                raise FileNotFoundError(
                    f"Không tìm thấy file '{self.credentials_file}' hoặc token xác thực ({self.token_file}). "
                    "Hãy đảm bảo có file credentials.json trong thư mục dự án."
                )

        return build('calendar', 'v3', credentials=creds)

    def sync_event(self, event_data: dict, reminder_minutes: list = None):
        """Đồng bộ 1 sự kiện từ Moodle sang Google Calendar (Tránh trùng lặp bằng Extended Property)."""
        if reminder_minutes is None:
            reminder_minutes = [4320, 2880, 1440, 600, 300] # Nhắc trước 3 ngày, 2 ngày, 1 ngày, 10 giờ, 5 giờ

        uid = event_data["uid"]
        raw_summary = event_data.get("raw_summary") or event_data["summary"]
        description = event_data.get("description", "")
        location = event_data.get("location", "")
        start_time = event_data["start"]
        end_time = event_data["end"]
        status = event_data.get("status", "info")

        # 1. Tiêu đề hiển thị theo trạng thái (Đã làm / Chưa làm hoặc Lịch học)
        is_timetable = bool(event_data.get("is_timetable"))
        final_summary = raw_summary
        if not is_timetable and self.show_status_in_title:
            if status == "completed":
                final_summary = f"✅ [ĐÃ LÀM] {raw_summary}"
            elif status == "pending":
                final_summary = f"⏳ [CHƯA LÀM] {raw_summary}"

        # 2. Màu sắc nổi bật trên Google Calendar:
        # 10 = Basil (Xanh lá cây đậm - Đã hoàn thành)
        # 11 = Tomato (Đỏ - Bài tập chưa làm / Buổi học tạm ngưng)
        # 9  = Blueberry (Xanh dương - Thời khóa biểu / Lịch học)
        # 7  = Peacock (Xanh lam - Sự kiện thông tin chung)
        color_id = None
        if self.use_color_tags:
            if is_timetable:
                color_id = "11" if event_data.get("is_tam_ngung") else "9"
            elif status == "completed":
                color_id = "10"
            elif status == "pending":
                color_id = "11"
            elif status == "info":
                color_id = "7"

        # 3. Chuông nhắc nhở
        if is_timetable:
            tt_reminders = event_data.get("timetable_reminder_minutes") or [120, 60]
            reminders_override = [
                {"method": "popup", "minutes": m} for m in tt_reminders
            ]
        elif status == "completed" and self.mute_reminders_for_completed:
            reminders_override = []
        else:
            reminders_override = [
                {"method": "popup", "minutes": m} for m in reminder_minutes
            ]

        # Tìm kiếm sự kiện đã có trên Google Calendar bằng moodle_uid
        existing_events = self.service.events().list(
            calendarId=self.calendar_id,
            privateExtendedProperty=f"moodle_uid={uid}"
        ).execute().get("items", [])

        source_tag = "[Đồng bộ tự động từ UTH Portal Thời khóa biểu]" if is_timetable else "[Đồng bộ tự động từ UTH Moodle]"
        gcal_event_body = {
            "summary": final_summary,
            "description": f"{description}\n\n{source_tag}",
            "location": location,
            "extendedProperties": {
                "private": {
                    "moodle_uid": uid
                }
            },
            "reminders": {
                "useDefault": False,
                "overrides": reminders_override
            }
        }

        if color_id:
            gcal_event_body["colorId"] = color_id

        # Xử lý format ngày/giờ (iso string)
        if "T" in start_time:
            # Phòng ngừa lỗi Google API nếu start == end
            if start_time == end_time:
                try:
                    dt_end = datetime.fromisoformat(end_time)
                    start_time = (dt_end - timedelta(hours=1)).isoformat()
                except Exception:
                    pass
            gcal_event_body["start"] = {"dateTime": start_time}
            gcal_event_body["end"] = {"dateTime": end_time}
        else:
            gcal_event_body["start"] = {"date": start_time}
            gcal_event_body["end"] = {"date": end_time}

        for attempt in range(3):
            try:
                if existing_events:
                    event_id = existing_events[0]["id"]
                    updated_event = self.service.events().update(
                        calendarId=self.calendar_id,
                        eventId=event_id,
                        body=gcal_event_body
                    ).execute()
                    logging.info(f"[CẬP NHẬT] {final_summary} (ID: {event_id})")
                    return "updated", updated_event
                else:
                    created_event = self.service.events().insert(
                        calendarId=self.calendar_id,
                        body=gcal_event_body
                    ).execute()
                    logging.info(f"[TẠO MỚI] {final_summary} (ID: {created_event['id']})")
                    return "created", created_event
            except Exception as e:
                err_str = str(e)
                if ("rateLimitExceeded" in err_str or "userRateLimitExceeded" in err_str or "429" in err_str or "503" in err_str) and attempt < 2:
                    wait_sec = 2 ** (attempt + 1)
                    logging.warning(f"Google API rate limit, thử lại sau {wait_sec}s: {e}")
                    time.sleep(wait_sec)
                else:
                    raise e

if __name__ == "__main__":
    print("Module gcal_notifier sẵn sàng.")
