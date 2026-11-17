"""Orchestration endpoints registered before the legacy generic resource routes."""
import copy
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.chain_graph import effective_paths, preview
from app.models import Chain, ChainEvent, ChainSample, ChainTemplate, Job, Subscription


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=50)
    operation: Literal["probe", "enable", "disable", "delete", "monitor_on", "monitor_off"]


class NamedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


def install_features(app, service, secured, ChainInput, view, save, remove):
    router = APIRouter(prefix="/api/v1", dependencies=secured)
    manager = service.chains

    class DiagnosticInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        chain_id: str | None = None
        draft: ChainInput | None = None

        @model_validator(mode="after")
        def one_source(self):
            if bool(self.chain_id) == bool(self.draft):
                raise ValueError("请选择已有链路或提交一份草稿")
            return self

    class TemplateInput(NamedInput):
        config: ChainInput

    def existing(db, model, identifier):
        row = db.get(model, identifier)
        if not row:
            raise HTTPException(404, "配置不存在")
        return row

    def draft_chain(payload):
        values = payload.model_dump(exclude={"listen_port", "revision"})
        values["paths"] = values["paths"] or []
        return Chain(id="draft", revision=1, listen_port=0, **values)

    def config_for(chain):
        return dict(name=chain.name, paths=copy.deepcopy(effective_paths(chain)),
                    interval=chain.interval, lock_exit=chain.lock_exit, monitor_enabled=chain.monitor_enabled,
                    relay_enabled=chain.relay_enabled, public_host=chain.public_host, enabled=False)

    @router.post("/chains/validate")
    async def validate(payload: ChainInput):
        try:
            draft = draft_chain(payload)
            paths = preview(manager, effective_paths(draft))
            await manager.validate_core(draft)
            return {"valid": True, "paths": paths}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/chains/diagnostics", status_code=202)
    async def diagnostics(payload: DiagnosticInput):
        if payload.chain_id:
            with service.db() as db:
                chain = existing(db, Chain, payload.chain_id)
        else:
            chain = draft_chain(payload.draft)
        try:
            job_id = manager.diagnostics.start(chain, source="manual" if payload.chain_id else "draft")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"job_id": job_id}

    @router.post("/jobs/{id}/cancel")
    async def cancel(id: str):
        with service.db() as db:
            job = existing(db, Job, id)
            if job.kind != "chain_diagnostic":
                raise HTTPException(409, "该任务类型暂不支持取消")
        await manager.diagnostics.cancel(id)
        return {"ok": True}

    @router.get("/chains/{id}")
    async def detail(id: str):
        await manager.inspect()
        with service.db() as db:
            return view(existing(db, Chain, id))

    @router.post("/chains/{id}/clone", status_code=201)
    async def clone(id: str, payload: NamedInput):
        with service.db() as db:
            values = config_for(existing(db, Chain, id))
        values["name"] = payload.name
        return await save("chains", ChainInput(**values))

    @router.post("/chains/batch")
    async def batch(payload: BatchInput):
        results = []
        for identifier in dict.fromkeys(payload.ids):
            try:
                validated_revision = None
                if payload.operation == "enable":
                    with service.db() as db:
                        candidate = existing(db, Chain, identifier)
                    preview(manager, effective_paths(candidate))
                    await manager.validate_core(candidate)
                    validated_revision = candidate.revision
                with service.db.begin() as db:
                    chain = existing(db, Chain, identifier)
                    if validated_revision is not None and chain.revision != validated_revision:
                        raise HTTPException(409, "配置已被修改，请刷新后重试")
                    item = {"id": identifier, "name": chain.name, "ok": True}
                    if payload.operation == "probe":
                        item["job_id"] = manager.diagnostics.start(chain)
                    elif payload.operation == "delete":
                        refs = [s.name for s in db.scalars(select(Subscription)) if identifier in s.chain_ids]
                        if refs:
                            raise HTTPException(409, "请先解除订阅关联：" + "、".join(refs))
                        db.delete(chain)
                    else:
                        if payload.operation in {"monitor_on", "monitor_off"}:
                            chain.monitor_enabled = payload.operation == "monitor_on"
                        else:
                            chain.enabled = payload.operation == "enable"
                        chain.revision += 1
                    results.append(item)
            except (HTTPException, ValueError) as exc:
                results.append({"id": identifier, "ok": False,
                                "error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)})
        if payload.operation != "probe":
            await manager.sync()
        return {"results": results}

    @router.get("/chain-templates")
    async def templates():
        result = []
        with service.db() as db:
            rows = list(db.scalars(select(ChainTemplate).order_by(ChainTemplate.created_at.desc())))
        for row in rows:
            try:
                preview(manager, row.config["paths"])
                error = None
            except ValueError as exc:
                error = str(exc)
            result.append({"id": row.id, "name": row.name, "config": row.config,
                           "created_at": row.created_at, "error": error})
        return result

    async def store_template(payload, identifier=None):
        chain = draft_chain(payload.config)
        values = config_for(chain)
        # Templates contain references only; never runtime credentials, public address or a listener port.
        values.pop("public_host", None)
        with service.db.begin() as db:
            row = existing(db, ChainTemplate, identifier) if identifier else ChainTemplate()
            row.name, row.config = payload.name, values
            db.add(row)
            db.flush()
            return {"id": row.id, "name": row.name, "config": row.config, "created_at": row.created_at}

    @router.post("/chain-templates", status_code=201)
    async def create_template(payload: TemplateInput):
        return await store_template(payload)

    @router.put("/chain-templates/{id}")
    async def update_template(id: str, payload: TemplateInput):
        return await store_template(payload, id)

    @router.delete("/chain-templates/{id}")
    async def delete_template(id: str):
        with service.db.begin() as db:
            db.delete(existing(db, ChainTemplate, id))
        return {"ok": True}

    @router.get("/chains/{id}/history")
    async def history(id: str, range: Literal["24h", "7d", "30d"] = "24h",
                      offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200)):
        duration = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}[range]
        bucket = {"24h": 300, "7d": 1800, "30d": 3600}[range]
        since = time.time() - duration
        with service.db() as db:
            chain = existing(db, Chain, id)
            samples = list(db.scalars(select(ChainSample).where(ChainSample.chain_id == id,
                                                               ChainSample.tested_at >= since)
                                      .order_by(ChainSample.tested_at)))
            events = list(db.scalars(select(ChainEvent).where(ChainEvent.chain_id == id,
                                                             ChainEvent.created_at >= since)
                                     .order_by(ChainEvent.created_at.desc(), ChainEvent.id).offset(offset).limit(limit + 1)))
        groups = {}
        for sample in samples:
            at = int(sample.tested_at // bucket) * bucket
            groups.setdefault(at, []).append(sample.result)
        series = []
        for at, rows in groups.items():
            latencies = [r["latency"] for r in rows if r.get("reachable") is True and r.get("latency") is not None]
            series.append({"at": at, "latency": round(sum(latencies) / len(latencies), 1) if latencies else None,
                           "success": sum(r.get("reachable") is True for r in rows),
                           "failed": sum(r.get("reachable") is False for r in rows),
                           "unknown": sum(r.get("reachable") is None for r in rows)})
        measured = [s.result for s in samples if s.result.get("source") == "monitor" and s.result.get("reachable") is not None]
        return {"range": range, "bucket_seconds": bucket, "monitor_enabled": chain.monitor_enabled,
                "series": series, "sample_count": len(samples), "monitor_sample_count": len(measured),
                "availability": round(100 * sum(r["reachable"] is True for r in measured) / len(measured), 2) if measured else None,
                "latest": samples[-1].result if samples else None,
                "events": [{"id": e.id, "at": e.created_at, "kind": e.kind, "detail": e.detail} for e in events[:limit]],
                "next_offset": offset + limit if len(events) > limit else None}

    app.include_router(router)
