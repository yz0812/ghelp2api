"""Compare reference availability against a single, read-only configuration and probe sample."""
import json
from dataclasses import dataclass
from itertools import batched

from sqlalchemy import select

from app.chain_graph import effective_paths, path_candidates
from app.chains import ChainError
from app.mihomo import available
from app.models import Account, Chain, Entry, Exit, Node, ProxyCheck, Source, Subscription
from app.service import CHECK_TTL, proxy_key
from app.subscription import sanitize


def subscription_for(hop, entries):
    if hop["source"] == "subscription":
        return hop["ref"]
    if hop["source"] == "entry" and hop["ref"] in entries:
        return entries[hop["ref"]].subscription_id
    return None


@dataclass
class Snapshot:
    subscriptions: dict
    affected: set
    chains: list
    entries: dict
    exits: dict
    account_rows: dict
    sources: list
    checks: dict
    kernel: bool


def source_keys(proxies):
    # Older caches may contain non-canonical ports, HTTPS aliases or unsupported records.
    return {proxy_key(proxy) for item in proxies if (proxy := sanitize(item, "节点"))}


def uses_checks(sub):
    return sub.exclude_failed or sub.max_latency or sub.max_per_country


def capture(service, db, source_id, previous, now):
    subs = {s.id: s for s in db.scalars(select(Subscription))}
    affected = {s.id for s in subs.values() if source_id in s.source_ids}
    if not affected:
        return None
    entries = {e.id: e for e in db.scalars(select(Entry))}
    chains, needed, exit_ids = [], affected.copy(), set()
    for chain in db.scalars(select(Chain)):
        hops = [h for p in effective_paths(chain) for h in p["hops"]]
        refs = {subscription_for(h, entries) for h in hops}
        if refs & affected:
            chains.append(chain)
            needed.update(refs)
            exit_ids.update(h["ref"] for h in hops if h["source"] == "proxy")
    subs = {i: s for i, s in subs.items() if i in needed}
    source_ids = {i for s in subs.values() for i in s.source_ids}
    sources = list(db.scalars(select(Source).where(Source.id.in_(source_ids), Source.enabled.is_(True))
                              .order_by(Source.created_at)))
    account_ids = {i for s in subs.values() for i in s.account_ids}
    pairs = db.execute(select(Node, Account).join(Account).where(
            Account.id.in_(account_ids), Account.enabled.is_(True), Account.status != "invalid",
            Account.last_success >= now - 3600)).all()
    account_rows = {s.id: service._account_rows(
        ((node, account) for node, account in pairs if account.id in s.account_ids), s.regions)
        for s in subs.values()}
    exits = {}
    for exit_row in db.scalars(select(Exit).where(Exit.id.in_(exit_ids), Exit.enabled.is_(True))):
        config = json.loads(service.secrets.decrypt(exit_row.config_cipher))
        exits[exit_row.id] = {proxy_key(config): config}
    filtered = [sub for sub in subs.values() if uses_checks(sub)]
    checked_sources = {i for sub in filtered for i in sub.source_ids}
    keys = {proxy_key(r["proxy"]) for sub in filtered for r in account_rows[sub.id]}
    for source in sources:
        if source.id in checked_sources:
            keys.update(source_keys(source.proxies))
            if source.id == source_id:
                keys.update(source_keys(previous))
    checks = {}
    for chunk in batched(keys, 900):
        query = select(ProxyCheck).where(ProxyCheck.key.in_(chunk), ProxyCheck.tested_at > now - CHECK_TTL)
        checks.update((c.key, c) for c in db.scalars(query))
    return Snapshot(subs, affected, chains, entries, exits, account_rows, sources, checks, available())


def subscriptions(service, sample, source_id, proxies, now):
    source_rows = {s.id: service._source_proxy_rows(s, proxies if s.id == source_id else s.proxies)
                   for s in sample.sources}
    result = {}
    for sub in sample.subscriptions.values():
        rows = [dict(r) for r in sample.account_rows[sub.id]]
        rows.extend(dict(r) for i, group in source_rows.items() if i in sub.source_ids for r in group)
        if uses_checks(sub):
            service._apply_checks(rows, now, cached=sample.checks, kernel=sample.kernel)
        result[sub.id] = service._filter(rows, sub.exclude_failed, sub.max_latency, sub.max_per_country, sub.dedupe)
    return result


def hop_catalog(service, sample, rows):
    catalog = {}
    for i, group in rows.items():
        enabled = sample.subscriptions[i].enabled
        catalog["subscription", i] = {proxy_key(r["proxy"]): r["proxy"] for r in group} if enabled else {}
    for entry in sample.entries.values():
        if entry.enabled and catalog.get(("subscription", entry.subscription_id)):
            catalog["entry", entry.id] = {k: r["proxy"] for k, r in
                                          service.chains.entry_candidates(entry, rows[entry.subscription_id])}
    catalog.update({("proxy", i): nodes for i, nodes in sample.exits.items()})
    return catalog


def path_state(path, catalog, legacy):
    hops = []
    for hop in path["hops"]:
        nodes = catalog.get((hop["source"], hop["ref"]), {})
        key = hop.get("node_key")
        if legacy and hop["source"] == "entry" and path["strategy"] == "fixed" and not key:
            nodes = {}
        elif key and not (legacy and path["strategy"] != "fixed"):
            nodes = {key: nodes[key]} if key in nodes else {}
        hops.append(nodes.keys())
    # Ordered fallback may intentionally use only a subset of the pool.
    if path["strategy"] == "fallback":
        hops[0] = set(path["ordered_keys"]).intersection(hops[0])
    if not legacy:
        # A fixed tail fingerprint stays reserved even when its source no longer supplies it.
        tail_keys = {k for keys in hops[1:] for k in keys}
        tail_keys.update(h["node_key"] for h in path["hops"][1:] if h.get("node_key"))
        hops[0] = hops[0] - tail_keys
    complete = all(hops)
    if complete and not legacy:
        resolved = [([(k, catalog[h["source"], h["ref"]][k]) for k in keys], "")
                    for h, keys in zip(path["hops"], hops)]
        try:
            candidates, _ = path_candidates(path, resolved, strict=False)
            hops[0] = {k for k, _ in candidates}
        except ChainError:
            complete = False
    return complete, hops


def candidate_change(before, after):
    if not after:
        return "missing"
    if not before:
        return "restored"
    if len(after) < len(before):
        return "reduced"
    if len(after) > len(before):
        return "expanded"
    return "replaced"


def compare_chain(chain, old, new):
    paths, lost, valid = [], [], 0
    configured = effective_paths(chain)
    for p, path in enumerate(configured):
        was, before = path_state(path, old, not chain.paths)
        now, after = path_state(path, new, not chain.paths)
        valid += now
        if was and not now:
            lost.append(p)
        hops = []
        for h, (a, b) in enumerate(zip(before, after)):
            if a == b:
                continue
            hops.append(dict(hop_index=h, reason=candidate_change(a, b), before=len(a), after=len(b)))
        if hops or was != now:
            paths.append(dict(path_index=p, before=was, after=now, hops=hops))
    if not paths:
        return None
    if lost and not valid:
        level = "all_lost"
    elif 0 in lost:
        level = "primary_lost"
    elif lost:
        level = "backup_lost"
    elif any(not p["before"] and p["after"] for p in paths):
        level = "restored"
    else:
        level = "candidates"
    return dict(id=chain.id, revision=chain.revision, enabled=chain.enabled, level=level,
                complete_paths=valid, total_paths=len(configured), paths=paths)


def analyze(service, sample, source_id, previous, current, now, version):
    previous_keys, current_keys = source_keys(previous), source_keys(current)
    report = dict(version=version, analyzed_at=now, added=len(current_keys - previous_keys),
                  removed=len(previous_keys - current_keys), subscriptions=[], chains=[])
    if sample is None:
        return report
    before = subscriptions(service, sample, source_id, previous, now)
    after = subscriptions(service, sample, source_id, current, now)
    for sub_id in sorted(sample.affected):
        old, new = ({proxy_key(r["proxy"]) for r in rows[sub_id]} for rows in (before, after))
        if old != new:
            report["subscriptions"].append(dict(id=sub_id, enabled=sample.subscriptions[sub_id].enabled,
                                                added=len(new - old), removed=len(old - new)))
    old, new = (hop_catalog(service, sample, rows) for rows in (before, after))
    for chain in sample.chains:
        change = compare_chain(chain, old, new)
        if change:
            report["chains"].append(change)
    return report
