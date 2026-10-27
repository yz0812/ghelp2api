import hashlib
import re

import yaml


def render(rows: list[dict], *, full: bool = True) -> str:
    if not rows:
        raise ValueError("没有可导出的节点")
    proxies = {}
    groups: dict[str, list[str]] = {}
    for row in sorted(rows, key=lambda r: (r["account_id"], r["region"], r["server"], r["port"])):
        key = (row["server"], row["port"])
        alias = re.sub(r"[\x00-\x1f\x7f]", "", row["account_name"])[:40]
        account_group = f"账号 · {alias} · {row['account_id'][:8]}"
        if key not in proxies:
            suffix = hashlib.sha256(f"{key[0]}:{key[1]}".encode()).hexdigest()[:10]
            name = f"{alias}-{row['account_id'][:8]}-{row['region']}-{suffix}"
            proxies[key] = {"name": name, "type": "http", "server": key[0], "port": key[1],
                            "tls": True, "skip-cert-verify": True, "udp": False}
        groups.setdefault(account_group, []).append(proxies[key]["name"])
    config = {"proxies": list(proxies.values())}
    if full:
        names = [p["name"] for p in proxies.values()]
        auto = {"type": "url-test", "url": "https://www.gstatic.com/generate_204",
                "interval": 300, "tolerance": 100}
        config = {"mixed-port": 7891, "allow-lan": False, "mode": "rule", "log-level": "info",
                  "ipv6": False, "external-controller": "127.0.0.1:9091",
                  "dns": {"enable": True, "ipv6": False, "enhanced-mode": "fake-ip",
                          "fake-ip-range": "198.18.0.1/16", "fake-ip-filter": ["*.lan", "*.local"],
                          "nameserver": ["223.5.5.5", "119.29.29.29"]}, **config,
                  "proxy-groups": [{"name": "节点选择", "type": "select",
                                    "proxies": ["自动选择", *groups, "DIRECT"]},
                                   {"name": "自动选择", **auto, "proxies": names},
                                   *[{"name": k, **auto, "proxies": list(dict.fromkeys(v))}
                                     for k, v in groups.items()]],
                  "rules": ["IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
                            "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
                            "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
                            "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
                            "DOMAIN-SUFFIX,cn,DIRECT", "DOMAIN-SUFFIX,baidu.com,DIRECT",
                            "DOMAIN-SUFFIX,qq.com,DIRECT", "DOMAIN-SUFFIX,taobao.com,DIRECT",
                            "DOMAIN-SUFFIX,jd.com,DIRECT", "DOMAIN-SUFFIX,bilibili.com,DIRECT",
                            "GEOIP,CN,DIRECT", "MATCH,节点选择"]}
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
