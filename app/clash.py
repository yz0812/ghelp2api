import yaml


def converted_proxies(nodes, template):
    used = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL", "节点选择", "自动选择", "全部节点"}
    if template == "enhanced":
        used.update(("Apple", "Google", "广告拦截"))
    proxies = []
    for node in nodes:
        base = name = node["name"]
        suffix = 1
        while name in used:
            suffix += 1
            name = f"{base} ({suffix})"
        used.add(name)
        proxies.append(dict(node, name=name))
    return proxies


def render(proxies: list[dict], groups: dict[str, list[str]], *, full: bool = True,
           chain_groups=None, chain_targets=(), targets=None, converter_template=None) -> str:
    """按已合并、去重、命名好的节点与分组生成 Clash 配置。"""
    if not proxies:
        raise ValueError("没有可导出的节点")
    config = {"proxies": proxies}
    if full:
        names = targets if targets is not None else [p["name"] for p in proxies]
        auto = {"type": "url-test", "url": "https://www.gstatic.com/generate_204",
                "interval": 300, "tolerance": 100}
        config = {"mixed-port": 7891, "allow-lan": False, "mode": "rule", "log-level": "info",
                  "ipv6": False, "external-controller": "127.0.0.1:9091",
                  "dns": {"enable": True, "ipv6": False, "enhanced-mode": "fake-ip",
                          "fake-ip-range": "198.18.0.1/16", "fake-ip-filter": ["*.lan", "*.local"],
                          "nameserver": ["223.5.5.5", "119.29.29.29"]}, **config,
                  "proxy-groups": [{"name": "节点选择", "type": "select",
                                    "proxies": ["自动选择", *groups, *chain_targets, "DIRECT"]},
                                   {"name": "自动选择", **auto, "proxies": names},
                                   *[{"name": k, **auto, "proxies": list(dict.fromkeys(v))}
                                     for k, v in groups.items()], *(chain_groups or [])],
                  "rules": ["IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
                            "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
                            "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
                            "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
                            "DOMAIN-SUFFIX,cn,DIRECT", "DOMAIN-SUFFIX,baidu.com,DIRECT",
                            "DOMAIN-SUFFIX,qq.com,DIRECT", "DOMAIN-SUFFIX,taobao.com,DIRECT",
                            "DOMAIN-SUFFIX,jd.com,DIRECT", "DOMAIN-SUFFIX,bilibili.com,DIRECT",
                            "GEOIP,CN,DIRECT", "MATCH,节点选择"]}
    if full and converter_template:
        config["proxy-groups"][0]["proxies"] = ["自动选择", *names, "DIRECT"]
        if converter_template == "enhanced":
            for name, choices in (("Apple", ["DIRECT", "节点选择"]), ("Google", ["节点选择", "DIRECT"]),
                                  ("广告拦截", ["REJECT", "DIRECT"])):
                config["proxy-groups"].append({"name": name, "type": "select", "proxies": choices})
            providers = {"private": "DIRECT", "reject": "广告拦截", "icloud": "Apple", "apple": "Apple",
                         "google": "Google", "proxy": "节点选择", "direct": "DIRECT"}
            config["rule-providers"] = {
                key: {"type": "http", "behavior": "domain", "interval": 86400,
                      "path": f"./ruleset/{key}.yaml",
                      "url": f"https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/{key}.txt"}
                for key in providers
            }
            config["rules"] = [f"RULE-SET,{key},{target}" for key, target in providers.items()] + config["rules"]
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
