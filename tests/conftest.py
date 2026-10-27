import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fixtures import FakeGateway


@pytest.fixture(autouse=True)
def no_mihomo(monkeypatch):
    """默认关掉内核测速：单元测试只验证逻辑，不去碰真实网络和本机内核。"""
    monkeypatch.setenv("MIHOMO_BIN", "off")


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, admin_password="test-password-12345", admin_api_key="x" * 40,
                    encryption_key=Fernet.generate_key().decode(), data_dir=tmp_path,
                    scheduler_enabled=False, frontend_dir=tmp_path / "no-frontend")


@pytest.fixture
def client(settings):
    app = create_app(settings)
    original = app.state.service.gateway
    app.state.service.gateway = FakeGateway()
    with TestClient(app) as client:
        client.portal.call(original.close)
        client.headers["Authorization"] = f"Bearer {settings.admin_api_key}"
        yield client


@pytest.fixture
def account_id(client):
    return client.post("/api/v1/accounts", json={"name": "测试账号", "token": "a" * 32}).json()["id"]
