import asyncio
import contextlib
import hmac
import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select, text

from app.clash import render
from app.config import Settings
from app.db import make_db
from app.models import Account, Job, LoginSession, Node, Preference, Source, Subscription
from app.regions import label as region_label
from app.schemas import (AccountInput, AccountPatch, ImportInput, LoginInput,
                         PreferencesInput, ProbeInput, SourceImportInput, SourceInput, SourcePatch,
                         SubscriptionInput)
from app.sources import SourceError, redact, validate_url
from app.security import Secrets, digest, hint
from app.service import Service

# 批量导入时等待首次拉取的总时限，超时的源留给生成订阅时按需补拉
IMPORT_TIMEOUT = 60


def source_invalid(source) -> bool:
    """最近一次拉取失败，或没有任何可用节点缓存，都视为失效。"""
    return bool(source.last_error) or (source.last_fetch is not None and not source.proxies)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    engine, db_factory = make_db(settings.data_dir)
    vault = Secrets(settings.encryption_key)
    service = Service(settings, db_factory, vault)
    # HTTP client debug messages can include upstream token query parameters.
    logging.getLogger("httpx").disabled = True
    logging.getLogger("httpcore").disabled = True

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        yield
        await service.stop()
        engine.dispose()

    app = FastAPI(title="GHelper Clash Web", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service, app.state.db, app.state.settings = service, db_factory, settings
    attempts = {}
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        # Limit request payloads including chunked bodies before parsing/validation.
        if request.method in {"POST", "PUT", "PATCH"}:
            size, chunks = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1_000_000:
                    return JSONResponse({"detail": "请求体过大"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api/", "/sub/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default errors include the input, which may be a token/password.
        return JSONResponse({"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                                        for e in exc.errors()]}, status_code=422)

    async def auth(request: Request, credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credential is not None:
            if hmac.compare_digest(credential.credentials.encode(), settings.admin_api_key.encode()):
                return {"username": settings.admin_username, "csrf": None}
            raise HTTPException(401, "API 密钥无效")
        cookie = request.cookies.get("ghelper_session", "")
        with db_factory() as db:
            session = db.get(LoginSession, digest(cookie)) if cookie else None
            if not session or session.expires < time.time():
                raise HTTPException(401, "请先登录")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
                    raise HTTPException(403, "CSRF 校验失败，请重新登录")
            return {"username": settings.admin_username, "csrf": session.csrf}

    secured = [Depends(auth)]

    @app.post("/api/v1/auth/login")
    async def login(payload: LoginInput, request: Request, response: Response):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in {settings.public_base_url.rstrip("/"),
                                                  str(request.base_url).rstrip("/")}:
            raise HTTPException(403, "登录来源不匹配")
        now = time.time()
        client = request.client.host if request.client else "unknown"
        for key in list(attempts):
            if attempts[key][0] < now - 600:
                del attempts[key]
        since, failures = attempts.get(client, (now, 0))
        if failures >= 10:
            raise HTTPException(429, "尝试次数过多，请 10 分钟后再试")
        attempts[client] = (since, failures + 1)
        if not (hmac.compare_digest(payload.username.encode(), settings.admin_username.encode()) and
                hmac.compare_digest(payload.password.encode(), settings.admin_password.encode())):
            raise HTTPException(401, "用户名或密码错误")
        attempts.pop(client, None)
        key, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with db_factory.begin() as db:
            old = request.cookies.get("ghelper_session")
            if old:
                db.execute(delete(LoginSession).where(LoginSession.id == digest(old)))
            db.add(LoginSession(id=digest(key), csrf=csrf, expires=now + 43200))
        response.set_cookie("ghelper_session", key, httponly=True, samesite="strict",
                            secure=settings.cookie_secure, max_age=43200)
        return {"username": settings.admin_username, "csrf": csrf}

    @app.get("/api/v1/auth/me")
    async def me(user=Depends(auth)):
        return user

    @app.post("/api/v1/auth/logout", dependencies=secured)
    async def logout(request: Request, response: Response):
        with db_factory.begin() as db:
            db.execute(delete(LoginSession).where(LoginSession.id == digest(request.cookies.get("ghelper_session", ""))))
        response.delete_cookie("ghelper_session")
        return {"ok": True}

    def account_view(account, count=0):
        return {key: getattr(account, key) for key in (
            "id", "name", "token_hint", "enabled", "status", "error", "info", "info_error",
            "first_seen", "last_success", "last_attempt", "next_refresh", "degraded")} | {
            "node_count": count, "pk_hint": account.pk[:8] if account.pk else None,
            "stale": bool(account.last_success and time.time() - account.last_success >=
                          service.preferences()["refresh_minutes"] * 60)}

    @app.get("/api/v1/accounts", dependencies=secured)
    async def accounts():
        with db_factory() as db:
            counts = dict(db.execute(select(Node.account_id, func.count()).group_by(Node.account_id)).all())
            return [account_view(a, counts.get(a.id, 0)) for a in db.scalars(select(Account).order_by(Account.created_at))]

    def import_accounts(items):
        results = []
        with db_factory.begin() as db:
            for item in items:
                fingerprint = vault.fingerprint(item.token)
                existing = db.scalar(select(Account).where(Account.fingerprint == fingerprint))
                if existing:
                    results.append({"id": existing.id, "name": existing.name, "status": "duplicate"})
                    continue
                account = Account(name=item.name, token_cipher=vault.encrypt(item.token),
                                  token_hint=hint(item.token), fingerprint=fingerprint)
                db.add(account)
                db.flush()
                results.append({"id": account.id, "name": account.name, "status": "created"})
        return results

    @app.post("/api/v1/accounts", dependencies=secured, status_code=201)
    async def add_account(payload: AccountInput):
        return import_accounts([payload])[0]

    @app.post("/api/v1/accounts/import", dependencies=secured)
    async def bulk_import(payload: ImportInput):
        return {"results": import_accounts(payload.accounts)}

    @app.patch("/api/v1/accounts/{account_id}", dependencies=secured)
    async def edit_account(account_id: str, payload: AccountPatch):
        async with service.lock(account_id):
            with db_factory.begin() as db:
                account = db.get(Account, account_id)
                if not account:
                    raise HTTPException(404, "账号不存在")
                if payload.name is not None:
                    if not payload.name.strip():
                        raise HTTPException(422, "名称不能为空")
                    account.name = payload.name.strip()
                if payload.enabled is not None:
                    account.enabled = payload.enabled
                    if payload.enabled:
                        account.next_refresh = 0
                if payload.token:
                    fingerprint = vault.fingerprint(payload.token)
                    if db.scalar(select(Account.id).where(Account.fingerprint == fingerprint, Account.id != account_id)):
                        raise HTTPException(409, "token 已存在于其他账号")
                    account.token_cipher, account.token_hint = vault.encrypt(payload.token), hint(payload.token)
                    account.fingerprint, account.status, account.error = fingerprint, "pending", None
                    account.last_success, account.pk, account.first_seen = None, None, None
                    account.next_refresh, account.failures, account.info = 0, 0, {}
                    db.execute(delete(Node).where(Node.account_id == account_id))
        return {"ok": True}

    @app.delete("/api/v1/accounts/{account_id}", dependencies=secured)
    async def delete_account(account_id: str):
        async with service.lock(account_id):
            with db_factory.begin() as db:
                account = db.get(Account, account_id)
                if not account:
                    raise HTTPException(404, "账号不存在")
                db.delete(account)
                for sub in db.scalars(select(Subscription)):
                    if account_id in sub.account_ids:
                        sub.account_ids = [i for i in sub.account_ids if i != account_id]
                        if not sub.account_ids and not sub.source_ids:
                            sub.enabled = False
        return {"ok": True}

    @app.post("/api/v1/accounts/{account_id}/refresh", dependencies=secured, status_code=202)
    async def refresh(account_id: str):
        with db_factory() as db:
            ids = list(db.scalars(select(Account.id).where(Account.enabled.is_(True)))) if account_id == "all" else [account_id]
            if account_id != "all" and not db.get(Account, account_id):
                raise HTTPException(404, "账号不存在")
        job_id = service.new_job("refresh", len(ids))
        service.spawn(service.refresh_job(job_id, ids))
        return {"job_id": job_id}

    @app.get("/api/v1/nodes", dependencies=secured)
    async def nodes():
        with db_factory() as db:
            return [{"id": n.id, "account_id": a.id, "account_name": a.name, "server": n.server,
                     "port": n.port, "region": n.region, "region_label": region_label(n.region),
                     "latency": n.latency, "reachable": n.reachable,
                     "tested_at": n.tested_at, "enabled": a.enabled,
                     "stale": not a.last_success or time.time() - a.last_success > 3600}
                    for n, a in db.execute(select(Node, Account).join(Account)).all()]

    @app.post("/api/v1/probes", dependencies=secured, status_code=202)
    async def probe(payload: ProbeInput):
        if service.probe_job_id:
            return {"job_id": service.probe_job_id}
        with db_factory() as db:
            ids = list(db.scalars(select(Node.id).join(Account).where(
                Node.id.in_(payload.node_ids), Account.enabled.is_(True), Account.status != "invalid",
                Account.last_success >= time.time() - 3600)))
        if not ids:
            raise HTTPException(422, "没有可测速的当前节点")
        job_id = service.new_job("probe", len(ids))
        service.probe_job_id = job_id
        service.spawn(service.probe_job(job_id, ids))
        return {"job_id": job_id}

    def job_view(job):
        return {k: getattr(job, k) for k in ("id", "kind", "status", "total", "completed", "errors", "created_at")}

    @app.get("/api/v1/jobs", dependencies=secured)
    async def jobs():
        with db_factory() as db:
            return [job_view(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100))]

    @app.get("/api/v1/jobs/{job_id}", dependencies=secured)
    async def job(job_id: str):
        with db_factory() as db:
            item = db.get(Job, job_id)
            if not item:
                raise HTTPException(404, "任务不存在")
            return job_view(item)

    def subscription_view(sub):
        key = vault.decrypt(sub.key_cipher)
        base = settings.public_base_url.rstrip("/")
        return {k: getattr(sub, k) for k in ("id", "name", "account_ids", "source_ids", "regions",
                                             "exclude_failed", "dedupe", "max_latency", "last_probe",
                                             "enabled")} | {
            "clash_url": f"{base}/sub/{key}/clash.yaml", "proxies_url": f"{base}/sub/{key}/proxies.yaml"}

    @app.get("/api/v1/subscriptions", dependencies=secured)
    async def subscriptions():
        with db_factory() as db:
            return [subscription_view(s) for s in db.scalars(select(Subscription))]

    def verify_accounts(db, ids):
        existing = set(db.scalars(select(Account.id).where(Account.id.in_(ids))))
        if existing != set(ids):
            raise HTTPException(422, "包含不存在的账号")

    def verify_sources(db, ids):
        existing = set(db.scalars(select(Source.id).where(Source.id.in_(ids))))
        if existing != set(ids):
            raise HTTPException(422, "包含不存在的订阅源")

    @app.post("/api/v1/subscriptions", dependencies=secured, status_code=201)
    async def add_subscription(payload: SubscriptionInput):
        key = secrets.token_urlsafe(32)
        with db_factory.begin() as db:
            verify_accounts(db, payload.account_ids)
            verify_sources(db, payload.source_ids)
            sub = Subscription(**payload.model_dump(), key_hash=digest(key), key_cipher=vault.encrypt(key))
            db.add(sub)
            db.flush()
            view, sub_id = subscription_view(sub), sub.id
            filtered = sub.exclude_failed or sub.max_latency
        if filtered:  # 提交后再触发，避免后台任务读到未提交的数据
            service.schedule_probe(sub_id)
        return view

    @app.put("/api/v1/subscriptions/{sub_id}", dependencies=secured)
    async def edit_subscription(sub_id: str, payload: SubscriptionInput):
        with db_factory.begin() as db:
            sub = db.get(Subscription, sub_id)
            if not sub:
                raise HTTPException(404, "订阅不存在")
            verify_accounts(db, payload.account_ids)
            verify_sources(db, payload.source_ids)
            for k, v in payload.model_dump().items():
                setattr(sub, k, v)
            view, filtered = subscription_view(sub), sub.exclude_failed or sub.max_latency
        if filtered:
            service.schedule_probe(sub_id)
        return view

    @app.post("/api/v1/subscriptions/{sub_id}/rotate", dependencies=secured)
    async def rotate(sub_id: str):
        with db_factory.begin() as db:
            sub = db.get(Subscription, sub_id)
            if not sub:
                raise HTTPException(404, "订阅不存在")
            key = secrets.token_urlsafe(32)
            sub.key_hash, sub.key_cipher = digest(key), vault.encrypt(key)
            return subscription_view(sub)

    @app.delete("/api/v1/subscriptions/{sub_id}", dependencies=secured)
    async def delete_subscription(sub_id: str):
        with db_factory.begin() as db:
            sub = db.get(Subscription, sub_id)
            if not sub:
                raise HTTPException(404, "订阅不存在")
            db.delete(sub)
        return {"ok": True}

    def source_view(source):
        try:
            address = redact(vault.decrypt(source.url_cipher))
        except Exception:
            address = "无法解密"
        return {"id": source.id, "name": source.name, "address": address,
                "enabled": source.enabled, "node_count": len(source.proxies or []),
                "last_fetch": source.last_fetch, "last_error": source.last_error,
                "failures": source.failures, "created_at": source.created_at,
                "invalid": source_invalid(source)}

    @app.get("/api/v1/sources", dependencies=secured)
    async def sources():
        with db_factory() as db:
            return [source_view(s) for s in db.scalars(select(Source).order_by(Source.created_at))]

    @app.post("/api/v1/sources", dependencies=secured, status_code=201)
    async def add_source(payload: SourceInput):
        try:
            url = validate_url(payload.url)
        except SourceError as exc:
            raise HTTPException(422, str(exc)) from None
        with db_factory.begin() as db:
            source = Source(name=payload.name, url_cipher=vault.encrypt(url))
            db.add(source)
            db.flush()
            source_id = source.id
        await service.refresh_source(source_id)  # 创建后立即拉取一次，失败也会保存在 last_error
        with db_factory() as db:
            return source_view(db.get(Source, source_id))

    @app.post("/api/v1/sources/import", dependencies=secured, status_code=201)
    async def import_sources(payload: SourceImportInput):
        prepared = []
        for item in payload.sources:
            try:
                prepared.append((item.name, validate_url(item.url)))
            except SourceError as exc:
                raise HTTPException(422, f"{item.name}：{exc}") from None
        with db_factory.begin() as db:
            created = []
            for name, url in prepared:
                source = Source(name=name, url_cipher=vault.encrypt(url))
                db.add(source)
                db.flush()
                created.append(source.id)
        limit = asyncio.Semaphore(5)

        async def one(source_id):
            async with limit:
                await service.refresh_source(source_id)

        # 整批导入设总时限：没来得及拉取的源保持未拉取状态，生成订阅时会按需补拉
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(*(one(i) for i in created), return_exceptions=True),
                                   IMPORT_TIMEOUT)
        with db_factory() as db:
            return [source_view(db.get(Source, i)) for i in created]

    @app.post("/api/v1/sources/check", dependencies=secured, status_code=202)
    async def check_sources():
        """重新拉取每个已启用的订阅源，把失效的挑出来。"""
        if service.source_check_job_id:
            return {"job_id": service.source_check_job_id}
        with db_factory() as db:
            ids = list(db.scalars(select(Source.id).where(Source.enabled.is_(True))))
        if not ids:
            raise HTTPException(422, "还没有启用的订阅源可检测")
        job_id = service.new_job("source", len(ids))
        service.source_check_job_id = job_id
        service.spawn(service.source_check_job(job_id, ids))
        return {"job_id": job_id}

    def detach_source(db, source_id):
        for sub in db.scalars(select(Subscription)):
            if source_id in sub.source_ids:
                sub.source_ids = [i for i in sub.source_ids if i != source_id]
                if not sub.account_ids and not sub.source_ids:
                    sub.enabled = False

    @app.post("/api/v1/sources/invalid/disable", dependencies=secured)
    async def disable_invalid_sources():
        count = 0
        with db_factory.begin() as db:
            for source in db.scalars(select(Source)):
                if source.enabled and source_invalid(source):
                    source.enabled = False
                    count += 1
        return {"disabled": count}

    @app.post("/api/v1/sources/invalid/delete", dependencies=secured)
    async def delete_invalid_sources():
        with db_factory.begin() as db:
            invalid = [s for s in db.scalars(select(Source)) if source_invalid(s)]
            for source in invalid:
                db.delete(source)
                detach_source(db, source.id)
            deleted = [s.id for s in invalid]
        for source_id in deleted:
            service.forget_source(source_id)
        return {"deleted": len(deleted)}

    @app.put("/api/v1/sources/{source_id}", dependencies=secured)
    async def edit_source(source_id: str, payload: SourcePatch):
        async with service.lock(f"source:{source_id}"):
            with db_factory.begin() as db:
                source = db.get(Source, source_id)
                if not source:
                    raise HTTPException(404, "订阅源不存在")
                if payload.name is not None:
                    source.name = payload.name.strip()
                if payload.enabled is not None:
                    source.enabled = payload.enabled
                if payload.url:
                    try:
                        source.url_cipher = vault.encrypt(validate_url(payload.url))
                    except SourceError as exc:
                        raise HTTPException(422, str(exc)) from None
                    # 地址变了：旧节点、错误与退避都属于旧地址，清空后下次生成时立即拉取
                    source.proxies, source.last_fetch, source.last_attempt = [], None, None
                    source.last_error, source.failures = None, 0
        return {"ok": True}

    @app.post("/api/v1/sources/{source_id}/refresh", dependencies=secured)
    async def refresh_source(source_id: str):
        with db_factory() as db:
            if not db.get(Source, source_id):
                raise HTTPException(404, "订阅源不存在")
        count, error = await service.refresh_source(source_id)
        if error:
            raise HTTPException(502, error)
        return {"node_count": count}

    @app.delete("/api/v1/sources/{source_id}", dependencies=secured)
    async def delete_source(source_id: str):
        with db_factory.begin() as db:
            source = db.get(Source, source_id)
            if not source:
                raise HTTPException(404, "订阅源不存在")
            db.delete(source)
            detach_source(db, source_id)
        service.forget_source(source_id)
        return {"ok": True}

    async def config_response(account_ids, source_ids, full, regions=None, exclude_failed=False,
                              dedupe=True, max_latency=None):
        proxies, groups = await service.export_proxies(account_ids, source_ids, regions,
                                                       exclude_failed, dedupe, max_latency)
        if not proxies:
            raise HTTPException(503, "当前没有可用节点，请检查账号或等待刷新", headers={"Retry-After": "60"})
        return Response(render(proxies, groups, full=full), media_type="application/yaml",
                        headers={"Content-Disposition": 'inline; filename="ghelper.yaml"',
                                 "profile-update-interval": "1"})

    @app.get("/api/v1/config/clash", dependencies=secured)
    async def direct_config(account_ids: str = "", source_ids: str = "", format: str = "full",
                            dedupe: bool = True, exclude_failed: bool = False,
                            max_latency: int | None = Query(default=None, ge=1, le=10000)):
        if format not in {"full", "proxies"}:
            raise HTTPException(422, "format 必须是 full 或 proxies")
        accounts = [i for i in account_ids.split(",") if i]
        sources = [i for i in source_ids.split(",") if i]
        if not accounts and not sources:
            raise HTTPException(422, "请至少选择一个账号或订阅源")
        if len(accounts) > 100 or len(sources) > 100:
            raise HTTPException(422, "一次最多选择 100 个来源")
        with db_factory() as db:
            verify_accounts(db, accounts)
            verify_sources(db, sources)
        return await config_response(accounts, sources, format == "full", exclude_failed=exclude_failed,
                                     dedupe=dedupe, max_latency=max_latency)

    @app.get("/sub/{key}/{filename}", include_in_schema=False)
    async def subscription(key: str, filename: str):
        if filename not in {"clash.yaml", "proxies.yaml"} or len(key) > 100:
            raise HTTPException(404, "订阅不存在")
        with db_factory() as db:
            sub = db.scalar(select(Subscription).where(Subscription.key_hash == digest(key),
                                                       Subscription.enabled.is_(True)))
            if not sub:
                raise HTTPException(404, "订阅不存在或已停用")
            accounts, sources = sub.account_ids, sub.source_ids
            regions, exclude_failed, dedupe = sub.regions, sub.exclude_failed, sub.dedupe
            max_latency = sub.max_latency
        response = await config_response(accounts, sources, filename == "clash.yaml", regions,
                                         exclude_failed, dedupe, max_latency)
        # Revocation/deletion may happen while a slow refresh is in progress.
        with db_factory() as db:
            if not db.scalar(select(Subscription.id).where(Subscription.key_hash == digest(key),
                                                            Subscription.enabled.is_(True))):
                raise HTTPException(404, "订阅已撤销")
        return response

    @app.get("/api/v1/settings", dependencies=secured)
    async def get_settings():
        return service.preferences() | {"public_base_url": settings.public_base_url,
                                        "extension_version": settings.extension_version}

    @app.put("/api/v1/settings", dependencies=secured)
    async def save_settings(payload: PreferencesInput):
        with db_factory.begin() as db:
            db.merge(Preference(key="general", value=payload.model_dump()))
        return payload

    @app.get("/api/v1/openapi.json", dependencies=secured, include_in_schema=False)
    async def schema():
        return app.openapi()

    @app.get("/api/docs", dependencies=secured, include_in_schema=False)
    async def docs():
        return get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title="GHelper API")

    @app.get("/healthz", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def ready():
        try:
            with db_factory() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(503, "数据库不可用") from None
        return {"status": "ready"}

    if settings.frontend_dir.is_dir():
        assets = settings.frontend_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str):
            if path.startswith(("api/", "sub/")):
                raise HTTPException(404, "接口不存在")
            return FileResponse(settings.frontend_dir / "index.html")
    return app
