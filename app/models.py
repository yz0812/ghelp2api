import time
import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    token_cipher: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    token_hint: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    info_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    info: Mapped[dict] = mapped_column(JSON, default=dict)
    pk: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_success: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_attempt: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_refresh: Mapped[float] = mapped_column(Float, default=0)
    failures: Mapped[int] = mapped_column(default=0)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    server: Mapped[str] = mapped_column(Text)
    port: Mapped[int]
    region: Mapped[str] = mapped_column(String(80))
    latency: Mapped[float | None] = mapped_column(Float, nullable=True)
    reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tested_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_cipher: Mapped[str] = mapped_column(Text)
    account_ids: Mapped[list] = mapped_column(JSON)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    regions: Mapped[list] = mapped_column(JSON, default=list)
    exclude_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe: Mapped[bool] = mapped_column(Boolean, default=True)
    max_latency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_per_country: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_probe: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    total: Mapped[int] = mapped_column(default=0)
    completed: Mapped[int] = mapped_column(default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Source(Base):
    """外部订阅源；地址可能含 token，只存密文。"""
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    url_cipher: Mapped[str] = mapped_column(Text)
    # url：按地址定期拉取；file：用户上传的文件内容，节点只在重新上传时变化
    kind: Mapped[str] = mapped_column(String(10), default="url", server_default="url")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    proxies: Mapped[list] = mapped_column(JSON, default=list)
    last_fetch: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_attempt: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failures: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class ProxyCheck(Base):
    """按节点指纹缓存的可用性探测结果。"""
    __tablename__ = "proxy_checks"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    reachable: Mapped[bool] = mapped_column(Boolean, default=False)
    latency: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(String(10), default="tcp")
    tested_at: Mapped[float] = mapped_column(Float, default=time.time)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf: Mapped[str] = mapped_column(String(80))
    expires: Mapped[float] = mapped_column(Float)


class Preference(Base):
    __tablename__ = "preferences"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
