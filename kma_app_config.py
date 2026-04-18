#!/usr/bin/env python3
"""Configuration helpers for the macOS KMA downloader app."""

from __future__ import annotations

import json
from pathlib import Path

from kma_client import KmaConfigurationError

APP_NAME = "KMA ASOS Downloader"
SETTINGS_TEMPLATE = {
    "auth_key": "YOUR_KMA_AUTH_KEY",
}


def get_app_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def get_settings_path() -> Path:
    return get_app_support_dir() / "settings.json"


def ensure_settings_file() -> Path:
    settings_path = get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if not settings_path.exists():
        settings_path.write_text(
            json.dumps(SETTINGS_TEMPLATE, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    return settings_path


def load_auth_key() -> str:
    settings_path = ensure_settings_file()

    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KmaConfigurationError(f"설정 파일을 읽을 수 없습니다: {settings_path}") from exc

    auth_key = payload.get("auth_key")
    if not isinstance(auth_key, str) or not auth_key.strip() or auth_key == SETTINGS_TEMPLATE["auth_key"]:
        raise KmaConfigurationError(
            "settings.json 에 auth_key 값을 입력해 주세요: "
            f"{settings_path}"
        )

    return auth_key.strip()


def get_default_downloads_dir() -> Path:
    return Path.home() / "Downloads"
