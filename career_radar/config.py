from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

# Reuse the key the user already configured for the earlier Hermes prototype.
_prototype_env = ROOT.parent / "research-workspace" / "hermes-home" / ".env"
if not os.getenv("TOKEN_PLAN_API_KEY") and _prototype_env.is_file():
    _key = dotenv_values(_prototype_env).get("TOKEN_PLAN_API_KEY")
    if _key:
        os.environ["TOKEN_PLAN_API_KEY"] = _key


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    model_base_url: str
    model_name: str
    api_key: str
    crawl_delay_seconds: float
    crawl_max_jobs: int
    hermes_path: Path

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(os.getenv("APP_DATA_DIR", str(ROOT / "data"))).resolve()
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
            hermes_path=Path(
                os.getenv("HERMES_AGENT_PATH", str(ROOT.parent / "hermes-agent"))
            ).resolve(),
        )

