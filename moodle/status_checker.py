"""
Module moodle.status_checker:
Bộ não phân tích và kiểm tra trạng thái hoàn thành của từng bài tập / bài kiểm tra trắc nghiệm:
1. Đối với Moodle có WebService REST API (như courses.ut.edu.vn):
   - mod_quiz_get_user_attempts: Kiểm tra số lần làm bài trắc nghiệm, điểm số, giới hạn số lần cho phép.
   - mod_assign_get_submission_status: Kiểm tra trạng thái nộp bài tập file.
   - core_completion_get_activities_completion_status: Kiểm tra thanh tiến độ môn học.
2. Đối với Moodle dùng phiên Web/Cookie HTML (như thnn.ut.edu.vn):
   - Phân tích trực tiếp mã nguồn HTML giao diện bài học.
   - Nhận diện các trạng thái: Đã nộp bài, Chưa nộp bài, Đã hoàn thành (Finished), Các lần làm bài (Your attempts).
   - Tự động trích xuất điểm số hiển thị (Ví dụ: '8.60/10.00').
3. Xử lý đồng bộ thông minh cho cả sự kiện Mở đề ([OPEN]) và Đóng đề ([CLOSES]):
   - Nếu bài đã làm: Cả 2 sự kiện đều gắn nhãn [ĐÃ LÀM] (màu xanh lá cây) và tắt chuông báo.
   - Nếu bài chưa làm: Sự kiện đóng đề [CLOSES] sẽ gắn nhãn [CHƯA LÀM] (màu đỏ) và bật chuông báo,
     trong khi sự kiện mở đề [OPEN] chỉ hiển thị mốc giờ thông tin bắt đầu.
"""

import re
import logging
import requests
from urllib.parse import urljoin, urlparse
from moodle.utils import clean_event_name


class StatusChecker:
    """Xử lý kiểm tra trạng thái bài tập / trắc nghiệm (đã nộp hay chưa, điểm số, hoàn thành)."""

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def check_event_status(self, event_dict: dict):
        """
        Kiểm tra xem sinh viên đã nộp bài / làm bài hay chưa.
        Returns:
            status: 'completed' (đã làm) | 'pending' (chưa làm) | 'info' (thông tin chung)
            detail: chi tiết trạng thái (điểm số, số lượt làm, thông báo trạng thái)
        """
        course = event_dict.get("course")
        course_id = course.get("id") if isinstance(course, dict) else event_dict.get("courseid")
        modname = (event_dict.get("modulename") or "").lower()
        component = (event_dict.get("component") or "").lower()
        event_type = (event_dict.get("eventtype") or "").lower()
        inst_or_cmid = event_dict.get("instance")
        event_name = event_dict.get("name", "")

        is_open = (event_type == "open" or bool(re.search(r'\b(?:opens|mở đề)\b', event_name, re.IGNORECASE)))

        # 1. Nếu không có wstoken (ví dụ trang thnn.ut.edu.vn tắt WebService), kiểm tra qua cào HTML & session
        if not self.fetcher.wstoken:
            raw_url = event_dict.get("url") or (event_dict.get("action", {}).get("url") if isinstance(event_dict.get("action"), dict) else "")
            url = ""
            if raw_url:
                full_url = urljoin(self.fetcher.base_url, raw_url)
                parsed_full = urlparse(full_url)
                parsed_base = urlparse(self.fetcher.base_url)
                if parsed_full.netloc == parsed_base.netloc:
                    url = full_url

            if url and self.fetcher.session.cookies.get("MoodleSession") and (
                modname in ["assign", "quiz"]
                or "mod_" in component
                or "/mod/quiz" in url
                or "/mod/assign" in url
            ):
                try:
                    if url not in self.fetcher._checked_submissions:
                        res = self.fetcher.session.get(url, timeout=10)
                        html_text = res.text

                        # 1.1 Kiểm tra đối với Assignment (Bài tập nộp file)
                        if "/mod/assign" in url or modname == "assign" or "mod_assign" in component:
                            if any(k in html_text for k in [
                                "submissionstatussubmitted", "Submitted for grading", "Đã nộp để chấm điểm"
                            ]):
                                self.fetcher._checked_submissions[url] = ("completed", "Đã nộp bài")
                            else:
                                self.fetcher._checked_submissions[url] = ("pending", "Chưa nộp bài")

                        # 1.2 Kiểm tra đối với Quiz (Bài trắc nghiệm)
                        elif "/mod/quiz" in url or modname == "quiz" or "mod_quiz" in component:
                            grade_str = ""
                            grade_match = re.search(
                                r'(?:Your final grade for this quiz is|Điểm số cuối cùng của bạn[^<]*?là)\s*([0-9.,/ ]+)',
                                html_text,
                                re.IGNORECASE
                            )
                            if grade_match:
                                grade_val = grade_match.group(1).strip().rstrip('.')
                                grade_str = f" - Điểm: {grade_val}"
                            else:
                                tbl_grade = re.search(
                                    r'<th[^>]*>\s*Grade\s*</th>\s*<td[^>]*>(.*?)</td>',
                                    html_text,
                                    re.IGNORECASE | re.S
                                )
                                if tbl_grade:
                                    g_clean = re.sub(r'<[^>]+>', '', tbl_grade.group(1)).strip()
                                    if g_clean:
                                        grade_str = f" - Điểm: {g_clean}"

                            is_quiz_finished = (
                                "review.php?attempt=" in html_text
                                or "Thực hiện lại đề thi" in html_text
                                or "Re-attempt quiz" in html_text
                                or "No more attempts are allowed" in html_text
                                or "Không cho phép thực hiện thêm lần nào nữa" in html_text
                                or bool(re.search(r'(?:Your final grade for this quiz is|Điểm số cuối cùng của bạn[^<]*?là)', html_text, re.IGNORECASE))
                                or bool(re.search(r'(?:Status|Trạng thái|Tình trạng)[^<]*</th\s*>\s*<td[^>]*>\s*(?:Finished|Đã kết thúc|Đã hoàn thành|Đã nộp)', html_text, re.IGNORECASE))
                                or (
                                    bool(re.search(r'(?:Finished|Đã hoàn thành|Đã kết thúc|Đã nộp|Submitted)', html_text, re.IGNORECASE))
                                    and any(k in html_text for k in [
                                        "Your attempts", "Summary of your previous attempts", "Tóm tắt các lần làm bài trước",
                                        "Các lần làm bài của bạn", "quizattemptsummary", "quizreviewsummary"
                                    ])
                                )
                            )

                            if is_quiz_finished:
                                self.fetcher._checked_submissions[url] = ("completed", f"Đã hoàn thành{grade_str}")
                            else:
                                # Nếu chưa có lần làm bài nào hoàn thành thì là Chưa làm
                                self.fetcher._checked_submissions[url] = ("pending", "Chưa làm")
                        else:
                            self.fetcher._checked_submissions[url] = ("info", "")

                    status, detail = self.fetcher._checked_submissions[url]
                    if is_open and status != "completed":
                        return "info", "Mở đề / Bắt đầu"
                    return status, detail
                except Exception as e:
                    logging.debug(f"Không thể kiểm tra HTML {url}: {e}")

            action = event_dict.get("action")
            if isinstance(action, dict) and action.get("actionable") and not is_open:
                return "pending", "Chưa nộp bài / Chưa làm"

            if is_open:
                return "info", "Mở đề / Bắt đầu"
            return "info", ""

        userid = self.fetcher._get_userid()
        if not userid:
            return ("info", "Mở đề / Bắt đầu") if is_open else ("info", "")

        # 2. Xử lý Trắc nghiệm (Quiz) qua WebService REST API
        if modname == "quiz" or "mod_quiz" in component:
            if course_id:
                try:
                    if course_id not in self.fetcher._course_quizzes:
                        q_data = requests.get(
                            f"{self.fetcher.base_url}/webservice/rest/server.php",
                            params={
                                "wstoken": self.fetcher.wstoken,
                                "wsfunction": "mod_quiz_get_quizzes_by_courses",
                                "moodlewsrestformat": "json",
                                "courseids[0]": course_id,
                            },
                            timeout=15,
                        ).json()
                        self.fetcher._course_quizzes[course_id] = q_data.get("quizzes", [])

                    quiz_item = None
                    clean_evt_name = clean_event_name(event_name).lower()
                    for q in self.fetcher._course_quizzes[course_id]:
                        if (
                            str(q.get("coursemodule")) == str(inst_or_cmid)
                            or str(q.get("id")) == str(inst_or_cmid)
                            or clean_event_name(q.get("name", "")).lower() == clean_evt_name
                        ):
                            quiz_item = q
                            break

                    if quiz_item:
                        qid = quiz_item.get("id")
                        if qid not in self.fetcher._checked_attempts:
                            att_data = requests.get(
                                f"{self.fetcher.base_url}/webservice/rest/server.php",
                                params={
                                    "wstoken": self.fetcher.wstoken,
                                    "wsfunction": "mod_quiz_get_user_attempts",
                                    "moodlewsrestformat": "json",
                                    "quizid": qid,
                                    "userid": userid,
                                    "status": "all",
                                },
                                timeout=15,
                            ).json()
                            self.fetcher._checked_attempts[qid] = att_data.get("attempts", [])

                        attempts = self.fetcher._checked_attempts[qid]
                        finished_attempts = [a for a in attempts if a.get("state") == "finished"]
                        attempts_done = len(finished_attempts)

                        # Kiểm tra giới hạn số lần làm bài theo chuẩn Moodle API
                        max_attempts = quiz_item.get("attempts", 0)
                        grademethod = quiz_item.get("grademethod", 1)

                        if finished_attempts:
                            grades = [a.get("sumgrades") for a in finished_attempts if a.get("sumgrades") is not None]
                            if grademethod == 1 and grades:
                                best_grade = max(grades)
                            elif grades:
                                best_grade = finished_attempts[-1].get("sumgrades")
                            else:
                                best_grade = None

                            grade_str = f" - Điểm: {best_grade}" if best_grade is not None else ""

                            if max_attempts > 0:
                                rem = max(0, max_attempts - attempts_done)
                                rem_str = " - Đã hết lượt" if rem == 0 else f" - Còn {rem} lượt"
                                limit_str = f" [Đã làm: {attempts_done}/{max_attempts} lần{rem_str}]"
                            else:
                                limit_str = f" [Đã làm: {attempts_done} lần - Không giới hạn số lần làm]"

                            return "completed", f"Đã hoàn thành{grade_str}{limit_str}"
                        else:
                            if is_open:
                                return "info", "Mở đề / Bắt đầu"
                            if max_attempts > 0:
                                limit_str = f" [Giới hạn: Tối đa {max_attempts} lần]"
                            else:
                                limit_str = " [Không giới hạn số lần làm]"
                            return "pending", f"Chưa làm{limit_str}"
                except Exception as e:
                    logging.debug(f"Lỗi kiểm tra Quiz: {e}")

        # 3. Xử lý Bài tập nộp (Assignment) qua WebService REST API
        if modname == "assign" or "mod_assign" in component:
            if course_id:
                try:
                    if course_id not in self.fetcher._course_assigns:
                        a_data = requests.get(
                            f"{self.fetcher.base_url}/webservice/rest/server.php",
                            params={
                                "wstoken": self.fetcher.wstoken,
                                "wsfunction": "mod_assign_get_assignments",
                                "moodlewsrestformat": "json",
                                "courseids[0]": course_id,
                            },
                            timeout=15,
                        ).json()
                        assigns = []
                        for c in a_data.get("courses", []):
                            assigns.extend(c.get("assignments", []))
                        self.fetcher._course_assigns[course_id] = assigns

                    assign_item = None
                    clean_evt_name = clean_event_name(event_name).lower()
                    for a in self.fetcher._course_assigns[course_id]:
                        if (
                            str(a.get("cmid")) == str(inst_or_cmid)
                            or str(a.get("id")) == str(inst_or_cmid)
                            or clean_event_name(a.get("name", "")).lower() == clean_evt_name
                        ):
                            assign_item = a
                            break

                    if assign_item:
                        aid = assign_item.get("id")
                        if aid not in self.fetcher._checked_submissions:
                            sub_data = requests.get(
                                f"{self.fetcher.base_url}/webservice/rest/server.php",
                                params={
                                    "wstoken": self.fetcher.wstoken,
                                    "wsfunction": "mod_assign_get_submission_status",
                                    "moodlewsrestformat": "json",
                                    "assignid": aid,
                                    "userid": userid,
                                },
                                timeout=15,
                            ).json()
                            lastattempt = sub_data.get("lastattempt", {})
                            sub_obj = (lastattempt.get("submission") or {}) if isinstance(lastattempt, dict) else {}
                            sub_status = sub_obj.get("status")
                            self.fetcher._checked_submissions[aid] = sub_status

                        sub_status = self.fetcher._checked_submissions[aid]
                        if sub_status == "submitted":
                            return "completed", "Đã nộp bài"
                        else:
                            if is_open:
                                return "info", "Mở đề / Bắt đầu"
                            return "pending", "Chưa nộp bài"
                except Exception as e:
                    logging.debug(f"Lỗi kiểm tra Assignment: {e}")

        # 4. Kiểm tra Completion Tracking của Moodle nếu có
        if course_id and inst_or_cmid:
            try:
                if course_id not in self.fetcher._course_completions:
                    comp_data = requests.get(
                        f"{self.fetcher.base_url}/webservice/rest/server.php",
                        params={
                            "wstoken": self.fetcher.wstoken,
                            "wsfunction": "core_completion_get_activities_completion_status",
                            "moodlewsrestformat": "json",
                            "courseid": course_id,
                            "userid": userid,
                        },
                        timeout=15,
                    ).json()
                    status_map = {}
                    for s in comp_data.get("statuses", []):
                        status_map[str(s.get("cmid"))] = s.get("state", 0)
                    self.fetcher._course_completions[course_id] = status_map

                comp_map = self.fetcher._course_completions[course_id]
                inst_str = str(inst_or_cmid)
                if inst_str in comp_map:
                    state = comp_map[inst_str]
                    if state in [1, 2, 3]:  # COMPLETE, COMPLETE_PASS, COMPLETE_FAIL
                        return "completed", "Đã hoàn thành"
                    elif state == 0:
                        if is_open:
                            return "info", "Mở đề / Bắt đầu"
                        return "pending", "Chưa hoàn thành"
            except Exception as e:
                logging.debug(f"Lỗi kiểm tra Completion: {e}")

        if is_open:
            return "info", "Mở đề / Bắt đầu"
        return "info", ""
