"""PicoPaaS API client. The key is only ever sent as an Authorization header."""
from dataclasses import dataclass, field
from typing import Protocol

import httpx


class PicoPaasError(Exception):
    """Plain-language message, safe to show."""


@dataclass
class AppInfo:
    id: int
    hostname: str
    status: str


@dataclass
class Me:
    email: str
    app_limit: int | None


class PicoPaasClient(Protocol):
    def me(self) -> Me: ...
    def apply_spec(self, spec: str, project: str | None) -> AppInfo: ...
    def get_app(self, app_id: int) -> AppInfo: ...
    def logs(self, app_id: int, tail: int = 60) -> str: ...
    def probe(self, url: str) -> int | None: ...


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail", "")
    except Exception:
        d = ""
    if isinstance(d, list):
        d = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in d)
    return (str(d) or r.text)[:300]


def _plain(r: httpx.Response) -> str:
    code = r.status_code
    if code == 401:
        return "PicoPaaS rejected the API key (401)"
    if code == 403:
        return f"PicoPaaS refused the request (403): {_detail(r)}"
    if code == 404:
        return "PicoPaaS: not found (404)"
    if code == 422:
        return f"PicoPaaS rejected the app spec: {_detail(r)}"
    return f"PicoPaaS error {code}: {_detail(r)}"


class RealPicoPaas:
    def __init__(self, base_url: str, key: str, transport=None):
        self.http = httpx.Client(base_url=base_url.rstrip("/"),  # the API is at the root: /auth/me, /apps/spec
                                 timeout=30, transport=transport,
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": "kaisen"})
        self.probe_transport = transport

    def _req(self, method, path, **kw):
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as e:  # the exception type (ConnectTimeout, ConnectError...) is what tells network from DNS/TLS
            raise PicoPaasError(f"Could not reach PicoPaaS at {self.http.base_url} ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise PicoPaasError(_plain(r))
        return r.json()

    @staticmethod
    def _app(d: dict) -> AppInfo:
        return AppInfo(id=int(d["id"]), hostname=d.get("hostname") or "", status=d.get("status") or "unknown")

    def me(self) -> Me:
        d = self._req("GET", "/auth/me")
        return Me(email=d.get("email", ""), app_limit=d.get("app_limit_effective"))

    def apply_spec(self, spec: str, project: str | None) -> AppInfo:
        body = {"spec": spec}
        if project:
            body["project"] = project
        return self._app(self._req("POST", "/apps/spec", json=body))

    def get_app(self, app_id: int) -> AppInfo:
        return self._app(self._req("GET", f"/apps/{int(app_id)}"))

    def logs(self, app_id: int, tail: int = 60) -> str:
        d = self._req("GET", f"/apps/{int(app_id)}/logs", params={"tail": tail})
        return "\n".join(str(x) for x in d.get("lines", []))

    def probe(self, url: str) -> int | None:
        """HTTP status of the app URL (no redirects, 10 s), or None if it did not answer."""
        try:
            with httpx.Client(timeout=10, follow_redirects=False, transport=self.probe_transport) as c:
                return c.get(url).status_code
        except httpx.HTTPError:
            return None


@dataclass
class FakePicoPaas:
    """Scripted stand-in. `statuses` is consumed one per get_app call (the last one repeats)."""
    email: str = "owner@example.test"
    app_limit: int | None = 3
    hostname: str = "kaisen-demo.picopaas.test"
    statuses: list = field(default_factory=lambda: ["running"])
    http_codes: list = field(default_factory=lambda: [200])  # consumed per probe (None = no answer); last repeats
    log_text: str = "[kaisen-runtime] phase 4/4: starting"
    apply_error: str | None = None
    auth_error: str | None = None
    applied: list = field(default_factory=list)
    probes: list = field(default_factory=list)

    def _check_auth(self):
        if self.auth_error:
            raise PicoPaasError(self.auth_error)

    def me(self) -> Me:
        self._check_auth()
        return Me(self.email, self.app_limit)

    def apply_spec(self, spec: str, project: str | None) -> AppInfo:
        self._check_auth()
        if self.apply_error:
            raise PicoPaasError(self.apply_error)
        self.applied.append({"spec": spec, "project": project})
        return AppInfo(id=42, hostname=self.hostname, status="pending")

    def get_app(self, app_id: int) -> AppInfo:
        self._check_auth()
        st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return AppInfo(id=app_id, hostname=self.hostname, status=st)

    def logs(self, app_id: int, tail: int = 60) -> str:
        return self.log_text

    def probe(self, url: str) -> int | None:
        self.probes.append(url)
        return self.http_codes.pop(0) if len(self.http_codes) > 1 else self.http_codes[0]
