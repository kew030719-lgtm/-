"""Non-secret desktop settings and model-key storage.

The JSON file deliberately excludes the API key. On Windows the key is stored
through Credential Manager via ``keyring``; tests and source checkouts use an
in-process store unless an environment key was already supplied.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from typing import Protocol

from .config import Settings

SERVICE_NAME = "CareerRadar"
KEY_ACCOUNT = "model-api-key"


class SecretStore(Protocol):
    def get(self) -> str: ...
    def set(self, value: str) -> None: ...
    def delete(self) -> None: ...


class MemorySecretStore:
    def __init__(self, initial: str = ""):
        self.value = initial

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value

    def delete(self) -> None:
        self.value = ""


class KeyringSecretStore:
    def __init__(self) -> None:
        import keyring
        self.keyring = keyring
        self.delete_error = keyring.errors.PasswordDeleteError

    def get(self) -> str:
        return self.keyring.get_password(SERVICE_NAME, KEY_ACCOUNT) or ""

    def set(self, value: str) -> None:
        self.keyring.set_password(SERVICE_NAME, KEY_ACCOUNT, value)

    def delete(self) -> None:
        try:
            self.keyring.delete_password(SERVICE_NAME, KEY_ACCOUNT)
        except self.delete_error:
            return


def default_secret_store(initial: str = "") -> SecretStore:
    if sys.platform == "win32":
        try:
            store = KeyringSecretStore()
            if initial and not store.get():
                store.set(initial)
            return store
        except Exception as exc:
            raise RuntimeError("Windows Credential Manager 不可用，拒绝以明文保存模型密钥") from exc
    return MemorySecretStore(initial)


class LocalSettingsStore:
    def __init__(self, settings: Settings, secret_store: SecretStore | None = None):
        self.path = settings.data_dir / "settings.json"
        self.secret_store = secret_store or default_secret_store(settings.api_key)
        self.settings = self._load(settings)

    def _load(self, base: Settings) -> Settings:
        values: dict[str, str] = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                values = {key: str(raw[key]).strip() for key in ("model_base_url", "model_name") if raw.get(key)}
            except (OSError, ValueError, TypeError):
                values = {}
        return replace(base, **values, api_key=self.secret_store.get() or base.api_key)

    def public(self) -> dict[str, object]:
        return {
            "model_base_url": self.settings.model_base_url,
            "model_name": self.settings.model_name,
            "api_key_configured": bool(self.secret_store.get()),
            "data_dir": str(self.settings.data_dir),
        }

    def update(self, *, model_base_url: str, model_name: str, api_key: str | None = None) -> Settings:
        base_url = model_base_url.strip().rstrip("/")
        name = model_name.strip()
        if not base_url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise ValueError("模型服务地址必须使用 HTTPS，或指向本机 localhost")
        if not name:
            raise ValueError("模型名称不能为空")
        if api_key is not None:
            if api_key.strip():
                self.secret_store.set(api_key.strip())
            else:
                self.secret_store.delete()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"model_base_url": base_url, "model_name": name}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.settings.model_base_url = base_url
        self.settings.model_name = name
        self.settings.api_key = self.secret_store.get()
        return self.settings
