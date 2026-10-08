"""Paths, configuration and secrets.

Secrets come from `.env` at the repo root (gitignored) or the process environment.
Nothing else in the codebase reads keys directly: import `settings` from here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
GOLD_DIR = DATA_DIR / "gold"
LLM_CACHE_DIR = DATA_DIR / "llm_cache"
MANIFEST_PATH = DATA_DIR / "manifest.json"
REPORTS_DIR = REPO_ROOT / "reports"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr | None = None
    gee_service_account_json: Path | None = None
    gee_project: str | None = None
    hf_token: SecretStr | None = None
    zenodo_token: SecretStr | None = None


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def sources() -> dict[str, dict[str, Any]]:
    return load_yaml("sources.yaml")["sources"]


def aoi() -> dict[str, Any]:
    return load_yaml("aoi.yaml")
