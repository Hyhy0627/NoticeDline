import sys
import os
import re
import html
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from icalendar import Calendar

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

VIETNAM_TZ = timezone(timedelta(hours=7))

class MoodleFetcher:
    """
    Module thu thập lịch & deadline từ UTH Moodle (courses.ut.edu.vn).
    Hỗ trợ:
    1. Đăng nhập tự động bằng Tài khoản (MSSV) & Mật khẩu Moodle.
    2. Đăng nhập qua Cookie MoodleSession.
    3. Đăng nhập qua Moodle WebService Token.
    4. Dự phòng iCal URL (nếu có).
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
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        })

        if self.session_cookie:
            self._set_cookie(self.session_cookie)

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
                    logging.error("❌ Cookie MoodleSession đã hết hạn hoặc không hợp lệ. Vui lòng đăng nhập lại.")
                    return False
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

        # Nếu là sự kiện gắn liền với môn học (có courseid > 1)
        course = evt.get("course")
        cid = course.get("id") if isinstance(course, dict) else evt.get("courseid")
        if cid and str(cid) not in ["0", "1"]:
            return "course" in self.allowed_event_types

        # Nếu là sự kiện cá nhân của người dùng (có userid)
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

    def check_event_status(self, event_dict: dict):
        """
        Kiểm tra xem sinh viên đã nộp bài / làm bài hay chưa.
        Returns:
            status: 'completed' (đã làm) | 'pending' (chưa làm) | 'info' (thông tin chung)
            detail: chi tiết trạng thái (điểm, nộp bài, ...)
        """
        course = event_dict.get("course")
        course_id = course.get("id") if isinstance(course, dict) else event_dict.get("courseid")
        modname = (event_dict.get("modulename") or "").lower()
        component = (event_dict.get("component") or "").lower()
        event_type = (event_dict.get("eventtype") or "").lower()
        inst_or_cmid = event_dict.get("instance")
        event_name = event_dict.get("name", "")

        # 1. Sự kiện mở đề (opens), chỉ mang tính thông tin thời gian bắt đầu
        if event_type == "open" or bool(re.search(r'\b(?:opens|mở đề)\b', event_name, re.IGNORECASE)):
            return "info", "Mở đề / Bắt đầu"

        # Nếu không có wstoken (ví dụ trang thnn.ut.edu.vn tắt WebService), kiểm tra qua action & session
        if not self.wstoken:
            action = event_dict.get("action")
            if isinstance(action, dict) and action.get("actionable"):
                return "pending", "Chưa nộp bài / Chưa làm"

            url = event_dict.get("url") or (action.get("url") if isinstance(action, dict) else "")
            if url and self.session.cookies.get("MoodleSession") and (modname in ["assign", "quiz"] or "mod_" in component):
                try:
                    if url not in self._checked_submissions:
                        res = self.session.get(url, timeout=10)
                        html_text = res.text
                        if "submissionstatussubmitted" in html_text or "Submitted for grading" in html_text or "Đã nộp để chấm điểm" in html_text:
                            self._checked_submissions[url] = ("completed", "Đã nộp bài")
                        elif "No submissions have been made yet" in html_text or "Chưa có bài nộp nào" in html_text:
                            self._checked_submissions[url] = ("pending", "Chưa nộp bài")
                        elif "Thực hiện lại đề thi" in html_text or "Re-attempt quiz" in html_text:
                            self._checked_submissions[url] = ("completed", "Đã hoàn thành")
                        elif "Attempt quiz now" in html_text or "Thực hiện đề thi ngay" in html_text:
                            self._checked_submissions[url] = ("pending", "Chưa làm")
                        else:
                            self._checked_submissions[url] = ("info", "")
                    return self._checked_submissions[url]
                except Exception as e:
                    logging.debug(f"Không thể kiểm tra HTML {url}: {e}")

            return "info", ""

        userid = self._get_userid()
        if not userid:
            return "info", ""

        # 2. Xử lý Trắc nghiệm (Quiz)
        if modname == "quiz" or "mod_quiz" in component:
            if course_id:
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

                    quiz_item = None
                    for q in self._course_quizzes[course_id]:
                        if q.get("coursemodule") == inst_or_cmid or q.get("id") == inst_or_cmid:
                            quiz_item = q
                            break

                    if quiz_item:
                        qid = quiz_item.get("id")
                        if qid not in self._checked_attempts:
                            att_data = requests.get(
                                f"{self.base_url}/webservice/rest/server.php",
                                params={
                                    "wstoken": self.wstoken,
                                    "wsfunction": "mod_quiz_get_user_attempts",
                                    "moodlewsrestformat": "json",
                                    "quizid": qid,
                                    "userid": userid,
                                    "status": "all",
                                },
                                timeout=15,
                            ).json()
                            self._checked_attempts[qid] = att_data.get("attempts", [])

                        attempts = self._checked_attempts[qid]
                        finished_attempts = [a for a in attempts if a.get("state") == "finished"]
                        attempts_done = len(finished_attempts)

                        # Kiểm tra giới hạn số lần làm bài theo chuẩn Moodle API:
                        # attempts: 0 = Không giới hạn, > 0 = Số lần tối đa cho phép
                        max_attempts = quiz_item.get("attempts", 0)
                        grademethod = quiz_item.get("grademethod", 1)

                        if finished_attempts:
                            # Lấy điểm (nếu phương thức chấm là Lần cao nhất thì lấy điểm cao nhất)
                            grades = [a.get("sumgrades") for a in finished_attempts if a.get("sumgrades") is not None]
                            if grademethod == 1 and grades:
                                best_grade = max(grades)
                            else:
                                best_grade = finished_attempts[-1].get("sumgrades")

                            grade_str = f" - Điểm: {best_grade}" if best_grade is not None else ""

                            if max_attempts > 0:
                                rem = max(0, max_attempts - attempts_done)
                                rem_str = " - Đã hết lượt" if rem == 0 else f" - Còn {rem} lượt"
                                limit_str = f" [Đã làm: {attempts_done}/{max_attempts} lần{rem_str}]"
                            else:
                                limit_str = f" [Đã làm: {attempts_done} lần - Không giới hạn số lần làm]"

                            return "completed", f"Đã hoàn thành{grade_str}{limit_str}"
                        else:
                            if max_attempts > 0:
                                limit_str = f" [Giới hạn: Tối đa {max_attempts} lần]"
                            else:
                                limit_str = " [Không giới hạn số lần làm]"
                            return "pending", f"Chưa làm{limit_str}"
                except Exception as e:
                    logging.debug(f"Lỗi kiểm tra Quiz: {e}")

        # 3. Xử lý Bài tập nộp (Assignment)
        if modname == "assign" or "mod_assign" in component:
            if course_id:
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

                    assign_item = None
                    for a in self._course_assigns[course_id]:
                        if a.get("cmid") == inst_or_cmid or a.get("id") == inst_or_cmid:
                            assign_item = a
                            break

                    if assign_item:
                        aid = assign_item.get("id")
                        if aid not in self._checked_submissions:
                            sub_data = requests.get(
                                f"{self.base_url}/webservice/rest/server.php",
                                params={
                                    "wstoken": self.wstoken,
                                    "wsfunction": "mod_assign_get_submission_status",
                                    "moodlewsrestformat": "json",
                                    "assignid": aid,
                                    "userid": userid,
                                },
                                timeout=15,
                            ).json()
                            lastattempt = sub_data.get("lastattempt", {})
                            sub_status = lastattempt.get("submission", {}).get("status")
                            self._checked_submissions[aid] = sub_status

                        sub_status = self._checked_submissions[aid]
                        if sub_status == "submitted":
                            return "completed", "Đã nộp bài"
                        else:
                            return "pending", "Chưa nộp bài"
                except Exception as e:
                    logging.debug(f"Lỗi kiểm tra Assignment: {e}")

        # 4. Kiểm tra Completion Tracking của Moodle nếu có
        if course_id and inst_or_cmid:
            try:
                if course_id not in self._course_completions:
                    comp_data = requests.get(
                        f"{self.base_url}/webservice/rest/server.php",
                        params={
                            "wstoken": self.wstoken,
                            "wsfunction": "core_completion_get_activities_completion_status",
                            "moodlewsrestformat": "json",
                            "courseid": course_id,
                            "userid": userid,
                        },
                        timeout=15,
                    ).json()
                    status_map = {}
                    for s in comp_data.get("statuses", []):
                        status_map[s.get("cmid")] = s.get("state", 0)
                    self._course_completions[course_id] = status_map

                comp_map = self._course_completions[course_id]
                if inst_or_cmid in comp_map:
                    state = comp_map[inst_or_cmid]
                    if state in [1, 2, 3]:  # COMPLETE, COMPLETE_PASS, COMPLETE_FAIL
                        return "completed", "Đã hoàn thành"
                    elif state == 0:
                        return "pending", "Chưa hoàn thành"
            except Exception as e:
                logging.debug(f"Lỗi kiểm tra Completion: {e}")

        return "info", ""

    @staticmethod
    def _clean_event_name(raw_name: str) -> str:
        """Làm sạch các hậu tố và tiền tố thừa của Moodle (opens, closes, due, v.v.)."""
        name = html.unescape(raw_name or "").strip()
        # Xóa hậu tố
        name = re.sub(
            r'\s+(?:opens|open|closes|close|is due|due|kết thúc|tới hạn|hết hạn|đóng)$',
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

    @staticmethod
    def _format_datetime_vn(ts) -> str:
        """Định dạng timestamp sang chuỗi ngày giờ tiếng Việt dễ đọc."""
        if not ts or int(ts) <= 0:
            return ""
        dt = datetime.fromtimestamp(int(ts), tz=VIETNAM_TZ)
        days_vi = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
        day_name = days_vi[dt.weekday()]
        return f"{dt.strftime('%H:%M')} - {day_name}, {dt.strftime('%d/%m/%Y')}"

    def _index_raw_events(self, raw_events: list):
        """Quét trước danh sách sự kiện để ghép cặp thời gian Mở (Opens) và Đóng (Closes)."""
        if not hasattr(self, "_calendar_activity_times"):
            self._calendar_activity_times = {}

        for evt in raw_events:
            clean_name = self._clean_event_name(evt.get("name", ""))
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

            # Hỗ trợ tra cứu đa cấp: (modname, inst) -> (course_id, clean_name) -> clean_name
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

        # 1. Tra cứu từ API Quiz nếu có
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
                    if str(q.get("coursemodule")) == inst or str(q.get("id")) == inst or self._clean_event_name(q.get("name", "")).lower() == clean_name.lower():
                        open_ts = q.get("timeopen")
                        close_ts = q.get("timeclose")
                        break
            except Exception as e:
                logging.debug(f"Lỗi tra cứu thời gian Quiz: {e}")

        # 2. Tra cứu từ API Assignment nếu có
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
                    if str(a.get("cmid")) == inst or str(a.get("id")) == inst or self._clean_event_name(a.get("name", "")).lower() == clean_name.lower():
                        open_ts = a.get("allowsubmissionsfromdate")
                        close_ts = a.get("duedate")
                        break
            except Exception as e:
                logging.debug(f"Lỗi tra cứu thời gian Assignment: {e}")

        # 3. Tra cứu từ cache ghép cặp raw calendar events (theo các key ưu tiên)
        cache_map = getattr(self, "_calendar_activity_times", {})
        for lookup_key in [f"{modname}_{inst}", f"{course_id}_{clean_name.lower()}", clean_name.lower()]:
            if lookup_key in cache_map:
                if not open_ts and cache_map[lookup_key].get("open"):
                    open_ts = cache_map[lookup_key]["open"]
                if not close_ts and cache_map[lookup_key].get("close"):
                    close_ts = cache_map[lookup_key]["close"]
                if open_ts and close_ts:
                    break

        # 4. Fallback dựa vào chính sự kiện hiện tại
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

        # Phân loại sự kiện mở đề và sự kiện hạn chót / đóng đề
        is_open_event = (
            event_type == "open"
            or bool(re.search(r'\b(?:opens|open)\b', raw_name, re.IGNORECASE))
        )
        is_deadline_event = (
            event_type in ["close", "due"]
            or bool(re.search(r'\b(?:closes|close|is due|due|kết thúc|tới hạn|hết hạn|đóng)\b', raw_name, re.IGNORECASE))
        )

        clean_name = self._clean_event_name(raw_name)

        # Đặt tiêu đề rõ ràng cho từng loại sự kiện: [OPEN] hoặc [CLOSES]
        if is_open_event:
            summary = f"[OPEN] {clean_name}"
        elif is_deadline_event:
            summary = f"[CLOSES] {clean_name}"
        else:
            summary = clean_name

        # Kiểm tra trạng thái làm bài (đã làm / chưa làm)
        status, status_detail = self.check_event_status(event_dict)

        # Làm sạch phần mô tả HTML
        raw_desc = event_dict.get("description", "") or ""
        clean_desc = html.unescape(re.sub(r"<br\s*/?>", "\n", raw_desc))
        clean_desc = re.sub(r"<[^>]+>", "", clean_desc).strip()

        # Khóa học / Môn học
        course = event_dict.get("course")
        course_name = ""
        if isinstance(course, dict):
            course_name = course.get("fullname") or course.get("shortname") or ""
        elif isinstance(course, str):
            course_name = course

        # Link trực tiếp tới bài nộp/sự kiện
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

        # Thêm thông tin thời gian mở (Opens) và đóng (Closes) vào phần ghi chú
        open_ts, close_ts = self._get_activity_times(event_dict, clean_name)
        open_str = self._format_datetime_vn(open_ts)
        close_str = self._format_datetime_vn(close_ts)

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

        # Xử lý thời gian chuẩn xác
        timestart = event_dict.get("timestart")
        timeduration = event_dict.get("timeduration", 0) or 0

        if timestart:
            ts_dt = datetime.fromtimestamp(int(timestart), tz=VIETNAM_TZ)
            if timeduration and int(timeduration) > 0:
                start_dt = ts_dt
                end_dt = datetime.fromtimestamp(int(timestart) + int(timeduration), tz=VIETNAM_TZ)
            elif is_deadline_event:
                # Nếu là hạn chót (closes / due / kết thúc): Thời điểm kết thúc PHẢI LÀ deadline
                # Đặt khối 1 tiếng trước deadline trên Google Calendar để chuông nhắc đúng thời điểm
                end_dt = ts_dt
                start_dt = end_dt - timedelta(hours=1)
            else:
                # Nếu là sự kiện mở đề hoặc sự kiện mốc thời gian: Bắt đầu từ timestart, kéo dài 1 tiếng
                start_dt = ts_dt
                end_dt = start_dt + timedelta(hours=1)
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()
        else:
            start_iso = event_dict.get("start")
            end_iso = event_dict.get("end", start_iso)

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
        # 1. Quét lịch theo tháng
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

        # 2. Quét các sự kiện sắp tới (upcoming events & deadlines)
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

        # Index ghép cặp thời gian Mở và Đóng trước khi chuẩn hóa
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

                # Trường hợp monthly view
                if "weeks" in data:
                    for week in data.get("weeks", []):
                        for day in week.get("days", []):
                            for evt in day.get("events", []):
                                if self._is_allowed_event_type(evt):
                                    raw_events.append(evt)

                # Trường hợp upcoming / action events
                if "events" in data:
                    for evt in data.get("events", []):
                        if self._is_allowed_event_type(evt):
                            raw_events.append(evt)

            # Index ghép cặp thời gian Mở và Đóng trước khi chuẩn hóa
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
            # Tìm các khối sự kiện trên trang upcoming
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

    def fetch_events(self):
        """Quét và thu thập toàn bộ sự kiện theo các chiến lược ưu tiên."""
        # 1. Đảm bảo phiên đăng nhập hoặc lấy wstoken
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

        logging.info(f"🎉 Đã quét được tổng cộng {len(events)} sự kiện/deadline từ UTH Moodle!")
        return events

if __name__ == "__main__":
    print("Module moodle_fetcher sẵn sàng.")
