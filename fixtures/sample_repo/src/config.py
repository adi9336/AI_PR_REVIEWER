"""Configuration management for the sample repo.

Centralizes configuration loading from environment variables and files.
Uses a simple dataclass pattern.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    name: str = "myapp"
    user: str = "postgres"
    password: str = ""


@dataclass
class AppConfig:
    database: DatabaseConfig
    debug: bool = False
    secret_key: str = "change-me-in-production"


def load_config() -> AppConfig:
    """Load application configuration from environment variables."""
    db = DatabaseConfig(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        name=os.getenv("DB_NAME", "myapp"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )
    return AppConfig(
        database=db,
        debug=os.getenv("DEBUG", "false").lower() == "true",
        secret_key=os.getenv("SECRET_KEY", "change-me-in-production"),
    )