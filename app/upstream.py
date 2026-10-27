import uuid

import httpx


class UpstreamError(Exception):
    pass


class InvalidToken(UpstreamError):
    pass


class Gateway:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False)
        self.preferred = None

    async def close(self):
        await self.client.aclose()

    async def request(self, token: str, path: str, *, pk=None, query=None, body=None):
        hosts = [h.strip() for h in self.settings.api_hosts.split(",")]
        if self.preferred in hosts:
            hosts.remove(self.preferred)
            hosts.insert(0, self.preferred)
        params = {"token": token, "version": self.settings.extension_version, "lang": "zh-CN"}
        params.update(query or {})
        if pk:
            params["pk"] = pk
        for host in hosts:
            url = f"https://{uuid.uuid4()}.{host}/api2{path}"
            try:
                async with self.client.stream("POST" if body is not None else "GET", url,
                                              params=params, headers={"token": token}, json=body) as res:
                    if res.status_code == 401:
                        raise InvalidToken("token 已失效，请重新导入")
                    if res.status_code == 429:
                        raise UpstreamError("上游请求过于频繁，稍后自动重试")
                    if res.status_code >= 400 or res.is_redirect:
                        continue
                    data = bytearray()
                    async for chunk in res.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2_000_000:
                            raise UpstreamError("上游响应超过大小限制")
                import json
                obj = json.loads(data)
                if isinstance(obj, dict):
                    self.preferred = host
                    return obj
            except (httpx.HTTPError, ValueError):
                continue
        raise UpstreamError("上游域名均不可用或响应格式错误")
