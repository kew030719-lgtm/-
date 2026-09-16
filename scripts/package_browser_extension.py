#!/usr/bin/env python3
"""Build the exact ZIP uploaded to the Chrome Web Store."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "browser-extension"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "career-radar-browser-helper.zip")
    args = parser.parse_args()
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest["manifest_version"]) != 3:
        raise SystemExit("Chrome Web Store 发布包必须使用 Manifest V3")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SOURCE.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    print(f"{args.output} · version {manifest['version']}")


if __name__ == "__main__":
    main()
