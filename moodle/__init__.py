"""
Package moodle: Hệ thống thu thập, phân tích và đồng bộ dữ liệu từ các trang Moodle UTH.

Danh sách các module thành phần:
- constants.py: Cấu hình múi giờ Việt Nam (UTC+7), cấu hình HTTP User-Agent & Logging.
- utils.py: Các hàm tiện ích làm sạch tên bài tập/sự kiện và định dạng ngày giờ tiếng Việt.
- status_checker.py: Kiểm tra chi tiết trạng thái bài làm (đã làm, chưa làm, trích xuất điểm số, số lượt).
- fetcher.py: Lớp MoodleFetcher điều phối toàn bộ quá trình đăng nhập, quét sự kiện (REST, AJAX, HTML, iCal).
"""

from moodle.constants import VIETNAM_TZ, DEFAULT_HEADERS
from moodle.utils import clean_event_name, format_datetime_vn, parse_moodle_date
from moodle.status_checker import StatusChecker
from moodle.fetcher import MoodleFetcher
from moodle.portal_fetcher import PortalFetcher

__all__ = [
    "MoodleFetcher",
    "PortalFetcher",
    "StatusChecker",
    "VIETNAM_TZ",
    "DEFAULT_HEADERS",
    "clean_event_name",
    "format_datetime_vn",
    "parse_moodle_date",
]
