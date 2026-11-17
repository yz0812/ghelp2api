import json
import secrets
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlalchemy import select

from app.models import Chain, Entry, Exit, Subscription
from app.subscription import sanitize, _valid_server
from app.chain_graph import PathInput, effective_paths, legacy_compatible, preview, referenced


class EntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    subscription_id: str
    mode: Literal["auto", "fixed"] = "auto"
    node_key: str | None = None
    node_keys: list[str] | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def fixed_key(self):
        keys = self.node_keys if self.node_keys is not None else ([self.node_key] if self.node_key else [])
        keys = list(dict.fromkeys(k.strip() for k in keys if k.strip()))
        if self.mode == "fixed" and not keys:
            raise ValueError("固定模式需要至少选择一个入口节点")
        self.node_keys = keys if self.mode == "fixed" else []
        self.node_key = self.node_keys[0] if self.node_keys else None
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
    entry_id: str | None = None
    exit_id: str | None = None
    entry_mode: Literal["auto", "fixed"] = "auto"
    entry_node_key: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool = True
    relay_enabled: bool = True
    listen_port: int | None = Field(default=None, ge=1024, le=65535)
    public_host: str = ""
    paths: list[PathInput] | None = Field(default=None, min_length=1, max_length=3)
    revision: int | None = Field(default=None, ge=1)
    lock_exit: bool = True
    interval: int = Field(default=300, ge=60, le=3600)
    monitor_enabled: bool | None = None

    @model_validator(mode="after")
    def selected_entry(self):
        if self.paths is not None:
            if self.lock_exit:
                exits = {(p.hops[-1].source, p.hops[-1].ref, p.hops[-1].node_key) for p in self.paths}
                if len(exits) != 1:
                    raise ValueError("已锁定出口，所有主备路径必须使用同一出口节点")
            return self
        if not self.entry_id or not self.exit_id:
            raise ValueError("请选择入口和出口，或提供有序路径")
        if self.entry_mode == "fixed" and not self.entry_node_key:
            raise ValueError("指定入口模式需要选择一个入口节点")
        if self.entry_mode == "auto":
            self.entry_node_key = None
        return self


def install(app, service, secured):
    router = APIRouter(prefix="/api/v1", dependencies=secured)
    manager = service.chains
    models = {"entries": Entry, "exits": Exit, "chains": Chain}

    def view(row):
        if isinstance(row, Entry):
            return {k: getattr(row, k) for k in ("id", "name", "subscription_id", "mode", "node_key", "node_keys", "enabled")}
        if isinstance(row, Exit):
            config = json.loads(service.secrets.decrypt(row.config_cipher))
            password = config.pop("password", None)
            return dict(config, id=row.id, name=row.name, enabled=row.enabled, has_password=bool(password),
                        probe=manager.cached_result(row))
        with service.db() as db:
            entry = db.get(Entry, row.entry_id) if row.entry_id else None
        selected_entry = next((r["proxy"]["name"] for key, r in manager.candidates(entry, check_enabled=False)
                               if key == row.entry_node_key), None) if entry and row.entry_node_key else None
        try:
            configured_paths, configuration_error = preview(manager, effective_paths(row)), None
        except ValueError as exc:
            configured_paths, configuration_error = [], str(exc)
        return {k: getattr(row, k) for k in ("id", "name", "entry_id", "exit_id", "entry_mode", "entry_node_key", "enabled", "relay_enabled",
                                            "revision", "lock_exit", "interval", "monitor_enabled",
                                            "listen_port", "public_host")} | {"status": manager.status.get(row.id, {"state": "pending"}),
                                                                           "paths": effective_paths(row),
                                                                           "configured_paths": configured_paths,
                                                                           "configuration_error": configuration_error,
                                                                           "selected_entry": selected_entry,
                                                                           "probe": manager.cached_result(row)}

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
        selected = set(entry.node_keys or ([entry.node_key] if entry.node_key else []))
        return [n for n in data if n["included"] and n["type"] != "chain"
                and (entry.mode == "auto" or n["key"] in selected)]

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

    @router.post("/chains/{id}/probe")
    async def probe(id: str):
        with service.db() as db:
            get(db, Chain, id)
        return await manager.probe(id)

    @router.post("/exits/{id}/probe")
    async def probe_exit(id: str):
        with service.db() as db:
            get(db, Exit, id)
        return await manager.probe_exit(id)

    @router.get("/{kind}")
    async def listing(kind: Literal["entries", "exits", "chains"]):
        await manager.inspect()
        with service.db() as db:
            return [view(row) for row in db.scalars(select(models[kind]))]

    async def save(kind, payload, id=None):
        # Core validation awaits a subprocess: never hold SQLite's write transaction
        # across that await, or monitoring/concurrent edits can block the event loop.
        if kind == "chains" and payload.paths is not None:
            candidate = Chain(id=id or "validation", name=payload.name, enabled=True,
                              paths=[p.model_dump() for p in payload.paths], interval=payload.interval)
            try:
                await manager.validate_core(candidate)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        with service.db.begin() as db:
            model = models[kind]
            row = get(db, model, id) if id else model()
            if kind == "entries":
                sub = get(db, Subscription, payload.subscription_id)
                if not sub.account_ids and not sub.source_ids:
                    raise HTTPException(422, "入口订阅需要包含普通账号或订阅源节点")
                for k, v in payload.model_dump().items():
                    setattr(row, k, v)
                if payload.mode == "fixed":
                    available = {key for key, _ in manager.candidates(row, check_enabled=False)}
                    if not set(payload.node_keys).issubset(available):
                        raise HTTPException(422, "所选固定节点中有节点已失效，请重新选择")
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
                if id and payload.revision is not None and payload.revision != row.revision:
                    raise HTTPException(409, "配置已被修改，请刷新后重新编辑")
                if payload.paths is not None:
                    if id and payload.revision is None:
                        raise HTTPException(409, "编辑有序路径需要携带配置版本，请刷新后重试")
                    paths = [p.model_dump() for p in payload.paths]
                    try:
                        preview(manager, paths)
                    except ValueError as exc:
                        raise HTTPException(422, str(exc)) from None
                    row.paths = paths
                    first, last = paths[0]["hops"][0], paths[0]["hops"][-1]
                    row.entry_id = first["ref"] if first["source"] == "entry" else None
                    row.exit_id = last["ref"] if last["source"] == "proxy" else None
                    row.entry_mode = "fixed" if paths[0]["strategy"] == "fixed" else "auto"
                    row.entry_node_key = first.get("node_key") if first["source"] == "entry" else None
                    row.lock_exit, row.interval = payload.lock_exit, payload.interval
                    row.monitor_enabled = payload.monitor_enabled if payload.monitor_enabled is not None else True
                else:
                    if id and not legacy_compatible(row):
                        raise HTTPException(409, "该链路包含高级路径，请使用新版编辑器")
                    entry = get(db, Entry, payload.entry_id)
                    get(db, Exit, payload.exit_id)
                    if payload.entry_mode == "fixed":
                        available = {key for key, _ in manager.candidates(entry, check_enabled=False)}
                        if payload.entry_node_key not in available:
                            raise HTTPException(422, "指定入口节点不在当前候选中或已失效，请重新选择")
                    row.paths = []
                    row.entry_id, row.exit_id = payload.entry_id, payload.exit_id
                    row.entry_mode, row.entry_node_key = payload.entry_mode, payload.entry_node_key
                if payload.public_host and not _valid_server(payload.public_host):
                    raise HTTPException(422, "对外域名格式无效，请只填写域名或 IP")
                occupied = set(db.scalars(select(Chain.listen_port).where(Chain.id != id)))
                port = payload.listen_port or (row.listen_port if id else None)
                settings = service.settings
                if not port:
                    port = next((p for p in range(settings.proxy_port_start, settings.proxy_port_end + 1)
                                 if p not in occupied), None)
                port_range = f"{settings.proxy_port_start}–{settings.proxy_port_end}"
                if not port:
                    raise HTTPException(422, f"代理端口范围 {port_range} 已全部分配，请释放端口或扩大配置范围")
                if not settings.proxy_port_start <= port <= settings.proxy_port_end:
                    raise HTTPException(422, f"代理端口 {port} 超出允许范围，请填写 {port_range} 内的端口")
                if port in occupied:
                    raise HTTPException(422, f"代理端口 {port} 已被其他链路使用，请选择其他端口（允许范围 {port_range}）")
                for k in ("name", "enabled", "relay_enabled", "public_host"):
                    setattr(row, k, getattr(payload, k))
                row.revision = (row.revision + 1) if id else 1
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
                refs = [c.name for c in db.scalars(select(Chain)) if referenced(c, kind, id)]
            if refs:
                raise HTTPException(409, "请先解除关联：" + "、".join(refs))
            db.delete(row)
        await manager.sync()
        return {"ok": True}

    # Add after the application's existing routes: generic listing must not intercept /accounts etc.
    from app.chain_features_api import install_features
    install_features(app, service, secured, ChainInput, view, save, remove)
    app.include_router(router)
