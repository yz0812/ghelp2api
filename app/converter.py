"""Authenticated conversion records and shared offline/online rendering."""
import hashlib
import secrets
import time
from collections import Counter
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import func, select, text, update

from app.clash import converted_proxies, render
from app.chain_graph import referenced
from app.models import Chain, Conversion, Entry, Source, Subscription
from app.schemas import SourceContentInput
from app.security import digest
from app.subscription import SubscriptionError, parse_subscription


class ConvertInput(SourceContentInput):
    template: Literal["basic", "enhanced"] = "basic"
    name: str = Field(default="", max_length=80)
    allow_partial: bool = False


class PublishInput(ConvertInput):
    request_key: str | None = Field(default=None, min_length=1, max_length=64)


class EditInput(ConvertInput):
    version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


def parse_input(payload, *, preview=False):
    issues = []
    try:
        nodes = parse_subscription(payload.content, issues)
    except SubscriptionError as exc:
        raise HTTPException(422, str(exc)) from None
    if not preview:
        if not nodes:
            raise HTTPException(422, "订阅中没有可用节点")
        if issues and not payload.allow_partial:
            raise HTTPException(422, "请预览并确认跳过无效节点")
    return nodes, issues


def converted_yaml(nodes, template, full=True):
    proxies = converted_proxies(nodes, template)
    return render(proxies, {"全部节点": [p["name"] for p in proxies]}, full=full, converter_template=template)


def source_owner(db, source_id):
    return db.scalar(select(Conversion.id).where(Conversion.source_id == source_id))


def guard_source(db, source_id):
    if source_owner(db, source_id):
        raise HTTPException(409, "请从转换器的在线记录管理此来源")


def shared_source(db, record):
    return any(record.source_id in ids for ids in db.scalars(
        select(Subscription.source_ids).where(Subscription.id != record.id)))


def subscription_references(db, identifier):
    refs = list(db.scalars(select(Entry.name).where(Entry.subscription_id == identifier)))
    refs.extend(c.name for c in db.scalars(select(Chain)) if referenced(c, "subscriptions", identifier))
    return refs


def new_source(name, nodes, now):
    return Source(name=name, kind="file", url_cipher="", proxies=nodes, last_fetch=now,
                  last_attempt=now, refresh_version=1)


def eligible(sub, source):
    return (source and source.kind == "file" and source.enabled and not sub.account_ids and
            not sub.chain_ids and len(sub.source_ids) == 1 and not sub.regions and not sub.exclude_failed and
            sub.max_latency is None and sub.max_per_country is None)


def node_diff(before, after):
    def counts(nodes):
        return Counter((p["name"], yaml.safe_dump(p, sort_keys=True)) for p in nodes)
    old, new = counts(before), counts(after)
    def names(items):
        result = Counter()
        for (name, _), count in items.items():
            result[name] += count
        return result
    removed, added = names(old - new), names(new - old)
    modified = sum((removed & added).values())
    return {"added": sum(added.values()) - modified, "removed": sum(removed.values()) - modified,
            "modified": modified}


def converter_router(db_factory, vault, subscription_view, secured):
    router = APIRouter(prefix="/api/v1/convert", dependencies=secured)

    def lookup(db, identifier):
        record = db.get(Conversion, identifier)
        if not record:
            raise HTTPException(404, "转换记录不存在")
        return record, db.get(Subscription, identifier), db.get(Source, record.source_id)

    def summary(record, sub, count):
        return {"id": sub.id, "name": sub.name, "enabled": sub.enabled, "node_count": count,
                "template": record.template, "version": record.version, "created_at": record.created_at,
                "updated_at": record.updated_at}

    def detail(db, identifier):
        record, sub, source = lookup(db, identifier)
        view = subscription_view(sub)
        return summary(record, sub, len(source.proxies)) | {
            "clash_url": view["clash_url"], "proxies_url": view["proxies_url"],
            "references": subscription_references(db, identifier),
            "content": yaml.safe_dump({"proxies": source.proxies}, allow_unicode=True, sort_keys=False)}

    @router.post("")
    def download(payload: ConvertInput, full: bool = True):
        nodes, _ = parse_input(payload)
        return Response(converted_yaml(nodes, payload.template, full), media_type="application/yaml")

    @router.post("/preview")
    def preview(payload: ConvertInput, record_id: str | None = None):
        nodes, issues = parse_input(payload, preview=True)
        changes = None
        if record_id:
            with db_factory() as db:
                _, _, source = lookup(db, record_id)
                changes = node_diff(source.proxies, nodes)
        return {"count": len(nodes), "issues": issues, "changes": changes,
                "yaml": converted_yaml(nodes, payload.template) if nodes else ""}

    @router.post("/publish", status_code=201)
    def publish(payload: PublishInput):
        nodes, _ = parse_input(payload)
        signature = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        with db_factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            if payload.request_key:
                previous = db.scalar(select(Conversion).where(Conversion.request_key == payload.request_key))
                if previous:
                    if previous.request_hash != signature:
                        raise HTTPException(409, "发布请求已用于其他内容")
                    return subscription_view(db.get(Subscription, previous.id))
            now = time.time()
            name = payload.name.strip() or f"转换订阅 · {nodes[0]['name']}"
            source = new_source(name, nodes, now)
            db.add(source)
            db.flush()
            key = secrets.token_urlsafe(32)
            sub = Subscription(name=name, account_ids=[], source_ids=[source.id], dedupe=False,
                               key_hash=digest(key), key_cipher=vault.encrypt(key))
            db.add(sub)
            db.flush()
            db.add(Conversion(id=sub.id, source_id=source.id, owns_source=True, template=payload.template,
                              created_at=now, updated_at=now, request_key=payload.request_key, request_hash=signature))
            return subscription_view(sub)

    @router.get("/records")
    def records(q: str = "", enabled: bool | None = None, page: int = Query(1, ge=1)):
        query = (select(Conversion, Subscription, func.json_array_length(Source.proxies))
                 .join(Subscription, Subscription.id == Conversion.id).join(Source, Source.id == Conversion.source_id))
        if q.strip():
            query = query.where(Subscription.name.contains(q.strip(), autoescape=True))
        if enabled is not None:
            query = query.where(Subscription.enabled == enabled)
        with db_factory() as db:
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = db.execute(query.order_by(Conversion.updated_at.desc(), Conversion.id)
                              .offset((page - 1) * 20).limit(20))
            return {"total": total, "items": [summary(*row) for row in rows]}

    @router.get("/candidates")
    def candidates():
        with db_factory() as db:
            subs = db.scalars(select(Subscription).where(Subscription.id.not_in(select(Conversion.id))))
            result = []
            for sub in subs:
                source = db.get(Source, sub.source_ids[0]) if len(sub.source_ids) == 1 else None
                if eligible(sub, source) and not source_owner(db, source.id):
                    result.append({"id": sub.id, "name": sub.name, "node_count": len(source.proxies),
                                   "likely": sub.name.startswith("转换订阅 · ") and sub.name == source.name})
            return sorted(result, key=lambda row: not row["likely"])

    @router.post("/adopt/{identifier}")
    def adopt(identifier: str):
        with db_factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            sub = db.get(Subscription, identifier)
            if not sub or len(sub.source_ids) != 1:
                raise HTTPException(409, "只支持单文件源订阅")
            source = db.get(Source, sub.source_ids[0])
            if not eligible(sub, source) or source_owner(db, source.id):
                raise HTTPException(409, "订阅不符合接管条件或已纳入管理")
            db.add(Conversion(id=sub.id, source_id=source.id, owns_source=False, template="legacy"))
            return {"id": sub.id}

    @router.get("/records/{identifier}/link")
    def record_link(identifier: str):
        with db_factory() as db:
            if not db.get(Conversion, identifier):
                raise HTTPException(404, "转换记录不存在")
            return {"clash_url": subscription_view(db.get(Subscription, identifier))["clash_url"]}

    @router.get("/records/{identifier}")
    def get_record(identifier: str):
        with db_factory() as db:
            return detail(db, identifier)

    @router.put("/records/{identifier}")
    def edit(identifier: str, payload: EditInput):
        nodes, _ = parse_input(payload)
        with db_factory.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            record, sub, source = lookup(db, identifier)
            if record.version != payload.version:
                raise HTTPException(409, "记录已被更新，请保留本次输入并重新加载")
            now = time.time()
            if not record.owns_source or shared_source(db, record):
                source = new_source(payload.name, nodes, now)
                db.add(source)
                db.flush()
                record.source_id, record.owns_source = source.id, True
                sub.source_ids = [source.id]
            else:
                source.proxies, source.name = nodes, payload.name
                source.last_fetch = source.last_attempt = now
                source.refresh_version += 1
            sub.name, record.template = payload.name, payload.template
            record.updated_at, record.version = now, record.version + 1
            db.flush()
            return detail(db, identifier)

    @router.patch("/records/{identifier}")
    def set_enabled(identifier: str, enabled: bool):
        with db_factory.begin() as db:
            record, sub, _ = lookup(db, identifier)
            sub.enabled = enabled
            db.execute(update(Conversion).where(Conversion.id == record.id).values(
                updated_at=time.time(), version=Conversion.version + 1))
            return {"enabled": enabled}

    return router
