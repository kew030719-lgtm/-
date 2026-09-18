from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)


def default_data_dir() -> Path:
    """Return the local data directory used by the source/web runtime."""
    override = os.getenv("APP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32" and os.getenv("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "CareerRadar").resolve()
    return (ROOT / "data").resolve()


@dataclass
class Settings:
    data_dir: Path
    database_path: Path
    model_base_url: str
    model_name: str
    api_key: str
    crawl_delay_seconds: float
    crawl_max_jobs: int
    vision_model_name: str = ""
    # Defaults keep existing callers working; both are read per turn, so a
    # dataclass field is enough and no migration is needed.
    chat_history_limit: int = 24
    chat_summarize_after: int = 0

    @classmethod
    def load(cls) -> "Settings":
        data_dir = default_data_dir()
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "career_radar.db",
            model_base_url=os.getenv(
                "MODEL_BASE_URL",
                "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            ).rstrip("/"),
            model_name=os.getenv("MODEL_NAME", "deepseek-v4-pro-0813"),
            api_key=os.getenv("TOKEN_PLAN_API_KEY", "").strip(),
            crawl_delay_seconds=max(0, float(os.getenv("CRAWL_DELAY_SECONDS", "10"))),
            crawl_max_jobs=min(20, max(1, int(os.getenv("CRAWL_MAX_JOBS", "20")))),
            vision_model_name=os.getenv("VISION_MODEL_NAME", "").strip(),
            chat_history_limit=max(1, int(os.getenv("CHAT_HISTORY_LIMIT", "24"))),
            # 0 disables compaction, which is the shipped default: it is an
            # optimisation, not part of the reply contract.
            chat_summarize_after=max(0, int(os.getenv("CHAT_SUMMARIZE_AFTER", "0"))),
        )
