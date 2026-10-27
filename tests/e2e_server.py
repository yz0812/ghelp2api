"""Isolated demonstration server for browser tests; never contacts GHelper."""
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import Settings
from app.main import create_app
from tests.fixtures import FakeGateway

directory = tempfile.TemporaryDirectory(prefix="ghelper-e2e-")
settings = Settings(_env_file=None, admin_username="admin", admin_password="test-password-12345",
                    admin_api_key="e2e-synthetic-api-key-00000000000000000000",
                    encryption_key=Fernet.generate_key().decode(), data_dir=Path(directory.name),
                    public_base_url="http://127.0.0.1:18765", scheduler_enabled=False)
app = create_app(settings)
app.state.service.gateway = FakeGateway()


async def fake_probe(server, port):
    return 36.5


app.state.service.probe_node = fake_probe
