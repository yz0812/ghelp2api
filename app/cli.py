import argparse
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(description="GHelper 初始化")
    parser.add_argument("command", choices=["init"])
    parser.parse_args()
    path = Path(".env")
    content = (f"ADMIN_USERNAME=admin\nADMIN_PASSWORD={secrets.token_urlsafe(24)}\n"
               f"ADMIN_API_KEY={secrets.token_urlsafe(32)}\n"
               f"ENCRYPTION_KEY={Fernet.generate_key().decode()}\n"
               "DATA_DIR=./data\nPUBLIC_BASE_URL=http://localhost:8000\nCOOKIE_SECURE=false\n")
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(content)
        path.chmod(0o600)
    except FileExistsError:
        parser.exit(1, ".env 已存在，未覆盖。\n")
    print("已生成 .env。请在本机打开该文件查看管理员密码；请持久保存 ENCRYPTION_KEY。")


if __name__ == "__main__":
    main()
