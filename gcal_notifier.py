import sys
import os
import json
import base64
import logging
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

SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar'
]

class GoogleCalendarNotifier:
    def __init__(
        self,
        calendar_id="primary",
        credentials_file="credentials.json",
        token_file="token.json",
        show_status_in_title=True,
        use_color_tags=True,
        mute_reminders_for_completed=True
    ):
        self.calendar_id = calendar_id
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.show_status_in_title = show_status_in_title
        self.use_color_tags = use_color_tags
        self.mute_reminders_for_completed = mute_reminders_for_completed
        self.service = self._authenticate()

    def _authenticate(self):
        """Xác thực Google API (hỗ trợ cả môi trường Local và GitHub Actions)."""
        creds = None

        # 1. Kiểm tra biến môi trường GOOGLE_TOKEN_JSON (Dành cho GitHub Actions / Docker)
        env_token_b64 = os.getenv("GOOGLE_TOKEN_BASE64")
        env_token_json = os.getenv("GOOGLE_TOKEN_JSON")

        if env_token_b64:
            try:
                token_data = json.loads(base64.b64decode(env_token_b64).decode("utf-8"))
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                logging.info("Xác thực thành công bằng GOOGLE_TOKEN_BASE64 từ biến môi trường.")
            except Exception as e:
                logging.warning(f"Không thể giải mã GOOGLE_TOKEN_BASE64: {e}")
        elif env_token_json:
            try:
                token_data = json.loads(env_token_json)
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                logging.info("Xác thực thành công bằng GOOGLE_TOKEN_JSON từ biến môi trường.")
            except Exception as e:
                logging.warning(f"Lỗi đọc GOOGLE_TOKEN_JSON: {e}")

        # 2. Kiểm tra file token.json trên máy cục bộ
        if not creds and os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            except Exception as e:
                logging.warning(f"Lỗi đọc {self.token_file}: {e}")

        # Refresh token nếu hết hạn
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logging.info("Đã làm mới (refresh) Google OAuth Token thành công.")
                # Lưu lại token mới
                with open(self.token_file, "w", encoding="utf-8") as token:
                    token.write(creds.to_json())
            except Exception as e:
                logging.warning(f"Không thể refresh token: {e}")
                creds = None

        # 3. Nếu chưa có token hợp lệ và có file credentials.json -> Chạy OAuth Flow trên trình duyệt
        if not creds:
            if os.path.exists(self.credentials_file):
                logging.info("Đang mở trình duyệt để xác thực cấp quyền Google Calendar lần đầu...")
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
                # Lưu token lại
                with open(self.token_file, "w", encoding="utf-8") as token:
                    token.write(creds.to_json())
                logging.info(f"Đã lưu token xác thực vào file: {self.token_file}")
            else:
                raise FileNotFoundError(
                    f"Không tìm thấy file '{self.credentials_file}' hoặc token xác thực. "
                    "Hãy tải file credentials.json từ Google Cloud Console về thư mục dự án."
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

        # 1. Tiêu đề hiển thị theo trạng thái (Đã làm / Chưa làm)
        final_summary = raw_summary
        if self.show_status_in_title:
            if status == "completed":
                final_summary = f"✅ [ĐÃ LÀM] {raw_summary}"
            elif status == "pending":
                final_summary = f"⏳ [CHƯA LÀM] {raw_summary}"

        # 2. Màu sắc nổi bật trên Google Calendar:
        # 10 = Basil (Xanh lá cây đậm - Đã hoàn thành)
        # 11 = Tomato (Đỏ - Bài tập/Trắc nghiệm chưa làm)
        # 7  = Peacock (Xanh lam - Sự kiện thông tin/Điểm danh)
        color_id = None
        if self.use_color_tags:
            if status == "completed":
                color_id = "10"
            elif status == "pending":
                color_id = "11"
            elif status == "info":
                color_id = "7"

        # 3. Chuông nhắc nhở: Tắt nhắc nhở nếu bài tập đã làm xong để tránh làm phiền
        if status == "completed" and self.mute_reminders_for_completed:
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

        gcal_event_body = {
            "summary": final_summary,
            "description": f"{description}\n\n[Đồng bộ tự động từ UTH Moodle]",
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
            gcal_event_body["start"] = {"dateTime": start_time}
            gcal_event_body["end"] = {"dateTime": end_time}
        else:
            gcal_event_body["start"] = {"date": start_time}
            gcal_event_body["end"] = {"date": end_time}

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

if __name__ == "__main__":
    print("Module gcal_notifier sẵn sàng.")
