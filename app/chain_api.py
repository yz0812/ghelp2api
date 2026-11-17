import json
import secrets
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlalchemy import select

from app.models import Chain, Entry, Exit, Subscription
from app.subscription import sanitize, _valid_server


class EntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    subscription_id: str
    mode: Literal["auto", "fixed"] = "auto"
    node_key: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def fixed_key(self):
        if self.mode == "fixed" and not self.node_key:
            raise ValueError("固定模式需要选择入口节点")
        return self


class ExitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    type: Literal["http", "https", "socks5"] = "http"
    server: str = ""
    port: int = Field(default=80, ge=1, le=65535)
    username: str = ""
    password: str | None = None
    tls: bool = False
    enabled: bool = True
    content: str | None = Field(default=None, max_length=100000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class ChainInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    entry_id: str
    exit_id: str
    enabled: bool = True
    relay_enabled: bool = True
    listen_port: int | None = Field(default=None, ge=1024, le=65535)
    public_host: str = ""


def install(app, service, secured):
    router = APIRouter(prefix="/api/v1", dependencies=secured)
    manager = service.chains
    models = {"entries": Entry, "exits": Exit, "chains": Chain}

    def view(row):
        if isinstance(row, Entry):
            return {k: getattr(row, k) for k in ("id", "name", "subscription_id", "mode", "node_key", "enabled")}
        if isinstance(row, Exit):
            config = json.loads(service.secrets.decrypt(row.config_cipher))
            password = config.pop("password", None)
            return dict(config, id=row.id, name=row.name, enabled=row.enabled, has_password=bool(password))
        return {k: getattr(row, k) for k in ("id", "name", "entry_id", "exit_id", "enabled", "relay_enabled",
                                            "listen_port", "public_host")} | {"status": manager.status.get(row.id, {"state": "pending"})}

    def get(db, model, id):
        row = db.get(model, id)
        if not row:
            raise HTTPException(404, "配置不存在")
        return row

    @router.get("/entries/{id}/nodes")
    async def candidates(id: str):
        with service.db() as db:
            entry = get(db, Entry, id)
        data = service.subscription_nodes(entry.subscription_id) or []
        return [n for n in data if n["included"] and n["type"] != "chain"]

    @router.get("/chains/{id}/connection")
    async def connection(id: str):
        with service.db() as db:
            chain = get(db, Chain, id)
        return {"host": manager.host(chain), "port": chain.listen_port, "username": chain.username,
                "password": service.secrets.decrypt(chain.password_cipher), "protocols": ["http", "socks5"]}

    @router.post("/chains/{id}/rotate")
    async def rotate(id: str):
        with service.db.begin() as db:
            chain = get(db, Chain, id)
            chain.password_cipher = service.secrets.encrypt(secrets.token_urlsafe(32))
        await manager.sync()
        return await connection(id)

    @router.post("/chains/{id}/probe", status_code=202)
    async def probe(id: str):
        with service.db() as db:
            get(db, Chain, id)
        job = service.new_job("chain_probe", 1)
        service.spawn(manager.probe(id, job))
        return {"job_id": job}

    @router.get("/{kind}")
    async def listing(kind: Literal["entries", "exits", "chains"]):
        await manager.inspect()
        with service.db() as db:
            return [view(row) for row in db.scalars(select(models[kind]))]

    async def save(kind, payload, id=None):
        with service.db.begin() as db:
            model = models[kind]
            row = get(db, model, id) if id else model()
            if kind == "entries":
                sub = get(db, Subscription, payload.subscription_id)
                if not sub.account_ids and not sub.source_ids:
                    raise HTTPException(422, "入口订阅需要包含普通账号或订阅源节点")
                for k, v in payload.model_dump().items():
                    setattr(row, k, v)
                if payload.mode == "fixed" and not manager.candidates(row):
                    raise HTTPException(422, "所选固定节点已失效，请重新选择")
            elif kind == "exits":
                old = json.loads(service.secrets.decrypt(row.config_cipher)) if id else {}
                config = payload.model_dump(include={"type", "server", "port", "username", "tls"})
                if payload.password is not None:
                    config["password"] = payload.password
                elif "password" in old:
                    config["password"] = old["password"]
                if payload.content:
                    try:
                        parsed = yaml.safe_load(payload.content)
                        if isinstance(parsed, dict) and "proxies" in parsed:
                            parsed = parsed["proxies"]
                        if isinstance(parsed, list):
                            if len(parsed) != 1:
                                raise ValueError()
                            parsed = parsed[0]
                        if not isinstance(parsed, dict):
                            raise ValueError()
                        config = parsed
                    except (ValueError, yaml.YAMLError):
                        raise HTTPException(422, "请粘贴一个有效的出口 YAML 节点") from None
                config = sanitize(config, payload.name)
                if not config or config["type"] not in {"http", "socks5"}:
                    raise HTTPException(422, "出口需要有效的 HTTP、HTTPS 或 SOCKS5 地址和端口")
                row.name, row.enabled = payload.name, payload.enabled
                row.config_cipher = service.secrets.encrypt(json.dumps(config))
            else:
                get(db, Entry, payload.entry_id)
                get(db, Exit, payload.exit_id)
                if payload.public_host and not _valid_server(payload.public_host):
                    raise HTTPException(422, "对外域名格式无效，请只填写域名或 IP")
                occupied = set(db.scalars(select(Chain.listen_port).where(Chain.id != id)))
                port = payload.listen_port or (row.listen_port if id else None)
                settings = service.settings
                if not port:
                    port = next((p for p in range(settings.proxy_port_start, settings.proxy_port_end + 1)
                                 if p not in occupied), None)
                if not port or port in occupied or not settings.proxy_port_start <= port <= settings.proxy_port_end:
                    raise HTTPException(422, "代理端口已使用或超出配置范围")
                for k, v in payload.model_dump(exclude={"listen_port"}).items():
                    setattr(row, k, v)
                row.listen_port = port
                if not id:
                    row.username = f"chain_{secrets.token_hex(6)}"
                    row.password_cipher = service.secrets.encrypt(secrets.token_urlsafe(32))
            db.add(row)
            db.flush()
            result = view(row)
        await manager.sync()
        if kind == "chains":
            result["status"] = manager.status.get(row.id, {"state": "pending"})
        return result

    # Explicit routes preserve payload schemas and avoid shadowing existing API routes.
    for kind, schema in (("entries", EntryInput), ("exits", ExitInput), ("chains", ChainInput)):
        def make_create(kind, schema):
            async def create(payload):
                return await save(kind, payload)
            create.__annotations__["payload"] = schema
            return create

        def make_update(kind, schema):
            async def update(id: str, payload):
                return await save(kind, payload, id)
            update.__annotations__["payload"] = schema
            return update

        router.add_api_route(f"/{kind}", make_create(kind, schema), methods=["POST"], status_code=201)
        router.add_api_route(f"/{kind}/{{id}}", make_update(kind, schema), methods=["PUT"])

    @router.delete("/{kind}/{id}")
    async def remove(kind: Literal["entries", "exits", "chains"], id: str):
        with service.db.begin() as db:
            row = get(db, models[kind], id)
            if kind == "chains":
                refs = [s.name for s in db.scalars(select(Subscription)) if id in s.chain_ids]
            else:
                field = Chain.entry_id if kind == "entries" else Chain.exit_id
                refs = list(db.scalars(select(Chain.name).where(field == id)))
            if refs:
                raise HTTPException(409, "请先解除关联：" + "、".join(refs))
            db.delete(row)
        await manager.sync()
        return {"ok": True}

    # Add after the application's existing routes: generic listing must not intercept /accounts etc.
    app.include_router(router)
