import hashlib
import hmac

from cryptography.fernet import Fernet


class Secrets:
    def __init__(self, key: str):
        self.box = Fernet(key.encode())
        self.key = key.encode()

    def encrypt(self, value: str) -> str:
        return self.box.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.box.decrypt(value.encode()).decode()

    def fingerprint(self, value: str) -> str:
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hint(value: str) -> str:
    return value[:4] + "••••" + value[-4:]
