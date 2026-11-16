PAC = '''var proxy="HTTPS aabbcc01-abc123-tp1cgt-w7lw.hkt.example.com:443; HTTPS aabbcc02-abc123-tp1cgt-w7lw.los.example.com:8443";
var rule_list=[{t:"HTTPS aabbcc03-abc123-tp1cgt-w7lw.gzcm1.example.com:15228"},
{t:"HTTPS aabbcc01-abc123-tp1cgt-w7lw.hkt.example.com:443"},
{t:"HTTPS oldnode-zzz999-tp1cgt-w7lw.de.example.com:443"}];'''

# 2026-11 上游把域名中段从 tnqw00-tp1cgt 换成 tomdc0-ttrpst：
# 主列表只放 2 条默认线路，其余节点散落在 rule_list 里。
PAC_NEW_MARKER = '''var proxy="HTTPS 47d30f93-tomdc0-ttrpst-w7lw.viphuawei.com:443; HTTPS 24438e68-tomdc0-ttrpst-w7lw.sj11.cdnkuaishou.com:443";
var rule_list=[{t:"HTTPS c9a413e5-tomdc0-ttrpst-1tuas.hk.cdnkuaishou.com:443"},
{t:"HTTPS dfaec059-tomdc0-ttrpst-w7lw.gzcm1.cmhuawei.com:2340"},
{t:"HTTPS 99999999-old000-tp1cgt-w7lw.de.example.com:443"}];'''

# 同一批上游里不同账号拿到的标记并不一致：第四段会出现 1tuas 而不是 w7lw，
# 且别的账号的节点（ttrpst）不能混进来。
PAC_ALT_MARKER = '''var proxy="HTTPS fad253c7-tomdc0-tqqfg1-1tuas.los.cdnkuaishou.com:443";
var rule_list=[{t:"HTTPS 3b1f9c02-tomdc0-tqqfg1-1tuas.hk.cdnkuaishou.com:443"},
{t:"HTTPS 47d30f93-tomdc0-ttrpst-w7lw.viphuawei.com:443"}];'''


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.clear = False
        self.rotate = None
        self.info_fail = False
        self.pk = "synthetic-session-1"

    async def close(self):
        pass

    async def request(self, token, path, **kwargs):
        from app.upstream import UpstreamError
        self.calls.append((token, path))
        if self.clear:
            return {"clear_token": True}
        if self.fail:
            raise UpstreamError("模拟上游暂不可用")
        if path == "/session":
            response = {"session": {"proxy": {"pk": self.pk,
                "pac": {"value": {"pacScript": {"data": PAC}}}}}}
            if self.rotate:
                response["set_token"] = self.rotate
            return response
        if self.info_fail:
            raise UpstreamError("模拟资料接口失败")
        if path == "/v3/account":
            return {"account": {"email": "demo@example.invalid", "is_vip": True,
                                "vip_date": "2027-01-01", "token": "must-not-leak"}}
        if path == "/v3/servers/status":
            return {"servers": []}
        return {"total": 1024}
