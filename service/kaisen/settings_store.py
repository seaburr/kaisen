"""Settings in the DB. Secrets are Fernet-encrypted and write-only from the UI."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from . import config, crypto
from .models import Setting

SECRET_KEYS = ("anthropic_api_key", "github_token", "picopaas_key")
PLAIN_KEYS = ("model", "effort", "cap_run", "cap_day", "cap_project", "paused",
              "github_repo", "github_branch", "picopaas_url")


@dataclass
class RuntimeSettings:
    anthropic_api_key: str = ""
    github_token: str = ""
    picopaas_key: str = ""
    model: str = config.DEFAULT_MODEL
    effort: str = "medium"
    cap_run: float = 2.0
    cap_day: float = 8.0
    cap_project: float = 30.0
    paused: bool = False
    github_repo: str = ""
    github_branch: str = ""
    picopaas_url: str = ""

    def secret_values(self) -> list[str]:
        return [self.anthropic_api_key, self.github_token, self.picopaas_key, config.get().secret_key]


def set_value(s: Session, key: str, value: str) -> None:
    secret = key in SECRET_KEYS
    row = s.get(Setting, key) or Setting(key=key)
    row.value = crypto.encrypt(value) if secret else value
    row.secret = secret
    s.add(row)


def load(s: Session) -> RuntimeSettings:
    rs = RuntimeSettings()
    for row in s.query(Setting).all():
        if row.key not in SECRET_KEYS + PLAIN_KEYS:
            continue
        v = crypto.decrypt(row.value) if row.secret and row.value else row.value
        cur = getattr(rs, row.key)
        if isinstance(cur, bool):
            v = v == "1"
        elif isinstance(cur, float):
            v = float(v)
        setattr(rs, row.key, v)
    return rs


def describe_secrets(rs: RuntimeSettings) -> dict[str, str]:
    return {k: (f"set, ends in …{getattr(rs, k)[-4:]}" if getattr(rs, k) else "not set") for k in SECRET_KEYS}


def save_form(s: Session, form) -> None:
    """Validate then store. Blank secret field = keep; 'clear_<key>' removes it."""
    model = form.get("model", config.DEFAULT_MODEL)
    if model not in config.PRICES:
        raise ValueError("Unknown model")
    if form.get("effort", "medium") not in config.EFFORTS:
        raise ValueError("Unknown effort")
    caps = {}
    for k in ("cap_run", "cap_day", "cap_project"):
        try:
            caps[k] = float(form.get(k, ""))
        except ValueError:
            raise ValueError(f"{k} must be a number")
        if caps[k] < 0:
            raise ValueError(f"{k} must be >= 0")
    repo = form.get("github_repo", "").strip()
    if repo and "/" not in repo:
        raise ValueError("GitHub repo must look like owner/name")
    set_value(s, "model", model)
    set_value(s, "effort", form["effort"])
    for k, v in caps.items():
        set_value(s, k, str(v))
    for k in ("github_repo", "github_branch", "picopaas_url"):
        set_value(s, k, form.get(k, "").strip())
    for k in SECRET_KEYS:
        if form.get(f"clear_{k}"):
            set_value(s, k, "")
        elif form.get(k, "").strip():
            set_value(s, k, form[k].strip())
