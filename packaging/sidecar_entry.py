"""PyInstaller entry point for the localhost-only desktop sidecar."""

import uvicorn

from career_radar.config import default_data_dir
from career_radar.logging_config import configure_local_logging

if __name__ == "__main__":
    configure_local_logging(default_data_dir())
    uvicorn.run("career_radar.web:app", host="127.0.0.1", port=8000, reload=False)
