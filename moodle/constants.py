"""
Module moodle.constants:
Chứa các hằng số, cấu hình môi trường và tiêu chuẩn định dạng chung của hệ thống:
1. Thiết lập bảng mã UTF-8 cho terminal trên hệ điều hành Windows để tránh lỗi font tiếng Việt.
2. Cấu hình định dạng Logging chung (hiển thị thời gian và cấp độ INFO, WARNING, ERROR).
3. Hằng số múi giờ VIETNAM_TZ: Chuẩn múi giờ Việt Nam (UTC+7 / GMT+7).
4. DEFAULT_HEADERS: Giả lập trình duyệt Chrome hiện đại để tránh bị máy chủ Moodle chặn truy cập.
"""

import sys
import logging
from datetime import timezone, timedelta

# Cấu hình UTF-8 cho console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Cấu hình log hệ thống
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Múi giờ Việt Nam (+07:00)
VIETNAM_TZ = timezone(timedelta(hours=7))

# HTTP Headers giả lập trình duyệt
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
