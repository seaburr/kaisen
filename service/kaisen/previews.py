"""Live previews: publish a bundle, deploy the generic runtime image on PicoPaaS, check that it answers."""
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import config, export
from .builder import ToolError
from .models import Bundle, Project, ProjectFile, utcnow
from .picopaas import AppInfo, PicoPaasError
from .redact import redact
from .settings_store import RuntimeSettings

NOT_YET = (404, 502, 503, 504)  # what the edge answers while the app is not up yet
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_BUNDLE_URL = re.compile(r"/bundles/[A-Za-z0-9_-]{20,}\.tar\.gz")


class PreviewError(Exception):
    """Plain-language message, safe to show."""


@dataclass
class CheckResult:
    ok: bool
    text: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def app_name(p: Project) -> str:
    return "kaisen-" + p.slug[:50].strip("-")


def ensure_configured(rs: RuntimeSettings) -> None:
    if not rs.preview_enabled:
        raise PreviewError("PicoPaaS is not configured (Settings > PicoPaaS: add an API key).")
    if not rs.public_url:
        raise PreviewError("Kaisen's public URL is not set (Settings > PicoPaaS), so the preview cannot download its files.")
    if not config.runtime_image():
        raise PreviewError("The runtime image is not configured (KAISEN_RUNTIME_IMAGE).")


def make_bundle(s: Session, p: Project, public_url: str) -> tuple[str, str]:
    """Store a fresh bundle, keep only the latest few for the project. Returns (bundle URL, sha256)."""
    files = [(r.path, r.content) for r in s.scalars(select(ProjectFile).where(ProjectFile.project_id == p.id))]
    if not files:
        raise PreviewError("The project has no files yet.")
    data = export.build_tarball(p.slug, files)
    token = secrets.token_urlsafe(32)
    sha = hashlib.sha256(data).hexdigest()
    s.add(Bundle(project_id=p.id, token_hash=token_hash(token), sha256=sha, data=data))
    s.flush()
    keep = s.scalars(select(Bundle.id).where(Bundle.project_id == p.id).order_by(Bundle.id.desc())
                     .limit(config.BUNDLES_KEPT)).all()
    s.execute(delete(Bundle).where(Bundle.project_id == p.id, Bundle.id.not_in(keep)))
    return f"{public_url.rstrip('/')}/bundles/{token}.tar.gz", sha


# A project that declares {"database": "mysql"} in kaisen.json gets its own PicoPaaS MySQL database (50 MB, TLS) and
# these env vars. PicoPaaS cannot remove a database from an app later, so this is one-way for that preview.
DB_ENV = {
    "DB_HOST": "${{ db.host }}", "DB_PORT": "${{ db.port }}", "DB_NAME": "${{ db.name }}", "DB_USER": "${{ db.user }}",
    "DB_PASSWORD": "${{ db.password }}", "DB_CA_CERT": "${{ db.ca_cert }}", "DATABASE_URL": "${{ db.url }}",
}
DATABASES = ("mysql",)


def wanted_database(s: Session, p: Project) -> str | None:
    """The database kaisen.json asks for, or None. Raises PreviewError for an unsupported value."""
    row = s.scalars(select(ProjectFile).where(ProjectFile.project_id == p.id, ProjectFile.path == "kaisen.json")).first()
    try:
        db = (json.loads(row.content) if row else {}).get("database")
    except (ValueError, AttributeError):
        return None  # a broken manifest is reported by the runtime at boot, where the builder can read it
    if db in (None, "", False):
        return None
    if db not in DATABASES:
        raise PreviewError(f'kaisen.json "database" must be one of {", ".join(DATABASES)} (got {db!r})')
    return db


def spec_yaml(name: str, image: str, url: str, sha: str, database: str | None = None) -> str:
    q = json.dumps  # JSON strings are valid YAML scalars
    out = f"apiVersion: picopaas/v1\nname: {q(name)}\nimage: {q(image)}\nport: 8080\nhealthCheck:\n  enabled: false\n"
    if database:
        out += f"resources:\n  db: true\n  db_engine: {database}\n"
    out += f"env:\n  KAISEN_BUNDLE_URL: {q(url)}\n  KAISEN_BUNDLE_SHA256: {q(sha)}\n"
    if database:
        out += "".join(f"  {k}: {q(v)}\n" for k, v in DB_ENV.items())
    return out


def deploy(s: Session, p: Project, rs: RuntimeSettings, client) -> AppInfo:
    ensure_configured(rs)
    database = wanted_database(s, p)
    url, sha = make_bundle(s, p, rs.public_url)
    try:
        info = client.apply_spec(spec_yaml(app_name(p), config.runtime_image(), url, sha, database),
                                 rs.picopaas_project or None)
    except PicoPaasError as e:
        raise PreviewError(str(e)) from None
    p.app_id, p.app_hostname, p.deploy_status, p.last_deploy = info.id, info.hostname, info.status, utcnow()
    s.flush()
    return info


def sanitize(text: str, secrets_) -> str:
    text = _CONTROL.sub("", text.replace("\r", "\n"))
    return redact(_BUNDLE_URL.sub("/bundles/[REDACTED].tar.gz", text), secrets_)


def check(s: Session, p: Project, rs: RuntimeSettings, client, wait_s: float,
          clock=time.monotonic, sleep=time.sleep) -> CheckResult:
    """Poll until the app is running and answers HTTP (or failed, or wait_s passed). Text is safe to show a model."""
    if not p.app_id:
        raise PreviewError("Not deployed yet.")
    deadline = clock() + wait_s
    status, code = p.deploy_status or "unknown", None
    host = p.app_hostname or ""
    while True:
        try:
            info = client.get_app(p.app_id)
        except PicoPaasError as e:
            raise PreviewError(str(e)) from None
        status, host = info.status, info.hostname or host
        p.deploy_status, p.app_hostname = status, host
        if status == "failed":
            break
        if status == "running" and host:
            code = client.probe(f"https://{host}")
            if code is not None and code not in NOT_YET:
                break
        if clock() >= deadline:
            break
        sleep(5)
    try:
        logs = client.logs(p.app_id, 60)
    except PicoPaasError:
        logs = "(logs unavailable)"
    s.flush()
    ok = status == "running" and code is not None and 200 <= code < 300
    head = (f"Result: {'OK' if ok else 'NOT OK'}\nstatus: {status}\nurl: {'https://' + host if host else '(none yet)'}\n"
            f"http: {code if code is not None else 'no answer yet'}\nlast log lines:\n")
    sec = rs.secret_values()
    head = sanitize(head, sec)
    room = config.PREVIEW_RESULT_BYTES - len(head.encode())
    body = sanitize(logs, sec).encode()
    if len(body) > room:
        body = b"...[truncated]\n" + body[-(room - 15):]
    return CheckResult(ok, head + body.decode(errors="ignore"))


def run_preview(s: Session, p: Project, rs: RuntimeSettings, factory, wait_s: float,
                clock=time.monotonic, sleep=time.sleep) -> CheckResult:
    """Deploy the current files, commit, then wait and check. Raises PreviewError."""
    ensure_configured(rs)
    client = factory(rs.picopaas_url, rs.picopaas_key)
    deploy(s, p, rs, client)
    s.commit()  # do not hold a transaction open while waiting
    return check(s, p, rs, client, wait_s, clock, sleep)


def make_deployer(factory, wait_s: float, clock=time.monotonic, sleep=time.sleep):
    """The builder's deploy_preview tool: (session, project_id) -> (ok, text). Raises builder.ToolError."""
    def _deployer(s: Session, project_id: int):
        from . import settings_store
        try:
            r = run_preview(s, s.get(Project, project_id), settings_store.load(s), factory, wait_s, clock, sleep)
        except PreviewError as e:
            raise ToolError(str(e)) from None
        return r.ok, r.text
    return _deployer
