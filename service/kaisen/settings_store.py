"""Settings in the DB. Secrets are Fernet-encrypted and write-only from the UI."""
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from . import config, crypto
from .models import Setting

SECRET_KEYS = ("anthropic_api_key", "github_token", "picopaas_key")
PLAIN_KEYS = ("model", "effort", "cap_run", "cap_day", "cap_project", "paused",
              "github_repo", "github_branch", "picopaas_url", "output_mode", "push_on_finish", "open_pr")
OUTPUT_MODES = ("download", "github")
_REPO_RE = re.compile(r"^(?:https?://github\.com/)?([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$")


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
    output_mode: str = "download"  # download | github (download is always available)
    push_on_finish: bool = True
    open_pr: bool = False

    @property
    def push_enabled(self) -> bool:
        return self.output_mode == "github" and bool(self.github_repo and self.github_token)

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
    mode = form.get("output_mode", "download")
    if mode not in OUTPUT_MODES:
        raise ValueError("Unknown output mode")
    url = form.get("github_url", "").strip()
    repo = parse_repo(url) if url else ""
    if url and not repo:
        raise ValueError("GitHub repository must look like https://github.com/owner/repo")
    if mode == "github":
        if not repo:
            raise ValueError("Pushing to GitHub needs the repository URL")
        has_token = bool(form.get("github_token", "").strip()) or (
            bool(load(s).github_token) and not form.get("clear_github_token"))
        if not has_token:
            raise ValueError("Pushing to GitHub needs an access token (see the instructions under the token field)")
    set_value(s, "model", model)
    set_value(s, "effort", form["effort"])
    for k, v in caps.items():
        set_value(s, k, str(v))
    set_value(s, "output_mode", mode)
    set_value(s, "github_repo", repo)
    for k in ("github_branch", "picopaas_url"):
        set_value(s, k, form.get(k, "").strip())
    for k in ("push_on_finish", "open_pr"):
        set_value(s, k, "1" if form.get(k) else "0")
    for k in SECRET_KEYS:
        if form.get(f"clear_{k}"):
            set_value(s, k, "")
        elif form.get(k, "").strip():
            set_value(s, k, form[k].strip())


def parse_repo(text: str) -> str:
    """'https://github.com/owner/repo(.git)' or 'owner/repo' -> 'owner/repo'; '' if it is neither."""
    m = _REPO_RE.match(text.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else ""
