"""Validated, credential-free path references and a shared Mihomo compiler."""
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import Chain, Entry, Exit, Subscription
from app.security import digest


class HopInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: Literal["entry", "subscription", "proxy"]
    ref: str = Field(min_length=1, max_length=64)
    node_key: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def selected(self):
        if self.source == "subscription" and not self.node_key:
            raise ValueError("订阅节点需要指定节点指纹")
        if self.source == "proxy" and self.node_key:
            raise ValueError("已保存代理不能指定订阅节点")
        return self


class PathInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="主路径", min_length=1, max_length=80)
    hops: list[HopInput] = Field(min_length=2, max_length=5)
    strategy: Literal["auto", "fixed", "stable", "fallback"] = "auto"
    tolerance_ms: int = Field(default=50, ge=0, le=5000)
    ordered_keys: list[str] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def topology(self):
        if any(h.source == "entry" for h in self.hops[1:]):
            raise ValueError("入口候选池只能放在第一跳")
        if self.hops[0].source != "entry":
            self.strategy = "fixed"
        elif self.strategy == "fixed" and not self.hops[0].node_key:
            raise ValueError("指定入口模式需要选择节点")
        elif self.strategy != "fixed":
            self.hops[0].node_key = None
        if self.strategy == "fallback" and not self.ordered_keys:
            raise ValueError("主备入口需要明确选择候选节点及优先顺序")
        if len(set(self.ordered_keys)) != len(self.ordered_keys):
            raise ValueError("主备入口顺序不能包含重复节点")
        refs = [(h.source, h.ref, h.node_key) for h in self.hops]
        if len(set(refs)) != len(refs):
            raise ValueError("同一路径不能重复使用同一节点")
        return self


def effective_paths(chain):
    if chain.paths:
        return chain.paths
    return [{"name": "主路径", "strategy": chain.entry_mode or "auto", "tolerance_ms": 50,
             "ordered_keys": [], "hops": [
                 {"source": "entry", "ref": chain.entry_id, "node_key": chain.entry_node_key},
                 {"source": "proxy", "ref": chain.exit_id, "node_key": None}]}]


def referenced(chain, kind, identifier):
    source = {"entries": "entry", "exits": "proxy", "subscriptions": "subscription"}[kind]
    return any(h["source"] == source and h["ref"] == identifier
               for p in effective_paths(chain) for h in p["hops"])


def referenced_subscriptions(db, chains):
    ids = set()
    for chain in chains:
        for path in effective_paths(chain):
            for hop in path["hops"]:
                if hop["source"] == "subscription":
                    ids.add(hop["ref"])
                elif hop["source"] == "entry":
                    entry = db.get(Entry, hop["ref"]) if hop["ref"] else None
                    if entry:
                        ids.add(entry.subscription_id)
    return [sub for identifier in ids if (sub := db.get(Subscription, identifier))]


def resolve_hop(manager, hop):
    from app.chains import ChainError
    from app.service import proxy_key
    with manager.service.db() as db:
        if hop["source"] == "entry":
            entry = db.get(Entry, hop["ref"])
            if not entry or not entry.enabled:
                raise ChainError("入口不存在或已停用")
            nodes = [(key, dict(row["proxy"])) for key, row in manager.candidates(entry)]
            if hop.get("node_key"):
                nodes = [item for item in nodes if item[0] == hop["node_key"]]
            label = entry.name
        elif hop["source"] == "proxy":
            proxy = db.get(Exit, hop["ref"])
            if not proxy or not proxy.enabled:
                raise ChainError("已保存代理不存在或已停用")
            config = json.loads(manager.service.secrets.decrypt(proxy.config_cipher))
            config["name"] = proxy.name
            nodes, label = [(proxy_key(config), config)], proxy.name
        else:
            sub = db.get(Subscription, hop["ref"])
            if not sub or not sub.enabled:
                raise ChainError("节点来源订阅不存在或已停用")
            rows = manager.service._subscription_rows(sub, time.time())
            manager.service._apply_checks(rows, time.time())
            rows = manager.service._filter(rows, sub.exclude_failed, sub.max_latency, sub.max_per_country, sub.dedupe)
            nodes = [(proxy_key(r["proxy"]), dict(r["proxy"])) for r in rows
                     if proxy_key(r["proxy"]) == hop.get("node_key")]
            nodes, label = nodes[:1], sub.name
    if not nodes:
        raise ChainError("指定节点已失效、被过滤或没有可用候选，请重新选择")
    return nodes, label


def path_candidates(path, hops, *, strict):
    from app.chains import ChainError
    for nodes, _ in hops[1:]:
        if any(n["type"] == "hysteria2" or n.get("network") in {"quic", "kcp"} for _, n in nodes):
            raise ChainError("后续跳暂不支持依赖 UDP 的传输，请选择 TCP 节点")
    candidates = hops[0][0]
    if path["strategy"] == "fallback":
        available = dict(candidates)
        if strict and any(k not in available for k in path["ordered_keys"]):
            raise ChainError("主备顺序中存在已失效的入口节点")
        candidates = [(k, available[k]) for k in path["ordered_keys"] if k in available]
    tail = [nodes[0] for nodes, _ in hops[1:]]
    keys = [key for key, _ in tail]
    if len(set(keys)) != len(keys):
        raise ChainError("不同来源引用了同一节点，路径不能重复")
    candidates = [(key, node) for key, node in candidates if key not in keys]
    if not candidates:
        raise ChainError("入口候选失效或与后续节点重复")
    return candidates, tail


def resolve(manager, paths, *, strict=True):
    from app.chains import ChainError
    resolved, errors = [], []
    for index, path in enumerate(paths):
        try:
            hops = [resolve_hop(manager, h) for h in path["hops"]]
            candidates, tail = path_candidates(path, hops, strict=strict)
            resolved.append((index, path, candidates, tail, hops))
        except ChainError as exc:
            errors.append({"path_index": index, "error": str(exc)})
            if strict:
                raise ChainError(f"路径 {index + 1}：{exc}") from None
    if not resolved:
        raise ChainError("；".join(e["error"] for e in errors) or "没有可用路径")
    return resolved, errors


def preview(manager, paths):
    resolved, _ = resolve(manager, paths)
    return [{"name": path["name"], "strategy": path["strategy"], "hops": [
        {"source": path["hops"][i]["source"], "name": label if len(nodes) > 1 else nodes[0][1]["name"],
         "candidates": len(nodes), "type": nodes[0][1]["type"]} for i, (nodes, label) in enumerate(hops)]}
        for _, path, _, _, hops in resolved]


def compile_graph(manager, chain):
    from app.chains import ChainError
    from app.mihomo import TEST_URL
    if not chain.enabled:
        raise ChainError("链路已停用")
    resolved, errors = resolve(manager, effective_paths(chain), strict=False)
    proxies, groups = [], []
    interval = chain.interval or 300
    for index, path, candidates, tail, _ in resolved:
        terminals = []
        for key, first in candidates:
            nodes = [(key, first), *tail]
            names, snapshot = [], []
            for hop_index, (node_key, node) in enumerate(nodes):
                name = f"chain-{chain.id}-p{index}-{key}-h{hop_index}"
                proxy = dict(node, name=name, udp=False)
                proxy.pop("dialer-proxy", None)
                if names:
                    proxy["dialer-proxy"] = names[-1]
                names.append(name)
                proxies.append(proxy)
                snapshot.append({"name": node["name"], "type": node["type"], "key": node_key})
            terminal = names[-1]
            terminals.append(terminal)
            manager.entry_labels[terminal] = first["name"]
            manager.entry_keys[terminal] = key
            manager.path_details[terminal] = {"path_index": index, "path_name": path["name"], "hops": snapshot}
        mode = path["strategy"]
        group = {"name": f"chain-{chain.id}-path-{index}", "proxies": terminals,
                 "type": "select" if mode == "fixed" else "fallback" if mode == "fallback" else "url-test"}
        if mode != "fixed":
            group.update(url=TEST_URL, interval=interval, lazy=False)
        if mode in {"auto", "stable"}:
            group["tolerance"] = path["tolerance_ms"] if mode == "stable" else 0
        if mode == "stable":
            # v1.19.33 URLTest.fast only checks existing selection in proxies[1:].
            # A rejecting sentinel keeps every real candidate in that slice and
            # preserves tolerance even when the first real candidate was fastest.
            group["proxies"] = ["REJECT", *terminals]
        groups.append(group)
    name = f"链路 · {chain.name} · {chain.id[:8]}"
    if len(groups) == 1:
        group = dict(groups[0], name=name)
    else:
        group = {"name": name, "type": "fallback", "proxies": [g["name"] for g in groups],
                 "url": TEST_URL, "interval": interval, "lazy": False, "_groups": groups}
    group["_errors"] = errors
    return proxies, group


def groups_for(group):
    return [*group.get("_groups", []), {k: v for k, v in group.items() if not k.startswith("_")}]


def graph_fingerprint(manager, chain):
    paths = effective_paths(chain)
    material = [chain.enabled, paths, chain.interval, chain.lock_exit]
    for path in paths:
        for hop in path["hops"]:
            try:
                nodes, _ = resolve_hop(manager, hop)
                material.append([(key, {k: v for k, v in node.items() if k != "name"}) for key, node in nodes])
            except ValueError as exc:
                material.append(str(exc))
    return digest(json.dumps(material, sort_keys=True))


def legacy_compatible(chain: Chain):
    paths = effective_paths(chain)
    return (len(paths) == 1 and len(paths[0]["hops"]) == 2 and paths[0]["strategy"] in {"auto", "fixed"}
            and paths[0]["hops"][0]["source"] == "entry" and paths[0]["hops"][1]["source"] == "proxy")
