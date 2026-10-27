"""Exercise a running container without using real GHelper credentials."""
import json
import os
import time
import urllib.request


def main():
    base = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000")
    api_key = os.environ["ADMIN_API_KEY"]
    def request(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": "Bearer " + api_key,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)
    for attempt in range(30):
        try:
            assert request("/readyz")["status"] == "ready"
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
    with urllib.request.urlopen(base, timeout=10) as response:
        assert b'<div id="app">' in response.read()
    # Invalid credentials are never sent upstream: scheduler disabled in smoke container.
    result = request("/api/v1/accounts", {"name": "ci-smoke", "token": "f" * 32})
    assert result["status"] in {"created", "duplicate"}
    assert any(a["name"] == "ci-smoke" for a in request("/api/v1/accounts"))
    print("Container API, frontend and persistent account checks passed")


if __name__ == "__main__":
    main()
