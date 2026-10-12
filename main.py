"""
main.py - Chương trình điều phối chính của dự án NoticeDl.

Mục đích:
- Tự động lấy Thời khóa biểu từ UTH Portal (portal.ut.edu.vn/calendar).
- Tự động lấy Deadline, Quiz, Assignment từ UTH Moodle (courses.ut.edu.vn & thnn.ut.edu.vn).
- Sử dụng cơ chế Quét Sâu (Deep Course Scanner) để không bỏ sót các bài tập bị ẩn khỏi Lịch chung.
- Đồng bộ toàn bộ dữ liệu lên Google Calendar với màu sắc và chuông báo thông minh.
- Hỗ trợ chạy đa tài khoản (multi-account) độc lập hoặc dùng chung 1 lịch Google.

Luồng hoạt động (Workflow):
1. load_config(): Đọc cấu hình từ config.json / biến môi trường, khởi tạo danh sách tài khoản.
2. main(): Lặp qua từng tài khoản (cô lập lỗi, tài khoản này lỗi không ảnh hưởng tài khoản khác).
3. fetch_account_events():
   - Gọi PortalFetcher quét TKB (trả về danh sách buổi học + danh sách môn học của học kỳ).
   - Gọi MoodleFetcher quét sâu từng môn học để bắt trọn deadline và kiểm tra trạng thái nộp bài.
4. sync_account(): Gọi GoogleCalendarNotifier để thêm/cập nhật sự kiện lên Google Calendar.
5. In bảng thống kê chi tiết kết quả đồng bộ.
"""

import sys
import os
import json
import logging
import re
from dotenv import load_dotenv

# Đảm bảo UTF-8 cho stdout trên Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from moodle import MoodleFetcher, PortalFetcher
from gcal_notifier import GoogleCalendarNotifier

# Cấu hình logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_config() -> dict:
    """Tải và chuẩn hóa toàn bộ cấu hình từ config.json và biến môi trường."""
    load_dotenv()

    config = {
        "moodle_urls": ["https://courses.ut.edu.vn", "https://thnn.ut.edu.vn"],
        "google_calendar_id": os.getenv("GOOGLE_CALENDAR_ID", "primary"),
        "reminder_minutes": [4320, 2880, 1440, 600, 300],
        "event_types": ["course", "user"],
        "show_status_in_title": True,
        "show_account_in_title": None,  # None = tự động bật khi có nhiều tài khoản dùng chung 1 lịch
        "use_color_tags": True,
        "mute_reminders_for_completed": True,
        "skip_completed_events": False,
        "sync_timetable": True,
        "timetable_weeks_back": 1,
        "timetable_weeks_ahead": 12,
        "timetable_reminder_minutes": [120, 60],
        "deep_scan_courses": True,
        "accounts": []
    }

    # Đọc từ file config.json nếu có
    json_config = {}
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                json_config = json.load(f)
                for key in [
                    "moodle_username", "moodle_password", "moodle_session", "moodle_token",
                    "moodle_ical_url", "moodle_urls", "moodle_url", "moodle_base_url",
                    "google_calendar_id", "reminder_minutes", "event_types",
                    "show_status_in_title", "show_account_in_title", "use_color_tags",
                    "mute_reminders_for_completed", "skip_completed_events", "accounts",
                    "sync_timetable", "timetable_weeks_back", "timetable_weeks_ahead",
                    "timetable_reminder_minutes", "deep_scan_courses"
                ]:
                    if json_config.get(key) is not None:
                        config[key] = json_config[key]
        except Exception as e:
            logging.warning(f"Không thể đọc file config.json: {e}")

    # Chuẩn hóa danh sách các link Moodle
    env_urls = os.getenv("MOODLE_URLS", "")
    raw_urls = config.get("moodle_urls", [])
    if env_urls:
        raw_urls = [u.strip() for u in env_urls.split(",") if u.strip()]
    elif isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    elif not isinstance(raw_urls, list):
        raw_urls = []

    for single_key in ["moodle_url", "moodle_base_url"]:
        if config.get(single_key):
            raw_urls.append(config[single_key])

    cleaned_urls = []
    seen_urls = set()
    for u in raw_urls:
        if not u:
            continue
        m = re.match(r"(https?://[^/]+)", str(u).strip())
        base = m.group(1) if m else str(u).strip().rstrip("/")
        if base and base not in seen_urls:
            seen_urls.add(base)
            cleaned_urls.append(base)

    config["moodle_urls"] = cleaned_urls or ["https://courses.ut.edu.vn", "https://thnn.ut.edu.vn"]

    # Chuẩn hóa danh sách tài khoản
    accounts = []
    raw_accounts = config.get("accounts")
    if isinstance(raw_accounts, list) and len(raw_accounts) > 0:
        for idx, acc in enumerate(raw_accounts, start=1):
            if not isinstance(acc, dict):
                continue
            u = str(acc.get("moodle_username") or "").strip().strip("[]")
            p = str(acc.get("moodle_password") or "").strip().strip("[]")
            sess = acc.get("moodle_session", "")
            tok = acc.get("moodle_token", "")
            ical = acc.get("moodle_ical_url", "")
            label = acc.get("label") or acc.get("name") or u or f"Tài khoản {idx}"

            # Tách riêng file token Google cho mỗi tài khoản
            explicit_token = acc.get("google_token_file") or acc.get("token_file")
            if explicit_token:
                token_file = explicit_token
            elif idx == 1 and os.path.exists("token.json"):
                token_file = "token.json"
            elif u:
                token_file = f"token_{u}.json"
            else:
                token_file = f"token_acc{idx}.json"

            if (u and p) or sess or tok or ical:
                accounts.append({
                    "moodle_username": u,
                    "moodle_password": p,
                    "moodle_session": sess,
                    "moodle_token": tok,
                    "moodle_ical_url": ical,
                    "google_calendar_id": acc.get("google_calendar_id") or config.get("google_calendar_id", "primary"),
                    "google_token_file": token_file,
                    "google_token_base64": acc.get("google_token_base64") or acc.get("token_base64") or "",
                    "label": label,
                })

    # Fallback cho tài khoản đơn lẻ ở root config hoặc biến môi trường
    root_u = str(config.get("moodle_username") or os.getenv("MOODLE_USERNAME", "")).strip().strip("[]")
    root_p = str(config.get("moodle_password") or os.getenv("MOODLE_PASSWORD", "")).strip().strip("[]")
    root_sess = config.get("moodle_session") or os.getenv("MOODLE_SESSION", "")
    root_tok = config.get("moodle_token") or os.getenv("MOODLE_TOKEN", "")
    root_ical = config.get("moodle_ical_url") or os.getenv("MOODLE_ICAL_URL", "")

    if (root_u and root_p) or root_sess or root_tok or root_ical:
        if not any(a["moodle_username"] == root_u for a in accounts if root_u):
            accounts.insert(0, {
                "moodle_username": root_u,
                "moodle_password": root_p,
                "moodle_session": root_sess,
                "moodle_token": root_tok,
                "moodle_ical_url": root_ical,
                "google_calendar_id": config.get("google_calendar_id", "primary"),
                "google_token_file": config.get("google_token_file") or config.get("token_file") or "token.json",
                "google_token_base64": os.getenv("GOOGLE_TOKEN_BASE64", ""),
                "label": config.get("label") or config.get("name") or root_u or "Tài khoản chính",
            })

    config["accounts"] = accounts
    return config


def fetch_account_events(acc: dict, config: dict) -> list:
    """Quét và tổng hợp tất cả sự kiện, thời khóa biểu và deadline của một tài khoản."""
    events = []
    seen_uids = set()
    acc_label = acc["label"]
    moodle_urls = config.get("moodle_urls", [])
    event_types = config.get("event_types", ["course", "user"])
    course_hints = []

    # 1. Quét Thời khóa biểu (TKB) từ UTH Portal nếu bật sync_timetable
    if config.get("sync_timetable", True) and acc.get("moodle_username") and acc.get("moodle_password"):
        logging.info(f"📅 [{acc_label}] Đang quét Thời khóa biểu từ UTH Portal...")
        try:
            portal = PortalFetcher(acc["moodle_username"], acc["moodle_password"])
            w_back = int(config.get("timetable_weeks_back", 1))
            w_ahead = int(config.get("timetable_weeks_ahead", 12))
            tt_reminders = config.get("timetable_reminder_minutes", [120, 60])
            tt_events, courses_dict = portal.fetch_schedule_and_courses(weeks_back=w_back, weeks_ahead=w_ahead)
            course_hints = list(courses_dict.values())
            for te in tt_events:
                te["timetable_reminder_minutes"] = tt_reminders
                if te["uid"] not in seen_uids:
                    seen_uids.add(te["uid"])
                    events.append(te)
            logging.info(f"📅 [{acc_label}] Lấy thành công {len(tt_events)} buổi học TKB ({len(course_hints)} môn học).")
        except Exception as e:
            logging.warning(f"⚠️ [{acc_label}] Không thể quét Thời khóa biểu từ UTH Portal: {e}")

    # 2. Quét sự kiện và deadline từ các trang Moodle (courses.ut.edu.vn, thnn.ut.edu.vn)
    for base_url in moodle_urls:
        logging.info(f"🌐 [{acc_label}] Đang quét lịch từ: {base_url}")
        try:
            site_token = acc.get("moodle_token") if "courses.ut.edu.vn" in base_url else None
            fetcher = MoodleFetcher(
                base_url=base_url,
                username=acc["moodle_username"],
                password=acc["moodle_password"],
                session_cookie=acc.get("moodle_session"),
                wstoken=site_token,
                ical_url=acc.get("moodle_ical_url"),
                allowed_event_types=event_types
            )
            site_events = fetcher.fetch_events(course_hints=course_hints)
            for e in site_events:
                if e["uid"] not in seen_uids:
                    seen_uids.add(e["uid"])
                    events.append(e)
        except Exception as e:
            logging.warning(f"⚠️ [{acc_label}] Không thể quét lịch từ {base_url}: {e}")

    return events


def sync_account(
    acc: dict,
    global_config: dict,
    notifier_cache: dict,
    is_shared_calendar: bool,
    show_account_in_title: bool,
    idx: int
) -> dict:
    """Xử lý đồng bộ toàn diện cho một tài khoản (quét Portal TKB & Moodle -> đẩy Google Calendar)."""
    acc_label = acc["label"]
    acc_user = acc["moodle_username"]
    cal_id = acc["google_calendar_id"]
    token_file = acc["google_token_file"]
    token_b64 = acc["google_token_base64"] or None

    print("\n" + "-" * 65)
    logging.info(f"👤 [{idx}/{len(global_config['accounts'])}] Đang xử lý: {acc_label} (MSSV: {acc_user or 'Cookie/Token'})")
    logging.info(f"📅 Google Calendar: {cal_id} | Token: {token_file}")
    print("-" * 65)

    # 1. Quét sự kiện từ Portal TKB & Moodle
    events = fetch_account_events(acc, global_config)

    if not events:
        logging.info(f"ℹ️ [{acc_label}] Không tìm thấy bài tập hoặc sự kiện nào cần đồng bộ.")
        return {"status": "ok", "events_found": 0, "timetable_count": 0, "created": 0, "updated": 0, "completed": 0, "pending": 0}

    # Lọc bỏ bài đã làm nếu bật skip_completed_events (không bỏ các buổi học TKB)
    if global_config.get("skip_completed_events"):
        logging.info(f"ℹ️ [{acc_label}] Cấu hình 'skip_completed_events' đang BẬT: Bỏ qua các bài đã xong.")
        events = [e for e in events if e.get("is_timetable") or e.get("status") != "completed"]

    # 2. Khởi tạo / Lấy GoogleCalendarNotifier từ bộ nhớ đệm
    cache_key = (cal_id, token_file, token_b64 or "")
    if cache_key not in notifier_cache:
        notifier_cache[cache_key] = GoogleCalendarNotifier(
            calendar_id=cal_id,
            token_file=token_file,
            token_base64=token_b64,
            show_status_in_title=global_config.get("show_status_in_title", True),
            use_color_tags=global_config.get("use_color_tags", True),
            mute_reminders_for_completed=global_config.get("mute_reminders_for_completed", True)
        )
    notifier = notifier_cache[cache_key]

    # 3. Đồng bộ từng sự kiện lên Google Calendar
    created_count = 0
    updated_count = 0
    timetable_count = sum(1 for e in events if e.get("is_timetable"))
    completed_count = sum(1 for e in events if not e.get("is_timetable") and e.get("status") == "completed")
    pending_count = sum(1 for e in events if not e.get("is_timetable") and e.get("status") == "pending")

    for evt in events:
        evt_sync = dict(evt)

        # Nếu dùng chung lịch Google, thêm MSSV vào UID để tránh xung đột bài tập giữa 2 người
        if is_shared_calendar:
            uid_suffix = acc_user if acc_user else f"acc_{idx}"
            evt_sync["uid"] = f"{evt['uid']}_{uid_suffix}"

        # Gắn nhãn tên tài khoản vào tiêu đề nếu cần
        if show_account_in_title:
            raw = evt_sync.get("raw_summary") or evt_sync.get("summary")
            evt_sync["raw_summary"] = f"[{acc_label}] {raw}"
            evt_sync["summary"] = f"[{acc_label}] {evt_sync.get('summary')}"

        status, _ = notifier.sync_event(evt_sync, reminder_minutes=global_config["reminder_minutes"])
        if status == "created":
            created_count += 1
        elif status == "updated":
            updated_count += 1

    logging.info(f"✅ Hoàn thành [{acc_label}]: Tạo mới {created_count}, Cập nhật {updated_count} sự kiện.")
    stats_msg = f"📊 Thống kê [{acc_label}]: "
    if timetable_count:
        stats_msg += f"📅 {timetable_count} buổi học TKB | "
    stats_msg += f"✅ {completed_count} bài đã hoàn thành | ⏳ {pending_count} bài chưa làm."
    logging.info(stats_msg)

    return {
        "status": "ok",
        "events_found": len(events),
        "timetable_count": timetable_count,
        "created": created_count,
        "updated": updated_count,
        "completed": completed_count,
        "pending": pending_count
    }


def main():
    print("=" * 65)
    print("   TOOL TỰ ĐỘNG ĐỒNG BỘ LỊCH & DEADLINE UTH MOODLE -> GOOGLE CALENDAR   ")
    print("=" * 65)

    config = load_config()
    accounts = config.get("accounts", [])

    if not accounts:
        logging.error("CHƯA CẤU HÌNH THÔNG TIN KẾT NỐI MOODLE UTH!")
        print("\n" + "=" * 65)
        print("👉 HƯỚNG DẪN CẤU HÌNH (Mở file 'config.json'):")
        print('  Điền thông tin tài khoản Moodle UTH vào "config.json"')
        print("=" * 65 + "\n")
        return

    # Phân tích xem các tài khoản đang đồng bộ về Lịch riêng hay Lịch chung
    distinct_destinations = set(
        (a["google_calendar_id"], a["google_token_file"], a["google_token_base64"])
        for a in accounts
    )
    is_shared_calendar = len(distinct_destinations) < len(accounts)

    show_account_in_title = config.get("show_account_in_title")
    if show_account_in_title is None:
        show_account_in_title = is_shared_calendar

    logging.info(f"🚀 Tìm thấy {len(accounts)} tài khoản trong cấu hình.")
    if is_shared_calendar:
        logging.info("ℹ️ Chế độ DÙNG CHUNG lịch: Tự động phân tách UID và gắn nhãn tài khoản vào tiêu đề.")
    else:
        logging.info("ℹ️ Chế độ LỊCH RIÊNG BIỆT: Mỗi tài khoản đồng bộ vào Google Calendar độc lập.")

    notifier_cache = {}
    account_results = []

    # Duyệt qua từng tài khoản (cô lập lỗi: lỗi 1 tài khoản không làm dừng toàn bộ)
    for idx, acc in enumerate(accounts, start=1):
        acc_label = acc["label"]
        try:
            res = sync_account(
                acc=acc,
                global_config=config,
                notifier_cache=notifier_cache,
                is_shared_calendar=is_shared_calendar,
                show_account_in_title=show_account_in_title,
                idx=idx
            )
            account_results.append((acc_label, "SUCCESS", res))
        except Exception as e:
            logging.error(f"❌ Xảy ra lỗi khi xử lý tài khoản [{acc_label}]: {e}")
            account_results.append((acc_label, "ERROR", str(e)))

    # Bảng tổng kết kết quả
    print("\n" + "=" * 65)
    print("📋 BẢNG TỔNG KẾT TIẾN TRÌNH ĐỒNG BỘ")
    print("=" * 65)
    total_created = 0
    total_updated = 0
    total_completed = 0
    total_pending = 0
    total_timetable = 0

    for label, status, data in account_results:
        if status == "SUCCESS":
            total_created += data["created"]
            total_updated += data["updated"]
            total_completed += data["completed"]
            total_pending += data["pending"]
            tt_c = data.get("timetable_count", 0)
            total_timetable += tt_c
            tt_str = f"📅 {tt_c} TKB | " if tt_c else ""
            print(f"✅ {label}: Tạo mới {data['created']} | Cập nhật {data['updated']} | ({tt_str}✅ {data['completed']} xong | ⏳ {data['pending']} cần làm)")
        else:
            print(f"❌ {label}: Lỗi ({data})")

    print("-" * 65)
    logging.info(f"✨ TỔNG CỘNG: Tạo mới {total_created}, Cập nhật {total_updated} sự kiện.")
    summary_status = f"📊 TRẠNG THÁI: "
    if total_timetable:
        summary_status += f"📅 {total_timetable} buổi học TKB | "
    summary_status += f"✅ {total_completed} bài hoàn thành | ⏳ {total_pending} bài chưa làm."
    logging.info(summary_status)
    print("=" * 65)


if __name__ == "__main__":
    main()
