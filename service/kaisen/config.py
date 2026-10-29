"""Environment config, model price table and cost math."""
import os
from dataclasses import dataclass

# USD per million tokens
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high")

# Builder limits (BUILDER_V0)
MAX_FILE_BYTES = 200 * 1024
MAX_FILES = 300
MAX_PROJECT_BYTES = 5 * 1024 * 1024
MAX_TURNS = 40
STEP_SECONDS = 15 * 60

# Preview loop
BUNDLES_KEPT = 3
PICOPAAS_DEFAULT_URL = "https://platform.picopaas.xyz"
PREVIEW_WAIT_TOOL = 240  # builder tool and auto-deploy: wait for running + an HTTP answer
PREVIEW_WAIT_UI = 20  # Deploy now / Check status buttons
PREVIEW_RESULT_BYTES = 4096


def runtime_image() -> str:
    """Runtime image for previews. Baked into the Kaisen image at build time; the env var wins."""
    return os.environ.get("KAISEN_RUNTIME_IMAGE", "").strip()


DEV_SECRET = "dev-only-insecure-secret-key"


@dataclass
class Config:
    environment: str
    secret_key: str
    database_url: str
    database_ca_cert: str | None
    worker_enabled: bool

    @property
    def cookie_secure(self) -> bool:
        return self.environment not in ("dev", "test")


_current: Config | None = None


def load() -> Config:
    global _current
    env = os.environ.get("ENVIRONMENT", "prod")
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if env not in ("dev", "test"):
            raise RuntimeError("SECRET_KEY is required when ENVIRONMENT is not dev/test")
        secret = DEV_SECRET
    _current = Config(
        environment=env,
        secret_key=secret,
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./data/kaisen.db"),
        database_ca_cert=os.environ.get("DATABASE_CA_CERT") or None,
        worker_enabled=os.environ.get("WORKER_ENABLED", "1") == "1",
    )
    return _current


def get() -> Config:
    return _current or load()


def cost_usd(model: str, usage: dict) -> float:
    p = PRICES[model]
    return (
        usage.get("input_tokens", 0) * p["in"]
        + usage.get("output_tokens", 0) * p["out"]
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
    ) / 1_000_000
