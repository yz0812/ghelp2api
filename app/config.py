from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def read_version() -> str:
    """版本号只有一个来源：仓库根目录的 VERSION（vX.Y.Z），这里去掉 v 前缀用于展示。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip().removeprefix("v") or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = read_version()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    admin_username: str = "admin"
    admin_password: str = Field(min_length=9)
    admin_api_key: str = Field(min_length=32)
    encryption_key: str
    data_dir: Path = Path("data")
    public_base_url: str = "http://localhost:8000"
    cookie_secure: bool = False
    frontend_dir: Path = Path("frontend/dist")
    api_hosts: str = "gheapi.com,apihuawei.com,plebvps.com,apikuaishou.com"
    extension_version: str = "2.9.3"
    scheduler_enabled: bool = True

    @field_validator("encryption_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        Fernet(value.encode())
        return value

    @field_validator("api_hosts")
    @classmethod
    def valid_hosts(cls, value: str) -> str:
        import re
        if not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", h.strip())
                   for h in value.split(",")):
            raise ValueError("API_HOSTS 只能包含逗号分隔的域名")
        return value
