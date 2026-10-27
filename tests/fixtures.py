PAC = '''var proxy="HTTPS aabbcc01-abc123-tp1cgt-w7lw.hkt.example.com:443; HTTPS aabbcc02-abc123-tp1cgt-w7lw.los.example.com:8443";
var rule_list=[{t:"HTTPS aabbcc03-abc123-tp1cgt-w7lw.gzcm1.example.com:15228"},
{t:"HTTPS aabbcc01-abc123-tp1cgt-w7lw.hkt.example.com:443"},
{t:"HTTPS oldnode-zzz999-tp1cgt-w7lw.de.example.com:443"}];'''


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
        return {"total": 1024}
