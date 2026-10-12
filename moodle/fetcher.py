"""
Module moodle.fetcher:
Điều phối chính toàn bộ quá trình thu thập lịch và bài tập từ các cổng Moodle UTH:
1. Đăng nhập & Xác thực phiên:
   - Chiến lược 1: Lấy WebService token (login/token.php qua moodle_mobile_app).
   - Chiến lược 2: Đăng nhập Form web (login/index.php với logintoken & lấy sesskey).
   - Chiến lược 3: Dùng cookie MoodleSession có sẵn từ cấu hình.
2. Quét sự kiện theo các kênh thông minh:
   - Kênh 1 (_fetch_via_ws_activities): REST API quét thẳng assignments & quizzes của toàn bộ môn học.
   - Kênh 2 (_fetch_via_enrolled_courses): Deep Course Scanner vào thẳng trang môn học cào quiz/assign bị ẩn khỏi Lịch chung.
   - Kênh 3 (_fetch_via_wstoken): Dùng REST API Calendar của Moodle khi có wstoken.
   - Kênh 4 (_fetch_via_ajax): Dùng AJAX Service của Moodle (/lib/ajax/service.php) qua session.
   - Kênh 5 (_fetch_via_html_view): Cào trực tiếp giao diện lịch Moodle (/calendar/view.php).
   - Kênh 6 (_fetch_via_ical): Đọc file iCal URL dự phòng nếu được cung cấp.
3. Ghép cặp và chuẩn hóa sự kiện (_normalize_event):
   - Tự động ghép cặp thời gian Mở đề (Opens) và Đóng đề (Closes) của cùng một bài học.
   - Chuyển tiếp tới StatusChecker để xác định bài đã nộp hay chưa.
   - Chuẩn bị dữ liệu theo schema chuẩn để đồng bộ lên Google Calendar.
"""

import re
import html
import logging
import requests
from urllib.parse import urljoin
from datetime import datetime, timedelta
from icalendar import Calendar

from moodle.constants import VIETNAM_TZ, DEFAULT_HEADERS
from moodle.utils import clean_event_name, format_datetime_vn, parse_moodle_date
from moodle.status_checker import StatusChecker


class MoodleFetcher:
    """
    Lớp điều phối thu thập lịch & deadline từ UTH Moodle (courses.ut.edu.vn & thnn.ut.edu.vn).
    """

    def __init__(
        self,
        base_url: str = "https://courses.ut.edu.vn",
        username: str = None,
        password: str = None,
        session_cookie: str = None,
        wstoken: str = None,
        ical_url: str = None,
        allowed_event_types: list = None,
    ):
        match = re.match(r"(https?://[^/]+)", base_url.strip())
        self.base_url = match.group(1) if match else base_url.strip().rstrip("/")
        self.username = username.strip().strip("[]") if username else None
        self.password = password.strip().strip("[]") if password else None
        self.session_cookie = session_cookie
        self.wstoken = wstoken
        self.ical_url = ical_url
        self.allowed_event_types = [t.lower() for t in (allowed_event_types or ["course", "user"])]
        self.sesskey = None
        self.userid = None
        self._course_quizzes = {}
        self._course_assigns = {}
        self._course_completions = {}
        self._checked_attempts = {}
        self._checked_submissions = {}
        self._calendar_activity_times = {}

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

        if self.session_cookie:
            self._set_cookie(self.session_cookie)

        self.status_checker = StatusChecker(self)

    def check_event_status(self, event_dict: dict):
        """Uỷ quyền kiểm tra trạng thái bài làm cho StatusChecker."""
        return self.status_checker.check_event_status(event_dict)

    @staticmethod
    def _clean_event_name(raw_name: str) -> str:
        return clean_event_name(raw_name)

    @staticmethod
    def _format_datetime_vn(ts) -> str:
        return format_datetime_vn(ts)

    def _set_cookie(self, cookie_str: str):
        cookie_val = cookie_str.strip()
        if "MoodleSession=" in cookie_val:
            match = re.search(r"MoodleSession=([^;]+)", cookie_val)
            if match:
                cookie_val = match.group(1).strip()
        domain = re.sub(r"^https?://", "", self.base_url).split("/")[0]
        self.session.cookies.set("MoodleSession", cookie_val, domain=domain)

    def _login(self):
        """Thực hiện đăng nhập nếu có username & password."""
        if not self.username or not self.password:
            return False

        # 1. Thử lấy token qua Moodle Mobile WebService
        try:
            logging.info("Đang kiểm tra đăng nhập qua Moodle Mobile Service...")
            token_res = requests.post(
                f"{self.base_url}/login/token.php",
                data={
                    "username": self.username,
                    "password": self.password,
                    "service": "moodle_mobile_app",
                },
                timeout=15,
            )
            token_data = token_res.json()
            if "token" in token_data:
                self.wstoken = token_data["token"]
                logging.info("✅ Xác thực Moodle Mobile WebService thành công!")
                return True
        except Exception as e:
            logging.debug(f"Không lấy được token.php: {e}")

        # 2. Thử đăng nhập Web Form qua login/index.php
        try:
            logging.info("Đang thực hiện đăng nhập Form trên giao diện web Moodle UTH...")
            login_url = f"{self.base_url}/login/index.php"
            page_res = self.session.get(login_url, timeout=15)

            token_match = re.search(r'name="logintoken"\s+value="([^"]+)"', page_res.text)
            logintoken = token_match.group(1) if token_match else ""

            post_data = {
                "anchor": "",
                "logintoken": logintoken,
                "username": self.username,
                "password": self.password,
            }

            post_res = self.session.post(login_url, data=post_data, timeout=15, allow_redirects=True)

            if "login/index.php" in post_res.url and (
                "alert-danger" in post_res.text or "loginerrors" in post_res.text
            ):
                err_match = re.search(r'class="alert alert-danger"[^>]*>(.*?)</div>', post_res.text, re.S)
                msg = re.sub(r"<[^>]+>", "", err_match.group(1)).strip() if err_match else "Sai tài khoản hoặc mật khẩu"
                logging.error(f"❌ Đăng nhập Moodle thất bại: {msg}")
                return False

            sesskey_match = re.search(r'"sesskey":"([^"]+)"', post_res.text)
            if sesskey_match:
                self.sesskey = sesskey_match.group(1)

            logging.info("✅ Đăng nhập web Moodle UTH thành công!")
            return True

        except Exception as e:
            logging.error(f"Lỗi khi thực hiện đăng nhập Moodle: {e}")
            return False

    def _ensure_session(self):
        """Đảm bảo phiên đăng nhập hoặc sesskey sẵn sàng."""
        if self.wstoken:
            return True

        if not self.session.cookies.get("MoodleSession"):
            if self.username and self.password:
                if not self._login():
                    return False
            elif not self.ical_url:
                logging.error("Thiếu thông tin xác thực (Cần username/password hoặc cookie MoodleSession).")
                return False

        # Lấy sesskey nếu chưa có
        if not self.sesskey and self.session.cookies.get("MoodleSession"):
            try:
                res = self.session.get(f"{self.base_url}/calendar/view.php?view=month", timeout=15)
                if "login/index.php" in res.url:
                    logging.warning("⚠️ Cookie MoodleSession đã hết hạn hoặc không hợp lệ.")
                    if self.username and self.password:
                        logging.info("🔄 Đang tự động đăng nhập lại bằng tài khoản & mật khẩu...")
                        self.session.cookies.clear()
                        if not self._login():
                            logging.error("❌ Tự động đăng nhập lại thất bại.")
                            return False
                    else:
                        logging.error("❌ Cookie MoodleSession đã hết hạn và không có username/password để đăng nhập lại.")
                        return False
                else:
                    sesskey_match = re.search(r'"sesskey":"([^"]+)"', res.text)
                    if sesskey_match:
                        self.sesskey = sesskey_match.group(1)
            except Exception as e:
                logging.warning(f"Lỗi kiểm tra session Moodle: {e}")

        return True

    def _is_allowed_event_type(self, evt: dict) -> bool:
        """Lọc sự kiện theo loại sự kiện được cấu hình (mặc định: 'course' và 'user')."""
        if not self.allowed_event_types:
            return True

        norm = (evt.get("normalisedeventtype") or "").strip().lower()
        if norm:
            return norm in self.allowed_event_types

        raw_type = (evt.get("eventtype") or "").strip().lower()
        if raw_type in ["course", "user"]:
            return raw_type in self.allowed_event_types

        course = evt.get("course")
        cid = course.get("id") if isinstance(course, dict) else evt.get("courseid")
        if cid and str(cid) not in ["0", "1"]:
            return "course" in self.allowed_event_types

        if evt.get("userid") and not evt.get("courseid"):
            return "user" in self.allowed_event_types

        return False

    def _get_userid(self):
        """Lấy Moodle userid của tài khoản hiện tại."""
        if self.userid:
            return self.userid
        if self.wstoken:
            try:
                res = requests.get(
                    f"{self.base_url}/webservice/rest/server.php",
                    params={
                        "wstoken": self.wstoken,
                        "wsfunction": "core_webservice_get_site_info",
                        "moodlewsrestformat": "json",
                    },
                    timeout=15,
                )
                data = res.json()
                self.userid = data.get("userid")
                return self.userid
            except Exception as e:
                logging.debug(f"Không lấy được site info: {e}")
        return None

    def _index_raw_events(self, raw_events: list):
        """Quét trước danh sách sự kiện để ghép cặp thời gian Mở (Opens) và Đóng (Closes)."""
        if not hasattr(self, "_calendar_activity_times"):
            self._calendar_activity_times = {}

        for evt in raw_events:
            clean_name = clean_event_name(evt.get("name", ""))
            ts = evt.get("timestart")
            if not clean_name or not ts:
                continue

            event_type = (evt.get("eventtype") or "").strip().lower()
            raw_name = evt.get("name", "")
            course = evt.get("course")
            course_id = str(course.get("id") if isinstance(course, dict) else evt.get("courseid", ""))
            modname = (evt.get("modulename") or "").lower()
            inst = str(evt.get("instance") or "")

            is_open = (event_type == "open" or bool(re.search(r'\b(?:opens|open)\b', raw_name, re.IGNORECASE)))
            is_close = (event_type in ["close", "due"] or bool(re.search(r'\b(?:closes|close|is due|due|kết thúc|tới hạn|hết hạn|đóng)\b', raw_name, re.IGNORECASE)))

            keys = [clean_name.lower()]
            if course_id:
                keys.append(f"{course_id}_{clean_name.lower()}")
            if modname and inst:
                keys.append(f"{modname}_{inst}")

            for k in keys:
                if k not in self._calendar_activity_times:
                    self._calendar_activity_times[k] = {}
                if is_open:
                    self._calendar_activity_times[k]["open"] = ts
                elif is_close:
                    self._calendar_activity_times[k]["close"] = ts

    def _get_activity_times(self, event_dict: dict, clean_name: str):
        """Lấy timestamp thời gian mở (Opens) và đóng (Closes) của bài học."""
        course = event_dict.get("course")
        course_id = str(course.get("id") if isinstance(course, dict) else event_dict.get("courseid", ""))
        modname = (event_dict.get("modulename") or "").lower()
        component = (event_dict.get("component") or "").lower()
        inst = str(event_dict.get("instance") or "")
        event_type = (event_dict.get("eventtype") or "").strip().lower()
        timestart = event_dict.get("timestart")

        open_ts = None
        close_ts = None

        if (modname == "quiz" or "mod_quiz" in component) and course_id and self.wstoken:
            try:
                if course_id not in self._course_quizzes:
                    q_data = requests.get(
                        f"{self.base_url}/webservice/rest/server.php",
                        params={
                            "wstoken": self.wstoken,
                            "wsfunction": "mod_quiz_get_quizzes_by_courses",
                            "moodlewsrestformat": "json",
                            "courseids[0]": course_id,
                        },
                        timeout=15,
                    ).json()
                    self._course_quizzes[course_id] = q_data.get("quizzes", [])

                for q in self._course_quizzes.get(course_id, []):
                    if str(q.get("coursemodule")) == inst or str(q.get("id")) == inst or clean_event_name(q.get("name", "")).lower() == clean_name.lower():
                        open_ts = q.get("timeopen")
                        close_ts = q.get("timeclose")
                        break
            except Exception as e:
                logging.debug(f"Lỗi tra cứu thời gian Quiz: {e}")

        elif (modname == "assign" or "mod_assign" in component) and course_id and self.wstoken:
            try:
                if course_id not in self._course_assigns:
                    a_data = requests.get(
                        f"{self.base_url}/webservice/rest/server.php",
                        params={
                            "wstoken": self.wstoken,
                            "wsfunction": "mod_assign_get_assignments",
                            "moodlewsrestformat": "json",
                            "courseids[0]": course_id,
                        },
                        timeout=15,
                    ).json()
                    assigns = []
                    for c in a_data.get("courses", []):
                        assigns.extend(c.get("assignments", []))
                    self._course_assigns[course_id] = assigns

                for a in self._course_assigns.get(course_id, []):
                    if str(a.get("cmid")) == inst or str(a.get("id")) == inst or clean_event_name(a.get("name", "")).lower() == clean_name.lower():
                        open_ts = a.get("allowsubmissionsfromdate")
                        close_ts = a.get("duedate")
                        break
            except Exception as e:
                logging.debug(f"Lỗi tra cứu thời gian Assignment: {e}")

        cache_map = getattr(self, "_calendar_activity_times", {})
        for lookup_key in [f"{modname}_{inst}", f"{course_id}_{clean_name.lower()}", clean_name.lower()]:
            if lookup_key in cache_map:
                if not open_ts and cache_map[lookup_key].get("open"):
                    open_ts = cache_map[lookup_key]["open"]
                if not close_ts and cache_map[lookup_key].get("close"):
                    close_ts = cache_map[lookup_key]["close"]
                if open_ts and close_ts:
                    break

        if not open_ts and (event_type == "open" or bool(re.search(r'\b(?:opens|open)\b', event_dict.get("name", ""), re.IGNORECASE))):
            open_ts = timestart
        if not close_ts and (event_type in ["close", "due"] or bool(re.search(r'\b(?:closes|close|is due|due|kết thúc|tới hạn|hết hạn|đóng)\b', event_dict.get("name", ""), re.IGNORECASE))):
            close_ts = timestart

        return open_ts, close_ts

    def _normalize_event(self, event_dict: dict):
        """Chuẩn hóa dữ liệu sự kiện theo schema chung cho Google Calendar."""
        event_id = str(event_dict.get("id", ""))
        raw_name = html.unescape(event_dict.get("name", "Sự kiện Moodle")).strip()
        event_type = (event_dict.get("eventtype") or "").strip().lower()

        is_open_event = (
            event_type == "open"
            or bool(re.search(r'\b(?:opens|open)\b', raw_name, re.IGNORECASE))
        )
        is_deadline_event = (
            event_type in ["close", "due"]
            or bool(re.search(r'\b(?:closes|close|is due|due|kết thúc|tới hạn|hết hạn|đóng)\b', raw_name, re.IGNORECASE))
        )

        clean_name = clean_event_name(raw_name)

        if is_open_event:
            summary = f"[OPEN] {clean_name}"
        elif is_deadline_event:
            summary = f"[CLOSES] {clean_name}"
        else:
            summary = clean_name

        status, status_detail = self.check_event_status(event_dict)

        raw_desc = event_dict.get("description", "") or ""
        clean_desc = html.unescape(re.sub(r"<br\s*/?>", "\n", raw_desc))
        clean_desc = re.sub(r"<[^>]+>", "", clean_desc).strip()

        course = event_dict.get("course")
        course_name = ""
        if isinstance(course, dict):
            course_name = course.get("fullname") or course.get("shortname") or ""
        elif isinstance(course, str):
            course_name = course

        url = event_dict.get("url", "")
        if not url and event_dict.get("action"):
            url = event_dict["action"].get("url", "")

        desc_parts = []
        if status == "completed":
            desc_parts.append(f"📌 Trạng thái: ✅ ĐÃ HOÀN THÀNH ({status_detail})")
        elif status == "pending":
            desc_parts.append(f"📌 Trạng thái: ⏳ CHƯA HOÀN THÀNH ({status_detail})")
        elif status_detail:
            desc_parts.append(f"📌 Ghi chú: {status_detail}")

        open_ts, close_ts = self._get_activity_times(event_dict, clean_name)
        open_str = format_datetime_vn(open_ts)
        close_str = format_datetime_vn(close_ts)

        time_lines = []
        if open_str:
            time_lines.append(f"🟢 Mở đề (Opens): {open_str}")
        if close_str:
            time_lines.append(f"🔴 Đóng đề (Closes): {close_str}")

        if time_lines:
            desc_parts.append("\n".join(time_lines))

        if clean_desc:
            desc_parts.append(clean_desc)
        if course_name:
            desc_parts.append(f"📚 Học phần: {course_name}")
        if url:
            desc_parts.append(f"🔗 Chi tiết Moodle: {url}")

        full_description = "\n\n".join(desc_parts)

        timestart = event_dict.get("timestart")
        timeduration = event_dict.get("timeduration", 0) or 0

        if timestart:
            ts_dt = datetime.fromtimestamp(int(timestart), tz=VIETNAM_TZ)
            if timeduration and int(timeduration) > 0:
                start_dt = ts_dt
                end_dt = datetime.fromtimestamp(int(timestart) + int(timeduration), tz=VIETNAM_TZ)
            elif is_deadline_event:
                end_dt = ts_dt
                start_dt = end_dt - timedelta(hours=1)
            else:
                start_dt = ts_dt
                end_dt = start_dt + timedelta(hours=1)
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()
        else:
            start_iso = event_dict.get("start")
            end_iso = event_dict.get("end", start_iso)
            # Tránh lỗi Google Calendar 400 Bad Request nếu start == end
            if start_iso and end_iso and start_iso == end_iso and "T" in start_iso:
                try:
                    dt = datetime.fromisoformat(end_iso)
                    start_iso = (dt - timedelta(hours=1)).isoformat()
                except Exception:
                    pass

        site_slug = "moodle"
        if "thnn" in self.base_url:
            site_slug = "thnn"
        elif "courses" not in self.base_url:
            match = re.search(r"https?://([^/]+)", self.base_url)
            if match:
                site_slug = match.group(1).replace(".", "_")

        uid = f"uth_{site_slug}_{event_id}" if event_id else f"uth_{site_slug}_{abs(hash(summary + str(timestart)))}"

        return {
            "uid": uid,
            "summary": summary,
            "raw_summary": summary,
            "status": status,
            "status_detail": status_detail,
            "description": full_description,
            "location": course_name,
            "start": start_iso,
            "end": end_iso,
        }

    def _fetch_via_wstoken(self):
        """Lấy sự kiện thông qua WebService REST API khi có wstoken."""
        logging.info("Đang quét lịch qua Moodle WebService REST API...")
        events_by_id = {}
        now = datetime.now(VIETNAM_TZ)

        months_to_query = [
            (now.year, now.month),
            (now.year if now.month < 12 else now.year + 1, now.month + 1 if now.month < 12 else 1),
        ]

        raw_events = []
        for yr, mo in months_to_query:
            try:
                res = requests.get(
                    f"{self.base_url}/webservice/rest/server.php",
                    params={
                        "wstoken": self.wstoken,
                        "wsfunction": "core_calendar_get_calendar_monthly_view",
                        "moodlewsrestformat": "json",
                        "year": yr,
                        "month": mo,
                    },
                    timeout=20,
                )
                data = res.json()
                for week in data.get("weeks", []):
                    for day in week.get("days", []):
                        for evt in day.get("events", []):
                            if self._is_allowed_event_type(evt):
                                raw_events.append(evt)
            except Exception as e:
                logging.warning(f"Lỗi khi tải lịch tháng {mo}/{yr}: {e}")

        try:
            res = requests.get(
                f"{self.base_url}/webservice/rest/server.php",
                params={
                    "wstoken": self.wstoken,
                    "wsfunction": "core_calendar_get_action_events_by_timesort",
                    "moodlewsrestformat": "json",
                    "timesortfrom": int(now.timestamp()) - 86400,
                    "limitnum": 50,
                },
                timeout=20,
            )
            data = res.json()
            for evt in data.get("events", []):
                if self._is_allowed_event_type(evt):
                    raw_events.append(evt)
        except Exception as e:
            logging.debug(f"Action events API: {e}")

        self._index_raw_events(raw_events)

        for evt in raw_events:
            norm = self._normalize_event(evt)
            events_by_id[norm["uid"]] = norm

        return list(events_by_id.values())

    def _fetch_via_ajax(self):
        """Lấy sự kiện thông qua AJAX service của Moodle."""
        logging.info("Đang quét lịch qua Moodle AJAX Service...")
        events_by_id = {}
        now = datetime.now(VIETNAM_TZ)

        months = [
            (now.year, now.month),
            (now.year if now.month < 12 else now.year + 1, now.month + 1 if now.month < 12 else 1),
        ]

        payload = [
            {
                "index": 0,
                "methodname": "core_calendar_get_calendar_monthly_view",
                "args": {"year": months[0][0], "month": months[0][1], "courseid": 1, "categoryid": 0},
            },
            {
                "index": 1,
                "methodname": "core_calendar_get_calendar_monthly_view",
                "args": {"year": months[1][0], "month": months[1][1], "courseid": 1, "categoryid": 0},
            },
            {
                "index": 2,
                "methodname": "core_calendar_get_calendar_upcoming_view",
                "args": {"courseid": 1, "categoryid": 0},
            },
            {
                "index": 3,
                "methodname": "core_calendar_get_action_events_by_timesort",
                "args": {"timesortfrom": int(now.timestamp()) - 86400, "limitnum": 50},
            },
        ]

        raw_events = []
        try:
            res = self.session.post(
                f"{self.base_url}/lib/ajax/service.php",
                params={"sesskey": self.sesskey or ""},
                json=payload,
                timeout=25,
            )
            data_list = res.json()

            for item in data_list:
                if item.get("error"):
                    continue
                data = item.get("data", {})

                if "weeks" in data:
                    for week in data.get("weeks", []):
                        for day in week.get("days", []):
                            for evt in day.get("events", []):
                                if self._is_allowed_event_type(evt):
                                    raw_events.append(evt)

                if "events" in data:
                    for evt in data.get("events", []):
                        if self._is_allowed_event_type(evt):
                            raw_events.append(evt)

            self._index_raw_events(raw_events)

            for evt in raw_events:
                norm = self._normalize_event(evt)
                events_by_id[norm["uid"]] = norm

        except Exception as e:
            logging.warning(f"Lỗi khi gọi AJAX Moodle: {e}")

        return list(events_by_id.values())

    def _fetch_via_html_view(self):
        """Quét HTML trang calendar/view.php nếu các API JSON bị chặn."""
        logging.info("Đang phân tích trực tiếp giao diện Lịch Moodle...")
        events = []
        try:
            res = self.session.get(f"{self.base_url}/calendar/view.php?view=upcoming", timeout=20)
            event_blocks = re.findall(
                r'<div[^>]*class="[^"]*event[^"]*"[^>]*data-event-id="(\d+)"[^>]*>(.*?)</div>\s*</div>',
                res.text,
                re.S,
            )
            for eid, content in event_blocks:
                title_match = re.search(r'<h3[^>]*class="[^"]*name[^"]*"[^>]*>(?:<a[^>]*>)?(.*?)(?:</a>)?</h3>', content, re.S)
                title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else f"Sự kiện Moodle {eid}"

                date_match = re.search(r'<div[^>]*class="[^"]*date[^"]*"[^>]*>(.*?)</div>', content, re.S)
                date_str = re.sub(r"<[^>]+>", "", date_match.group(1)).strip() if date_match else ""

                desc_match = re.search(r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>', content, re.S)
                desc = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip() if desc_match else ""

                course_match = re.search(r'<div[^>]*class="[^"]*course[^"]*"[^>]*>(?:<a[^>]*>)?(.*?)(?:</a>)?</div>', content, re.S)
                course = re.sub(r"<[^>]+>", "", course_match.group(1)).strip() if course_match else ""

                events.append({
                    "id": eid,
                    "name": title,
                    "description": f"{desc}\nThời gian ghi nhận: {date_str}" if date_str else desc,
                    "course": course,
                    "timestart": int(datetime.now(VIETNAM_TZ).timestamp()),
                    "timeduration": 3600,
                })
        except Exception as e:
            logging.debug(f"Không thể cào HTML upcoming: {e}")

        return [self._normalize_event(e) for e in events]

    def _fetch_via_ical(self):
        """Hỗ trợ dự phòng tải từ URL iCal nếu được cung cấp."""
        logging.info(f"Đang tải dữ liệu iCal từ: {self.ical_url}")
        res = requests.get(self.ical_url, timeout=30)
        res.raise_for_status()

        cal = Calendar.from_ical(res.content)
        events = []

        for component in cal.walk("VEVENT"):
            uid = str(component.get("uid", ""))
            summary = str(component.get("summary", "Sự kiện Moodle"))
            description = str(component.get("description", ""))
            location = str(component.get("location", ""))

            dtstart = component.get("dtstart")
            dtend = component.get("dtend")

            start_iso = dtstart.dt.isoformat() if dtstart else None
            end_iso = dtend.dt.isoformat() if dtend else start_iso

            events.append({
                "uid": uid,
                "summary": summary,
                "description": description,
                "location": location,
                "start": start_iso,
                "end": end_iso,
            })

        return events

    def _fetch_via_ws_activities(self, existing_events: list = None, course_hints: list = None) -> list:
        """
        Quét sâu bài tập & trắc nghiệm qua Moodle WebService REST API:
        Gọi mod_assign_get_assignments và mod_quiz_get_quizzes_by_courses để phát hiện tất cả
        deadline của các môn học đang tham gia (kể cả bài tập không đưa lên Lịch Moodle).
        """
        if not self.wstoken:
            return []

        logging.info("🔎 [Deep Scan WS] Đang quét bài tập & trắc nghiệm toàn bộ môn học qua WebService...")
        now = datetime.now(VIETNAM_TZ)
        min_ts = int((now - timedelta(days=14)).timestamp())

        known_cmids = set()
        known_summaries = set()
        if existing_events:
            for e in existing_events:
                desc = e.get("description", "")
                url_val = e.get("url", "")
                m = re.search(r"[?&]id=(\d+)", f"{desc} {url_val}")
                if m:
                    known_cmids.add(m.group(1))
                c_sum = clean_event_name(e.get("raw_summary") or e.get("summary") or "").lower()
                if c_sum:
                    known_summaries.add(c_sum)

        site_slug = "moodle"
        if "thnn" in self.base_url:
            site_slug = "thnn"
        elif "courses" not in self.base_url:
            m = re.search(r"https?://([^/]+)", self.base_url)
            if m:
                site_slug = m.group(1).replace(".", "_")

        extra_events = []

        # 1. Quét assignments qua mod_assign_get_assignments
        try:
            r_assign = requests.get(
                f"{self.base_url}/webservice/rest/server.php",
                params={
                    "wstoken": self.wstoken,
                    "wsfunction": "mod_assign_get_assignments",
                    "moodlewsrestformat": "json",
                },
                timeout=20,
            )
            for c in r_assign.json().get("courses", []):
                cname = c.get("fullname", "")
                for a in c.get("assignments", []):
                    due = a.get("duedate", 0)
                    if not due or due < min_ts:
                        continue
                    cmid = str(a.get("cmid") or a.get("id"))
                    clean_title = clean_event_name(a.get("name", ""))
                    if cmid in known_cmids or clean_title.lower() in known_summaries:
                        continue

                    url = f"{self.base_url}/mod/assign/view.php?id={cmid}"
                    fake_evt = {
                        "instance": a.get("id"),
                        "modulename": "assign",
                        "course": {"id": c.get("id")},
                        "url": url,
                        "name": clean_title,
                        "eventtype": "close",
                    }
                    status, detail = self.status_checker.check_event_status(fake_evt)
                    due_dt = datetime.fromtimestamp(due, tz=VIETNAM_TZ)
                    due_str_vn = format_datetime_vn(due)
                    status_text = detail or ("Đã nộp bài" if status == "completed" else "Chưa nộp bài")
                    full_desc = (
                        f"📚 Môn học: {cname}\n"
                        f"📌 Bài tập: {clean_title}\n"
                        f"⏰ Hạn nộp: {due_str_vn}\n"
                        f"📊 Trạng thái: {status_text}\n"
                        f"🔗 Link nộp bài: {url}"
                    )
                    start_dt = due_dt - timedelta(hours=1)
                    extra_events.append({
                        "uid": f"uth_{site_slug}_assign_{cmid}",
                        "summary": f"[CLOSES] {clean_title}",
                        "raw_summary": f"[CLOSES] {clean_title}",
                        "status": status,
                        "status_detail": status_text,
                        "description": full_desc,
                        "location": cname,
                        "start": start_dt.isoformat(),
                        "end": due_dt.isoformat(),
                        "url": url,
                    })
                    known_cmids.add(cmid)
                    known_summaries.add(clean_title.lower())
        except Exception as e:
            logging.debug(f"Lỗi quét WS assignments: {e}")

        # 2. Quét quizzes qua mod_quiz_get_quizzes_by_courses
        try:
            r_quiz = requests.get(
                f"{self.base_url}/webservice/rest/server.php",
                params={
                    "wstoken": self.wstoken,
                    "wsfunction": "mod_quiz_get_quizzes_by_courses",
                    "moodlewsrestformat": "json",
                },
                timeout=20,
            )
            for q in r_quiz.json().get("quizzes", []):
                timeclose = q.get("timeclose", 0)
                if not timeclose or timeclose < min_ts:
                    continue
                cmid = str(q.get("coursemodule") or q.get("id"))
                clean_title = clean_event_name(q.get("name", ""))
                if cmid in known_cmids or clean_title.lower() in known_summaries:
                    continue

                url = f"{self.base_url}/mod/quiz/view.php?id={cmid}"
                fake_evt = {
                    "instance": q.get("id"),
                    "modulename": "quiz",
                    "course": {"id": q.get("course")},
                    "url": url,
                    "name": clean_title,
                    "eventtype": "close",
                }
                status, detail = self.status_checker.check_event_status(fake_evt)
                due_dt = datetime.fromtimestamp(timeclose, tz=VIETNAM_TZ)
                due_str_vn = format_datetime_vn(timeclose)
                status_text = detail or ("Đã hoàn thành" if status == "completed" else "Chưa làm")
                full_desc = (
                    f"📚 Khóa học ID: {q.get('course')}\n"
                    f"📝 Bài trắc nghiệm: {clean_title}\n"
                    f"⏰ Hạn chót: {due_str_vn}\n"
                    f"📊 Trạng thái: {status_text}\n"
                    f"🔗 Link làm bài: {url}"
                )
                start_dt = due_dt - timedelta(hours=1)
                extra_events.append({
                    "uid": f"uth_{site_slug}_quiz_{cmid}",
                    "summary": f"[CLOSES] {clean_title}",
                    "raw_summary": f"[CLOSES] {clean_title}",
                    "status": status,
                    "status_detail": status_text,
                    "description": full_desc,
                    "location": f"Môn học ID {q.get('course')}",
                    "start": start_dt.isoformat(),
                    "end": due_dt.isoformat(),
                    "url": url,
                })
                known_cmids.add(cmid)
                known_summaries.add(clean_title.lower())
        except Exception as e:
            logging.debug(f"Lỗi quét WS quizzes: {e}")

        return extra_events

    def _fetch_via_enrolled_courses(self, existing_events: list = None, course_hints: list = None) -> list:
        """
        Quét sâu danh sách môn học (Deep Course Scanner):
        Tự động duyệt qua tất cả môn học đang tham gia để phát hiện các bài tập (Assignments & Quizzes)
        có hạn nộp nhưng giảng viên KHÔNG đưa lên Lịch Moodle.
        """
        if not self.session.cookies.get("MoodleSession"):
            return []

        logging.info(f"🔎 [Deep Scan Web] Đang quét sâu các môn học trên {self.base_url}...")
        now = datetime.now(VIETNAM_TZ)
        min_date = now - timedelta(days=14)

        # Tạo tập hợp các ID bài tập đã có từ Lịch để tránh trùng lặp
        known_cmids = set()
        known_summaries = set()
        if existing_events:
            for e in existing_events:
                desc = e.get("description", "")
                url_val = e.get("url", "")
                m = re.search(r"[?&]id=(\d+)", f"{desc} {url_val}")
                if m:
                    known_cmids.add(m.group(1))
                c_sum = clean_event_name(e.get("raw_summary") or e.get("summary") or "").lower()
                if c_sum:
                    known_summaries.add(c_sum)

        # 1. Lấy danh sách khóa học qua AJAX
        courses = []
        try:
            payload = [{
                "index": 0,
                "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                "args": {"classification": "all", "limit": 0, "offset": 0, "sort": "fullname"}
            }]
            res_ajax = self.session.post(
                f"{self.base_url}/lib/ajax/service.php",
                params={"sesskey": self.sesskey or ""},
                json=payload,
                timeout=20
            )
            data_list = res_ajax.json()
            if data_list and not data_list[0].get("error"):
                courses = data_list[0].get("data", {}).get("courses", [])
        except Exception as e:
            logging.debug(f"Không lấy được khóa học qua AJAX: {e}")

        # Fallback cào từ my/courses.php nếu AJAX không có dữ liệu
        if not courses:
            try:
                r_my = self.session.get(f"{self.base_url}/my/courses.php", timeout=20)
                c_links = set(re.findall(r'href="[^"]*course/view\.php\?id=(\d+)"[^>]*>(.*?)</a>', r_my.text))
                for cid, ctitle in c_links:
                    clean_ctitle = re.sub(r"<[^>]+>", "", ctitle).strip()
                    if clean_ctitle and cid not in ["0", "1"]:
                        courses.append({"id": int(cid), "fullname": clean_ctitle})
            except Exception as e:
                logging.debug(f"Không cào được khóa học từ my/courses.php: {e}")

        # Bổ sung các môn học từ Thời khóa biểu Portal (course_hints)
        existing_cids = {str(c.get("id")) for c in courses if c.get("id")}
        if course_hints:
            for ch in course_hints:
                c_link = ch.get("link", "")
                if self.base_url in c_link:
                    try:
                        r_hint = self.session.get(c_link, timeout=10, allow_redirects=True)
                        m_id = re.search(r'[?&]id=(\d+)', r_hint.url)
                        if m_id:
                            hint_cid = int(m_id.group(1))
                            if str(hint_cid) not in existing_cids and str(hint_cid) not in ["0", "1"]:
                                existing_cids.add(str(hint_cid))
                                courses.append({"id": hint_cid, "fullname": ch.get("name") or f"Môn học {ch.get('code')}"})
                    except Exception as e:
                        logging.debug(f"Không thể mở link môn học hint {c_link}: {e}")

        if not courses:
            return []

        site_slug = "moodle"
        if "thnn" in self.base_url:
            site_slug = "thnn"
        elif "courses" not in self.base_url:
            m = re.search(r"https?://([^/]+)", self.base_url)
            if m:
                site_slug = m.group(1).replace(".", "_")

        extra_events = []

        for c in courses:
            cid = c.get("id")
            cname = c.get("fullname", f"Khóa học {cid}")

            # --- A. Quét mod/assign/index.php ---
            try:
                r_assign = self.session.get(f"{self.base_url}/mod/assign/index.php?id={cid}", timeout=15)
                if r_assign.status_code == 200:
                    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r_assign.text, re.S)
                    for row in rows:
                        link_m = re.search(r'href="([^"]*view\.php\?id=(\d+))"[^>]*>(.*?)</a>', row)
                        if not link_m:
                            continue
                        rel_url, cmid, title = link_m.groups()
                        url = urljoin(f"{self.base_url}/mod/assign/", rel_url)
                        clean_title = clean_event_name(re.sub(r"<[^>]+>", "", title).strip())

                        if cmid in known_cmids or clean_title.lower() in known_summaries:
                            continue

                        cells = [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
                        due_dt = None

                        for cell in cells:
                            dt = parse_moodle_date(cell)
                            if dt:
                                due_dt = dt
                                break

                        # Kiểm tra xem bài đã nộp hay chưa
                        mock_evt = {
                            "url": url,
                            "modulename": "assign",
                            "name": clean_title,
                            "instance": cmid,
                            "eventtype": "close"
                        }
                        st, detail = self.status_checker.check_event_status(mock_evt)
                        sub_status = st if st in ["completed", "pending"] else "pending"
                        status_desc = detail if detail else ("Đã nộp bài" if sub_status == "completed" else "Chưa nộp bài")

                        # Chỉ lấy bài có hạn nộp và không quá cũ (> 14 ngày trước)
                        if due_dt and due_dt >= min_date:
                            uid = f"uth_{site_slug}_assign_{cmid}"
                            due_str_vn = format_datetime_vn(due_dt.timestamp())
                            full_desc = (
                                f"📚 Môn học: {cname}\n"
                                f"📌 Bài tập: {clean_title}\n"
                                f"⏰ Hạn nộp: {due_str_vn}\n"
                                f"📊 Trạng thái: {status_desc}\n"
                                f"🔗 Link nộp bài: {url}"
                            )
                            start_dt = due_dt - timedelta(hours=1)
                            end_dt = due_dt
                            extra_events.append({
                                "uid": uid,
                                "summary": f"[CLOSES] {clean_title}",
                                "raw_summary": f"[CLOSES] {clean_title}",
                                "status": sub_status,
                                "status_detail": status_desc,
                                "description": full_desc,
                                "location": cname,
                                "start": start_dt.isoformat(),
                                "end": end_dt.isoformat(),
                                "url": url,
                            })
                            known_cmids.add(cmid)
                            known_summaries.add(clean_title.lower())
            except Exception as e:
                logging.debug(f"Lỗi quét assign khóa học {cid}: {e}")

            # --- B. Quét mod/quiz/index.php ---
            try:
                r_quiz = self.session.get(f"{self.base_url}/mod/quiz/index.php?id={cid}", timeout=15)
                if r_quiz.status_code == 200:
                    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r_quiz.text, re.S)
                    for row in rows:
                        link_m = re.search(r'href="([^"]*view\.php\?id=(\d+))"[^>]*>(.*?)</a>', row)
                        if not link_m:
                            continue
                        rel_url, cmid, title = link_m.groups()
                        url = urljoin(f"{self.base_url}/mod/quiz/", rel_url)
                        clean_title = clean_event_name(re.sub(r"<[^>]+>", "", title).strip())

                        if cmid in known_cmids or clean_title.lower() in known_summaries:
                            continue

                        cells = [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
                        due_dt = None

                        for cell in cells:
                            dt = parse_moodle_date(cell)
                            if dt:
                                due_dt = dt
                                break

                        mock_evt = {
                            "url": url,
                            "modulename": "quiz",
                            "name": clean_title,
                            "instance": cmid,
                            "eventtype": "close"
                        }
                        st, detail = self.status_checker.check_event_status(mock_evt)
                        sub_status = st if st in ["completed", "pending"] else "pending"
                        status_desc = detail if detail else ("Đã hoàn thành" if sub_status == "completed" else "Chưa làm")

                        if due_dt and due_dt >= min_date:
                            uid = f"uth_{site_slug}_quiz_{cmid}"
                            due_str_vn = format_datetime_vn(due_dt.timestamp())
                            full_desc = (
                                f"📚 Môn học: {cname}\n"
                                f"📝 Bài trắc nghiệm: {clean_title}\n"
                                f"⏰ Hạn chót: {due_str_vn}\n"
                                f"📊 Trạng thái: {status_desc}\n"
                                f"🔗 Link làm bài: {url}"
                            )
                            start_dt = due_dt - timedelta(hours=1)
                            end_dt = due_dt
                            extra_events.append({
                                "uid": uid,
                                "summary": f"[CLOSES] {clean_title}",
                                "raw_summary": f"[CLOSES] {clean_title}",
                                "status": sub_status,
                                "status_detail": status_desc,
                                "description": full_desc,
                                "location": cname,
                                "start": start_dt.isoformat(),
                                "end": end_dt.isoformat(),
                                "url": url,
                            })
                            known_cmids.add(cmid)
                            known_summaries.add(clean_title.lower())
            except Exception as e:
                logging.debug(f"Lỗi quét quiz khóa học {cid}: {e}")

            # --- C. Quét trực tiếp giao diện khóa học course/view.php?id={cid} ---
            try:
                r_c = self.session.get(f"{self.base_url}/course/view.php?id={cid}", timeout=15)
                if r_c.status_code == 200:
                    modules = re.findall(r'<li[^>]*id="module-(\d+)"[^>]*>(.*?)</li>', r_c.text, re.S)
                    for cmid, mod_html in modules:
                        link_m = re.search(r'href="([^"]*mod/(quiz|assign)/view\.php\?id=\d+)"[^>]*>(.*?)</a>', mod_html)
                        if not link_m:
                            continue
                        url, mod_type, raw_title = link_m.groups()
                        clean_raw = re.sub(r'<span[^>]*class="[^"]*accesshide[^"]*"[^>]*>.*?</span>', '', raw_title, flags=re.S)
                        clean_title = clean_event_name(re.sub(r'<[^>]+>', '', clean_raw).strip())

                        if cmid in known_cmids or clean_title.lower() in known_summaries:
                            continue

                        # Trích xuất ngày giờ từ activity-dates
                        date_items = re.findall(r'<strong>(Opened|Closed|Due|Mở đề|Đóng đề|Hạn nộp|Hạn chót):\s*</strong>\s*([^<]+)', mod_html, re.I)
                        due_dt = None
                        for k, v in date_items:
                            k_lower = k.lower()
                            if any(w in k_lower for w in ["close", "due", "đóng", "hạn"]):
                                dt = parse_moodle_date(v.strip())
                                if dt:
                                    due_dt = dt
                                    break

                        if due_dt and due_dt >= min_date:
                            is_done = any(w in mod_html for w in ["Done", "is marked as done", "Đã hoàn thành"])
                            sub_status = "completed" if is_done else "pending"

                            mock_evt = {
                                "url": url,
                                "modulename": mod_type,
                                "name": clean_title,
                                "instance": cmid
                            }
                            st, detail = self.status_checker.check_event_status(mock_evt)
                            if st in ["completed", "pending"]:
                                sub_status = st
                                status_desc = detail
                            else:
                                status_desc = "Đã hoàn thành" if sub_status == "completed" else "Chưa làm"

                            uid = f"uth_{site_slug}_{mod_type}_{cmid}"
                            due_str_vn = format_datetime_vn(due_dt.timestamp())
                            full_desc = (
                                f"📚 Môn học: {cname}\n"
                                f"📌 Hoạt động: {clean_title}\n"
                                f"⏰ Hạn nộp/Hạn chót: {due_str_vn}\n"
                                f"📊 Trạng thái: {status_desc}\n"
                                f"🔗 Link: {url}"
                            )
                            start_dt = due_dt - timedelta(hours=1)
                            end_dt = due_dt
                            extra_events.append({
                                "uid": uid,
                                "summary": f"[CLOSES] {clean_title}",
                                "raw_summary": f"[CLOSES] {clean_title}",
                                "status": sub_status,
                                "status_detail": status_desc,
                                "description": full_desc,
                                "location": cname,
                                "start": start_dt.isoformat(),
                                "end": end_dt.isoformat(),
                                "url": url,
                            })
                            known_cmids.add(cmid)
                            known_summaries.add(clean_title.lower())
            except Exception as e:
                logging.debug(f"Lỗi quét course view {cid}: {e}")

        return extra_events

    def fetch_events(self, course_hints: list = None):
        """Quét và thu thập toàn bộ sự kiện theo các chiến lược ưu tiên."""
        if not self._ensure_session() and not self.ical_url:
            raise RuntimeError("Không thể đăng nhập hoặc khởi tạo phiên Moodle UTH. Vui lòng kiểm tra tài khoản/mật khẩu hoặc cookie.")

        events = []

        # Chiến lược 1: Dùng wstoken REST API
        if self.wstoken:
            try:
                events = self._fetch_via_wstoken()
            except Exception as e:
                logging.warning(f"Thử REST API thất bại: {e}")

        # Chiến lược 2: Dùng AJAX Service qua session
        if not events and self.session.cookies.get("MoodleSession"):
            try:
                events = self._fetch_via_ajax()
            except Exception as e:
                logging.warning(f"Thử AJAX Service thất bại: {e}")

        # Chiến lược 3: Cào trực tiếp giao diện HTML
        if not events and self.session.cookies.get("MoodleSession"):
            try:
                events = self._fetch_via_html_view()
            except Exception as e:
                logging.warning(f"Thử cào HTML thất bại: {e}")

        # Chiến lược 4: iCal URL nếu có
        if not events and self.ical_url:
            events = self._fetch_via_ical()

        # Chiến lược 5 (ĐẶC BIỆT): Quét sâu toàn bộ khóa học (Deep Course Scanner)
        # Giúp tự động phát hiện bài tập có deadline nhưng giảng viên KHÔNG đưa lên Lịch Moodle
        extra_events = []
        if self.wstoken:
            try:
                ws_extras = self._fetch_via_ws_activities(existing_events=events, course_hints=course_hints)
                if ws_extras:
                    extra_events.extend(ws_extras)
            except Exception as e:
                logging.debug(f"Quét sâu môn học qua WS không thành công: {e}")

        if self.session.cookies.get("MoodleSession"):
            try:
                enrol_extras = self._fetch_via_enrolled_courses(existing_events=events + extra_events, course_hints=course_hints)
                if enrol_extras:
                    extra_events.extend(enrol_extras)
            except Exception as e:
                logging.debug(f"Quét sâu môn học qua session không thành công: {e}")

        if extra_events:
            logging.info(f"🔎 Quét sâu môn học: Phát hiện thêm {len(extra_events)} bài tập/deadline không có trên Lịch Moodle!")
            existing_uids = {e["uid"] for e in events}
            for ee in extra_events:
                if ee["uid"] not in existing_uids:
                    events.append(ee)
                    existing_uids.add(ee["uid"])

        logging.info(f"🎉 Đã quét được tổng cộng {len(events)} sự kiện/deadline từ UTH Moodle ({self.base_url})!")
        return events
