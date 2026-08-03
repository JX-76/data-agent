"""Configuration management with profiles, environment layers, and hot-reload.

Supports:
- Environment variable overrides (DATA_AGENT_*)
- .env file loading
- Profile-based configs (dev/staging/prod)
- Hot-reload via file watcher
- Typed config with validation

Usage:
    from config_manager import Config, load_config
    cfg = load_config()
    print(cfg.server.port)  # typed int
    cfg.watch()  # Start hot-reload watcher
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

# ── Config Models ──


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    timeout_seconds: int = 30


@dataclass
class AuthConfig:
    enabled: bool = True
    api_keys: list[str] = field(default_factory=list)


@dataclass
class CacheConfig:
    enabled: bool = True
    max_entries: int = 500
    default_ttl_seconds: int = 300


@dataclass
class RateLimitConfig:
    enabled: bool = True
    max_requests_per_minute: int = 100
    block_seconds: int = 30


@dataclass
class LLMConfig:
    api_base: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    router_model: str = "deepseek-chat"
    max_tokens: int = 512
    temperature: float = 0.0


@dataclass
class DatabaseConfig:
    type: str = "sqlite"  # sqlite | postgresql | mysql | duckdb
    path: str = ":memory:"
    host: str = ""
    port: int = 5432
    name: str = ""
    user: str = ""
    password: str = ""
    pool_min: int = 1
    pool_max: int = 5
    pool_timeout: float = 5.0


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"  # json | text
    output: str = "stdout"  # stdout | file


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # Hot-reload
    _watcher: Optional[threading.Thread] = None
    _on_reload: list[Callable] = field(default_factory=list)
    _sources: list[str] = field(default_factory=list)

    def on_reload(self, callback: Callable):
        """Register a callback for hot-reload events."""
        self._on_reload.append(callback)

    def watch(self, interval_seconds: float = 5.0):
        """Start a background thread that watches config sources for changes."""
        if self._watcher and self._watcher.is_alive():
            return

        def _watch_loop():
            last_mtimes = {}
            while True:
                try:
                    changed = False
                    for src in self._sources:
                        p = Path(src)
                        if p.exists():
                            mtime = p.stat().st_mtime
                            if src in last_mtimes and mtime > last_mtimes[src]:
                                changed = True
                            last_mtimes[src] = mtime

                    if changed:
                        reloaded = load_config()
                        # Copy fields
                        for field_name in self.__dataclass_fields__:
                            if field_name.startswith("_"):
                                continue
                            setattr(self, field_name, getattr(reloaded, field_name))

                        # Notify callbacks
                        for cb in self._on_reload:
                            try:
                                cb()
                            except Exception as e:
                                logger.warning("bare_exception_caught", error=str(e))
                                pass
                except Exception as e:
                    logger.warning("bare_exception_caught", error=str(e))
                    pass
                time.sleep(interval_seconds)

        self._watcher = threading.Thread(target=_watch_loop, daemon=True)
        self._watcher.start()

    def to_dict(self) -> dict:
        """Export config as dict (safe for logging, masks secrets)."""
        d = {}
        for fn in self.__dataclass_fields__:
            if fn.startswith("_"):
                continue
            obj = getattr(self, fn)
            if hasattr(obj, "__dataclass_fields__"):
                d[fn] = {k: ("***" if "key" in k or "password" in k else v)
                         for k, v in obj.__dict__.items()}
            else:
                d[fn] = obj
        return d


# ── Profile Defs ──

PROFILES = {
    "dev": Config(
        server=ServerConfig(port=8000),
        auth=AuthConfig(enabled=False),
        cache=CacheConfig(max_entries=100, default_ttl_seconds=60),
        rate_limit=RateLimitConfig(enabled=False),
        logging=LoggingConfig(level="DEBUG", format="text"),
    ),
    "staging": Config(
        server=ServerConfig(port=8000),
        auth=AuthConfig(enabled=True),
        cache=CacheConfig(max_entries=500, default_ttl_seconds=300),
        rate_limit=RateLimitConfig(enabled=True, max_requests_per_minute=200),
        logging=LoggingConfig(level="INFO", format="json"),
    ),
    "prod": Config(
        server=ServerConfig(port=8000, workers=4, timeout_seconds=60),
        auth=AuthConfig(enabled=True),
        cache=CacheConfig(max_entries=2000, default_ttl_seconds=600),
        rate_limit=RateLimitConfig(enabled=True, max_requests_per_minute=100, block_seconds=60),
        logging=LoggingConfig(level="WARNING", format="json"),
    ),
}


# ── Loaders ──

def _env_override(config: Config):
    """Apply environment variable overrides (DATA_AGENT_*)."""
    env_map = {
        "DATA_AGENT_HOST": ("server", "host", str),
        "DATA_AGENT_PORT": ("server", "port", int),
        "DATA_AGENT_AUTH": ("auth", "enabled", lambda v: v.lower() in ("true", "1", "yes")),
        "DATA_AGENT_API_KEY": ("auth", "api_keys", lambda v: [k.strip() for k in v.split(",") if k.strip()]),
        "DATA_AGENT_CACHE_SIZE": ("cache", "max_entries", int),
        "DATA_AGENT_CACHE_TTL": ("cache", "default_ttl_seconds", int),
        "DATA_AGENT_RATE_LIMIT": ("rate_limit", "max_requests_per_minute", int),
        "DATA_AGENT_DB_TYPE": ("database", "type", str),
        "DATA_AGENT_DB_PATH": ("database", "path", str),
        "DATA_AGENT_DB_HOST": ("database", "host", str),
        "DATA_AGENT_DB_PORT": ("database", "port", int),
        "DATA_AGENT_DB_NAME": ("database", "name", str),
        "DATA_AGENT_DB_USER": ("database", "user", str),
        "DATA_AGENT_DB_PASSWORD": ("database", "password", str),
        "DATA_AGENT_DB_POOL_MAX": ("database", "pool_max", int),
        "DATA_AGENT_LOG_LEVEL": ("logging", "level", str),
        "DEEPSEEK_API_KEY": ("llm", "api_key", str),
        "DEEPSEEK_BASE_URL": ("llm", "api_base", str),
    }

    for env_var, (section, field, converter) in env_map.items():
        val = os.environ.get(env_var)
        if val:
            section_obj = getattr(config, section)
            setattr(section_obj, field, converter(val))


def _load_env_file(config: Config, path: Optional[Path] = None):
    """Load settings from .env file."""
    env_path = path or (Path(__file__).resolve().parents[1] / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def load_config(profile: Optional[str] = None) -> Config:
    """Load config from profile + environment.

    Priority: defaults < profile < .env file < environment variables

    Args:
        profile: Profile name (dev/staging/prod). If None, reads DATA_AGENT_PROFILE env.
    """
    # Determine profile
    if profile is None:
        profile = os.environ.get("DATA_AGENT_PROFILE", "dev")

    # Start with profile
    config = PROFILES.get(profile, PROFILES["dev"])

    # Make a clean copy (dataclass fields are NOT deep-copied by default)
    config = Config(
        server=ServerConfig(**config.server.__dict__),
        auth=AuthConfig(**config.auth.__dict__),
        cache=CacheConfig(**config.cache.__dict__),
        rate_limit=RateLimitConfig(**config.rate_limit.__dict__),
        llm=LLMConfig(**config.llm.__dict__),
        database=DatabaseConfig(**config.database.__dict__),
        logging=LoggingConfig(**config.logging.__dict__),
    )

    # Layer 1: .env file
    env_path = Path(__file__).resolve().parents[1] / ".env"
    _load_env_file(config, env_path)

    # Layer 2: Environment variables
    _env_override(config)

    # Track sources for hot-reload
    config._sources = [str(env_path)] if env_path.exists() else []

    return config


# ── Global singleton ──

_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create the global config singleton."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config():
    """Force reload config from sources."""
    global _config
    _config = load_config()
    return _config
