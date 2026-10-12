"""
Module moodle.portal_fetcher:
Điều phối đăng nhập và thu thập Thời khóa biểu (TKB) từ Cổng thông tin sinh viên UTH:
https://portal.ut.edu.vn/calendar

Chức năng:
1. Đăng nhập tự động qua API UTH Portal (/api/v1/user/login).
2. Quét Thời khóa biểu theo tuần (/api/v1/lichhoc/lichTuan?date=YYYY-MM-DD).
3. Chuẩn hóa các buổi học thành sự kiện Google Calendar ([TKB] Tên môn (Mã lớp)).
4. Trích xuất danh sách môn học kèm liên kết LMS để phục vụ quét bài tập chi tiết (Deep Scan).
"""

import re
import json
import random
import string
import logging
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

from moodle.constants import VIETNAM_TZ, DEFAULT_HEADERS


class PortalFetcher:
    """Thu thập Thời khóa biểu & thông tin môn học từ portal.ut.edu.vn."""

    BASE_URL = "https://portal.ut.edu.vn"
    API_LOGIN_URL = "https://portal.ut.edu.vn/api/v1/user/login"
    API_SCHEDULE_URL = "https://portal.ut.edu.vn/api/v1/lichhoc/lichTuan"

    # Bảng quy đổi tiết học sang giờ chuẩn (nếu API thiếu tuGio/denGio)
    TIET_HOURS = {
        1: ("06:45", "07:35"),
        2: ("07:35", "08:25"),
        3: ("08:35", "09:25"),
        4: ("09:25", "10:15"),
        5: ("10:15", "11:05"),
        6: ("11:05", "11:55"),
        7: ("12:20", "13:10"),
        8: ("13:10", "14:00"),
        9: ("14:00", "14:50"),
        10: ("14:50", "15:40"),
        11: ("15:40", "16:30"),
        12: ("16:30", "17:20"),
        13: ("17:30", "18:20"),
        14: ("18:20", "19:10"),
        15: ("19:10", "20:00"),
        16: ("20:00", "20:50"),
    }

    def __init__(self, username: str, password: str):
        self.username = username.strip().strip("[]") if username else ""
        self.password = password.strip().strip("[]") if password else ""
        self.token = None
        self.user_info = None

    @staticmethod
    def _generate_fake_captcha() -> str:
        """Sinh chuỗi giả lập captcha 32 ký tự theo đúng thuật toán client của UTH Portal."""
        chars = string.ascii_letters + string.digits
        return "".join(random.choice(chars) for _ in range(32))

    def login(self) -> bool:
        """Đăng nhập vào Portal UTH để lấy Bearer token."""
        if not self.username or not self.password:
            return False

        captcha = self._generate_fake_captcha()
        login_url = f"{self.API_LOGIN_URL}?g-recaptcha-response={captcha}"

        headers = dict(DEFAULT_HEADERS)
        headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/login",
        })

        try:
            logging.info(f"Đang đăng nhập UTH Portal ({self.username})...")
            res = requests.post(
                login_url,
                json={"username": self.username, "password": self.password},
                headers=headers,
                timeout=15,
            )
            data = res.json()
            if data.get("success") and data.get("token"):
                self.token = data["token"]
                self.user_info = data.get("body", {})
                logging.info(f"✅ Đăng nhập UTH Portal thành công ({self.username})!")
                return True
            else:
                msg = data.get("message") or "Đăng nhập không thành công"
                logging.warning(f"❌ Đăng nhập UTH Portal thất bại: {msg}")
                return False
        except Exception as e:
            logging.warning(f"Lỗi kết nối UTH Portal: {e}")
            return False

    def fetch_schedule_and_courses(
        self,
        weeks_back: int = 1,
        weeks_ahead: int = 12,
    ) -> tuple[list[dict], dict]:
        """
        Quét thời khóa biểu theo số tuần trước và sau.
        Returns:
            (events_list, courses_dict): Danh sách sự kiện TKB chuẩn hóa & Từ điển thông tin môn học.
        """
        if not self.token and not self.login():
            logging.warning("Không thể lấy thời khóa biểu vì chưa xác thực Portal.")
            return [], {}

        events = []
        courses_dict = {}
        now = datetime.now(VIETNAM_TZ)
        seen_event_uids = set()

        headers = dict(DEFAULT_HEADERS)
        headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.BASE_URL}/calendar",
        })

        logging.info(f"📅 Bắt đầu quét Thời khóa biểu UTH ({self.username}) từ {weeks_back} tuần trước đến {weeks_ahead} tuần tới...")
        empty_streak = 0

        for w_offset in range(-weeks_back, weeks_ahead + 1):
            target_date = (now + timedelta(weeks=w_offset)).strftime("%Y-%m-%d")
            url = f"{self.API_SCHEDULE_URL}?date={target_date}"
            try:
                res = requests.get(url, headers=headers, timeout=15)
                data = res.json()
                items = data.get("body", []) if isinstance(data, dict) else []

                if not items:
                    empty_streak += 1
                    if empty_streak >= 4 and w_offset > 2:
                        # Dừng nếu đã vượt quá thời gian học kỳ
                        logging.debug(f"Không có lịch học 4 tuần liên tiếp kể từ {target_date}, kết thúc quét.")
                        break
                    continue

                empty_streak = 0
                for item in items:
                    # Trích xuất môn học để quét deadline chuyên sâu sau này
                    ma_hp = str(item.get("maLopHocPhan") or "").strip()
                    ten_mon = str(item.get("tenMonHoc") or "").strip()
                    link_lms = item.get("link") or ""

                    if ma_hp and ma_hp not in courses_dict:
                        courses_dict[ma_hp] = {
                            "code": ma_hp,
                            "name": ten_mon,
                            "link": link_lms,
                            "tenPhong": item.get("tenPhong") or "",
                            "giangVien": item.get("giangVien") or "",
                        }

                    # Chuẩn hóa sự kiện buổi học cho Google Calendar
                    evt = self._normalize_schedule_item(item)
                    if evt and evt["uid"] not in seen_event_uids:
                        seen_event_uids.add(evt["uid"])
                        events.append(evt)

            except Exception as e:
                logging.warning(f"Lỗi khi tải lịch học tuần {target_date}: {e}")

        logging.info(f"✅ Đã quét xong TKB: {len(events)} buổi học | {len(courses_dict)} môn học.")
        return events, courses_dict

    def _normalize_schedule_item(self, item: dict) -> dict | None:
        """Chuyển đổi 1 buổi học từ Portal sang schema sự kiện Google Calendar."""
        raw_date = item.get("ngayBatDauHoc")  # VD: "12/10/2026"
        if not raw_date:
            return None

        # Parse ngày học
        try:
            d_parts = [int(p) for p in raw_date.split("/")]
            day, month, year = d_parts[0], d_parts[1], d_parts[2]
        except Exception:
            return None

        tu_tiet = item.get("tuTiet") or 1
        den_tiet = item.get("denTiet") or tu_tiet

        # Xác định giờ bắt đầu & kết thúc
        tu_gio = (item.get("tuGio") or "").strip()
        den_gio = (item.get("denGio") or "").strip()

        if not tu_gio and tu_tiet in self.TIET_HOURS:
            tu_gio = self.TIET_HOURS[tu_tiet][0]
        if not den_gio and den_tiet in self.TIET_HOURS:
            den_gio = self.TIET_HOURS[den_tiet][1]

        if not tu_gio:
            tu_gio = "07:00"
        if not den_gio:
            den_gio = "09:00"

        # Chuẩn hóa HH:MM
        try:
            s_h, s_m = [int(p) for p in tu_gio.split(":")[:2]]
            e_h, e_m = [int(p) for p in den_gio.split(":")[:2]]
            start_dt = datetime(year, month, day, s_h, s_m, tzinfo=VIETNAM_TZ)
            end_dt = datetime(year, month, day, e_h, e_m, tzinfo=VIETNAM_TZ)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=2)
        except Exception:
            return None

        ten_mon = (item.get("tenMonHoc") or "Buổi học").strip()
        ma_hp = (item.get("maLopHocPhan") or "").strip()
        phong = (item.get("tenPhong") or "").strip()
        link_lms = (item.get("link") or "").strip()
        link_zoom = (item.get("text1") or "").strip()
        ghi_chu = (item.get("ghiChu") or "").strip()
        is_tam_ngung = bool(item.get("isTamNgung"))

        prefix = "[TẠM NGƯNG] [TKB]" if is_tam_ngung else "[TKB]"
        summary = f"{prefix} {ten_mon}"
        if ma_hp:
            summary += f" ({ma_hp})"

        desc_parts = [
            f"📚 Môn học: {ten_mon}",
            f"🔖 Mã lớp học phần: {ma_hp}" if ma_hp else None,
            f"⏰ Tiết học: {tu_tiet} - {den_tiet} ({tu_gio} - {den_gio})",
            f"📍 Phòng: {phong}" if phong else None,
            f"💻 Link trực tuyến (Zoom): {link_zoom}" if link_zoom else None,
            f"🔗 Vào Moodle LMS: {link_lms}" if link_lms else None,
            f"📝 Ghi chú: {ghi_chu}" if ghi_chu else None,
            "⚠️ LƯU Ý: Buổi học này hiện đang TẠM NGƯNG!" if is_tam_ngung else None,
        ]
        description = "\n".join(p for p in desc_parts if p)

        # UID ổn định để cập nhật buổi học không bị duplicate
        item_id = item.get("id")
        if item_id:
            uid = f"uth_tkb_{item_id}"
        else:
            uid = f"uth_tkb_{ma_hp}_{year}{month:02d}{day:02d}_{tu_tiet}"

        return {
            "uid": uid,
            "summary": summary,
            "raw_summary": summary,
            "status": "info" if not is_tam_ngung else "cancelled",
            "status_detail": "Lịch học Portal" if not is_tam_ngung else "Tạm ngưng",
            "description": description,
            "location": phong,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "is_timetable": True,
            "is_tam_ngung": is_tam_ngung,
        }
