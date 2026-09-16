from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from .database import Database
from .local_settings import LocalSettingsStore


def export_archive(database: Database, data_dir: Path, public_settings: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest = {
            "format": "career-radar-local-export-v1",
            "exported_at": datetime.now(UTC).isoformat(),
            "settings": public_settings,
            "note": "模型密钥不包含在导出中。",
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("data.json", json.dumps(database.export_all_data(), ensure_ascii=False, indent=2))
        for folder in ("exports", "logs"):
            root = data_dir / folder
            if root.is_dir():
                for path in root.rglob("*"):
                    if path.is_file():
                        archive.write(path, str(Path(folder) / path.relative_to(root)))
    return buffer.getvalue()


def delete_local_data(database: Database, data_dir: Path, settings: LocalSettingsStore) -> None:
    database.delete_all_data()
    for folder in ("exports", "logs"):
        path = data_dir / folder
        if path.is_dir():
            shutil.rmtree(path)
    settings.secret_store.delete()
    settings.settings.api_key = ""
    settings.path.unlink(missing_ok=True)
