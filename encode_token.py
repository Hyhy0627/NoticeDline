"""
encode_token.py - Script hỗ trợ mã hóa Google OAuth Token thành Base64.

Mục đích:
- File `token.json` chứa thông tin đăng nhập Google Calendar nhưng không được đẩy lên GitHub vì lý do bảo mật (.gitignore).
- Để GitHub Actions (chạy trên đám mây 24/7) có thể thay bạn ghi lịch vào Google Calendar, script này chuyển đổi toàn bộ nội dung `token.json` thành 1 dòng Base64 duy nhất.
- Bạn copy chuỗi in ra và dán vào GitHub Repo -> Settings -> Secrets -> GOOGLE_TOKEN_BASE64.
"""

import sys
import os
import base64

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def main():
    """Đọc file token.json, mã hóa Base64 và in ra màn hình."""
    token_file = "token.json"
    if not os.path.exists(token_file):
        print(f"LỖI: Không tìm thấy file '{token_file}'. Hãy chạy 'python main.py' xác thực thành công trước!")
        return

    with open(token_file, "rb") as f:
        token_data = f.read()

    b64_str = base64.b64encode(token_data).decode("utf-8")
    print("=" * 60)
    print("MÃ GOOGLE_TOKEN_BASE64 CỦA BẠN (DÙNG CHO GITHUB SECRETS):")
    print("=" * 60)
    print(b64_str)
    print("=" * 60)
    print("\nSao chép toàn bộ chuỗi ký tự ở trên để thêm vào GitHub Secrets.")

if __name__ == "__main__":
    main()
