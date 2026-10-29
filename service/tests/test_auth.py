import re

from kaisen import db
from kaisen.models import Setting, User
from tests.conftest import PASSWORD, csrf_of


def test_healthz_no_db(ctx, monkeypatch):
    monkeypatch.setattr(db, "SessionLocal", None)  # any DB use would blow up
    monkeypatch.setattr(db, "engine", None)
    r = ctx.client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


def test_unauthenticated_redirects(ctx):
    assert ctx.client.get("/").headers["location"] == "/setup"


def test_setup_code_single_use_and_password_length(ctx):
    t = csrf_of(ctx.client, "/setup")
    r = ctx.client.post("/setup", data={"csrf": t, "code": "wrong-code", "password": PASSWORD})
    assert r.status_code == 400
    r = ctx.client.post("/setup", data={"csrf": t, "code": ctx.code, "password": "short"})
    assert r.status_code == 400
    r = ctx.client.post("/setup", data={"csrf": t, "code": ctx.code, "password": PASSWORD})
    assert r.status_code == 303
    r = ctx.client.post("/setup", data={"csrf": t, "code": ctx.code, "password": "another-password-1"})
    assert r.status_code == 400  # already claimed
    with db.session_scope() as s:
        assert s.query(User).count() == 1
        assert s.get(Setting, "setup_code") is None


def test_login_and_lockout(ctx):
    t = csrf_of(ctx.client, "/setup")
    ctx.client.post("/setup", data={"csrf": t, "code": ctx.code, "password": PASSWORD})
    t = csrf_of(ctx.client, "/login")
    for _ in range(5):
        assert ctx.client.post("/login", data={"csrf": t, "password": "nope-nope-nope"}).status_code == 401
    # even the right password is refused now
    assert ctx.client.post("/login", data={"csrf": t, "password": PASSWORD}).status_code == 429


def test_login_ok_sets_secure_cookie_flags(admin):
    r = admin.client.get("/")
    assert r.status_code == 200
    h = r.headers["set-cookie"].lower()
    assert "httponly" in h and "samesite=strict" in h
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_csrf_rejected(admin):
    r = admin.client.post("/projects/new", data={"name": "x", "brief": "y"})
    assert r.status_code == 403
    r = admin.client.post("/projects/new", data={"csrf": "bogus", "name": "x", "brief": "y"})
    assert r.status_code == 403
    r = admin.client.post("/logout", data={})
    assert r.status_code == 403


def test_pages_require_login(ctx):
    t = csrf_of(ctx.client, "/setup")
    ctx.client.post("/setup", data={"csrf": t, "code": ctx.code, "password": PASSWORD})
    for path in ("/", "/settings", "/inbox", "/spend", "/projects/new", "/projects/1"):
        assert ctx.client.get(path).headers["location"] == "/login"
