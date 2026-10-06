import sys
import os
import json
import logging

# Đảm bảo UTF-8 cho stdout trên Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from moodle_fetcher import MoodleFetcher
from gcal_notifier import GoogleCalendarNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def load_config():
    load_dotenv()
    
    config = {
        "moodle_username": os.getenv("MOODLE_USERNAME", ""),
        "moodle_password": os.getenv("MOODLE_PASSWORD", ""),
        "moodle_session": os.getenv("MOODLE_SESSION", ""),
        "moodle_token": os.getenv("MOODLE_TOKEN", ""),
        "moodle_ical_url": os.getenv("MOODLE_ICAL_URL", ""),
        "moodle_urls": ["https://courses.ut.edu.vn", "https://thnn.ut.edu.vn"],
        "google_calendar_id": os.getenv("GOOGLE_CALENDAR_ID", "primary"),
        "reminder_minutes": [4320, 2880, 1440, 600, 300],
        "event_types": ["course", "user"],
        "show_status_in_title": True,
        "use_color_tags": True,
        "mute_reminders_for_completed": True,
        "skip_completed_events": False,
    }

    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                json_config = json.load(f)
                for key in [
                    "moodle_username", "moodle_password", "moodle_session", "moodle_token",
                    "moodle_ical_url", "moodle_urls", "moodle_url", "moodle_base_url",
                    "google_calendar_id", "reminder_minutes", "event_types",
                    "show_status_in_title", "use_color_tags", "mute_reminders_for_completed",
                    "skip_completed_events"
                ]:
                    if json_config.get(key) is not None:
                        config[key] = json_config[key]
        except Exception as e:
            logging.warning(f"Không thể đọc file config.json: {e}")

    env_urls = os.getenv("MOODLE_URLS", "")
    if env_urls:
        config["moodle_urls"] = [u.strip() for u in env_urls.split(",") if u.strip()]

    # Chuẩn hóa danh sách các link Moodle (tự động làm sạch các link dài như /calendar/view.php...)
    raw_urls = config.get("moodle_urls", [])
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    elif not isinstance(raw_urls, list):
        raw_urls = []

    for single_key in ["moodle_url", "moodle_base_url"]:
        if config.get(single_key):
            raw_urls.append(config[single_key])

    cleaned_urls = []
    seen = set()
    for u in raw_urls:
        if not u:
            continue
        import re
        m = re.match(r"(https?://[^/]+)", str(u).strip())
        base = m.group(1) if m else str(u).strip().rstrip("/")
        if base and base not in seen:
            seen.add(base)
            cleaned_urls.append(base)

    if not cleaned_urls:
        cleaned_urls = ["https://courses.ut.edu.vn", "https://thnn.ut.edu.vn"]

    config["moodle_urls"] = cleaned_urls
    return config

def main():
    print("=" * 65)
    print("   TOOL TỰ ĐỘNG ĐỒNG BỘ LỊCH & DEADLINE UTH MOODLE -> GOOGLE CALENDAR   ")
    print("=" * 65)

    config = load_config()

    has_credentials = bool(config["moodle_username"] and config["moodle_password"])
    has_session = bool(config["moodle_session"])
    has_token = bool(config["moodle_token"])
    has_ical = bool(config["moodle_ical_url"])

    if not (has_credentials or has_session or has_token or has_ical):
        logging.error("CHƯA CẤU HÌNH THÔNG TIN KẾT NỐI MOODLE UTH!")
        print("\n" + "=" * 65)
        print("👉 HƯỚNG DẪN CẤU HÌNH (Mở file 'config.json'):")
        print('  Điền tài khoản và mật khẩu Moodle UTH vào "config.json":')
        print('  {')
        print('    "moodle_username": "MSSV_CỦA_BẠN",')
        print('    "moodle_password": "MẬT_KHẨU_MOODLE"')
        print('  }')
        print("=" * 65 + "\n")
        return

    try:
        # 1. Quét sự kiện từ tất cả các trang Moodle UTH được cấu hình (Courses & THNN)
        events = []
        seen_uids = set()

        for base_url in config["moodle_urls"]:
            logging.info(f"🌐 Đang quét lịch từ: {base_url}")
            try:
                # Chỉ dùng wstoken cho courses.ut.edu.vn vì thnn.ut.edu.vn dùng form login
                site_token = config["moodle_token"] if "courses.ut.edu.vn" in base_url else None
                fetcher = MoodleFetcher(
                    base_url=base_url,
                    username=config["moodle_username"],
                    password=config["moodle_password"],
                    session_cookie=config["moodle_session"],
                    wstoken=site_token,
                    ical_url=config["moodle_ical_url"],
                    allowed_event_types=config["event_types"]
                )
                site_events = fetcher.fetch_events()
                for e in site_events:
                    if e["uid"] not in seen_uids:
                        seen_uids.add(e["uid"])
                        events.append(e)
            except Exception as e:
                logging.warning(f"Không thể quét lịch từ {base_url}: {e}")

        if not events:
            logging.info("Không tìm thấy sự kiện hoặc bài tập nào cần đồng bộ.")
            return

        # Lọc bỏ bài đã làm nếu người dùng cấu hình skip_completed_events = true
        if config.get("skip_completed_events"):
            logging.info("Cấu hình 'skip_completed_events' đang BẬT: Bỏ qua các bài đã hoàn thành.")
            events = [e for e in events if e.get("status") != "completed"]

        # 2. Khởi tạo Google Calendar và đồng bộ
        notifier = GoogleCalendarNotifier(
            calendar_id=config["google_calendar_id"],
            show_status_in_title=config.get("show_status_in_title", True),
            use_color_tags=config.get("use_color_tags", True),
            mute_reminders_for_completed=config.get("mute_reminders_for_completed", True)
        )

        created_count = 0
        updated_count = 0
        completed_count = sum(1 for e in events if e.get("status") == "completed")
        pending_count = sum(1 for e in events if e.get("status") == "pending")

        for evt in events:
            status, _ = notifier.sync_event(evt, reminder_minutes=config["reminder_minutes"])
            if status == "created":
                created_count += 1
            elif status == "updated":
                updated_count += 1

        print("-" * 65)
        logging.info(f"✨ HOÀN THÀNH: Đã tạo mới {created_count}, Cập nhật {updated_count} sự kiện lên Google Calendar.")
        logging.info(f"📊 Thống kê: ✅ {completed_count} bài đã hoàn thành | ⏳ {pending_count} bài chưa làm (cần nộp)")
        print("=" * 65)

    except Exception as e:
        logging.error(f"Đã xảy ra lỗi trong quá trình thực thi: {e}")

if __name__ == "__main__":
    main()
