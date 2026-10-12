"""
Module moodle.utils:
Cung cấp các hàm tiện ích hỗ trợ xử lý dữ liệu chuỗi và thời gian:
1. clean_event_name(raw_name): Làm sạch các từ khoá thừa của Moodle (như opens, closes, is due, kết thúc, v.v.)
   giúp ghép cặp sự kiện Mở và Đóng của cùng 1 bài học chính xác hơn.
2. format_datetime_vn(ts): Chuyển đổi timestamp Unix sang chuỗi ngày giờ tiếng Việt có tên thứ trong tuần (Thứ Hai, Thứ Ba,...).
"""

import html
import re
from datetime import datetime
from moodle.constants import VIETNAM_TZ


def clean_event_name(raw_name: str) -> str:
    """
    Làm sạch các hậu tố và tiền tố thừa của Moodle (opens, closes, due, v.v.).
    Ví dụ: 'Trắc nghiệm Chương 1 opens' -> 'Trắc nghiệm Chương 1'
    """
    name = html.unescape(raw_name or "").strip()
    # Xóa hậu tố
    name = re.sub(
        r'\s+(?:opens|open|closes|close|is due|due|kết thúc|tới hạn|hết hạn|đóng|quiz|assignment)$',
        '',
        name,
        flags=re.IGNORECASE
    ).strip()
    # Xóa tiền tố tag cũ nếu có
    name = re.sub(
        r'^(?:\[MỞ ĐỀ\]|\[OPEN\]|\[OPENS\]|\[CLOSES\]|\[CLOSE\]|opens|open|closes|close|is due|due|kết thúc|tới hạn|hết hạn|đóng)\s*:\s*',
        '',
        name,
        flags=re.IGNORECASE
    ).strip()
    return name


def format_datetime_vn(ts) -> str:
    """
    Định dạng timestamp Unix sang chuỗi ngày giờ tiếng Việt dễ đọc.
    Ví dụ: '17:00 - Thứ Ba, 06/10/2026'
    """
    if not ts or int(ts) <= 0:
        return ""
    dt = datetime.fromtimestamp(int(ts), tz=VIETNAM_TZ)
    days_vi = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    day_name = days_vi[dt.weekday()]
    return f"{dt.strftime('%H:%M')} - {day_name}, {dt.strftime('%d/%m/%Y')}"


def parse_moodle_date(date_str: str):
    """
    Phân tích chuỗi ngày giờ từ Moodle (hỗ trợ cả tiếng Anh lẫn tiếng Việt) thành datetime có tzinfo.
    Ví dụ:
      'Monday, 12 October 2026, 11:59 PM'
      'Thứ Hai, 12 Tháng Mười 2026, 23:59'
    """
    if not date_str or date_str.strip() == "-":
        return None
    s = date_str.strip()
    s = re.sub(r"^[A-Za-zÀ-ỹ\s]+,\s*", "", s)
    formats = [
        "%d %B %Y, %I:%M %p",
        "%d %B %Y, %H:%M",
        "%d/%m/%Y, %H:%M",
        "%d-%m-%Y, %H:%M",
        "%d %b %Y, %I:%M %p",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=VIETNAM_TZ)
        except ValueError:
            pass

    vn_months = {
        "tháng mười một": "November", "tháng mười hai": "December", "tháng mười": "October",
        "tháng một": "January", "tháng hai": "February", "tháng ba": "March",
        "tháng tư": "April", "tháng năm": "May", "tháng sáu": "June",
        "tháng bảy": "July", "tháng tám": "August", "tháng chín": "September",
        "tháng 11": "November", "tháng 12": "December", "tháng 10": "October",
        "tháng 1": "January", "tháng 2": "February", "tháng 3": "March",
        "tháng 4": "April", "tháng 5": "May", "tháng 6": "June",
        "tháng 7": "July", "tháng 8": "August", "tháng 9": "September",
    }
    s_lower = s.lower()
    for vn, en in vn_months.items():
        if vn in s_lower:
            s_lower = s_lower.replace(vn, en)
            break
    for fmt in ["%d %B %Y, %H:%M", "%d %B %Y, %I:%M %p"]:
        try:
            dt = datetime.strptime(s_lower, fmt)
            return dt.replace(tzinfo=VIETNAM_TZ)
        except ValueError:
            pass
    return None

