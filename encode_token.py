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
