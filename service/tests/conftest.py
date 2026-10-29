import os
import re

os.environ["ENVIRONMENT"] = "test"
os.environ["WORKER_ENABLED"] = "0"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ.pop("SECRET_KEY", None)

import pytest
from fastapi.testclient import TestClient

from kaisen import auth, db
from kaisen.github import FakeGitHub
from kaisen.llm import FakeModelClient
from kaisen.main import create_app
from kaisen.worker import Lease, tick

PASSWORD = "not-the-password-123"


class Ctx:
    pass


def csrf_of(client, path="/settings") -> str:
    html = client.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


@pytest.fixture
def ctx():
    c = Ctx()
    c.model = FakeModelClient()
    c.gh = FakeGitHub()
    app = create_app(model_client_factory=lambda rs: c.model, github_factory=lambda tok: c.gh, start_worker=False)
    db.Base.metadata.create_all(db.engine)
    c.code = auth.ensure_setup_code()
    c.client = TestClient(app, follow_redirects=False)
    c.lease = Lease()
    c.app = app
    c.csrf = lambda path="/settings": csrf_of(c.client, path)
    c.post = lambda path, **data: c.client.post(path, data={"csrf": c.csrf("/login" if not c.logged else "/settings"), **data})
    c.logged = False
    c.tick = lambda: tick(lambda rs: c.model, c.lease)
    yield c
    if db.engine is not None:
        db.engine.dispose()


@pytest.fixture
def admin(ctx):
    """Real setup + login flow."""
    t = csrf_of(ctx.client, "/setup")
    r = ctx.client.post("/setup", data={"csrf": t, "code": ctx.code, "password": PASSWORD})
    assert r.status_code == 303
    t = csrf_of(ctx.client, "/login")
    r = ctx.client.post("/login", data={"csrf": t, "password": PASSWORD})
    assert r.status_code == 303
    ctx.logged = True
    return ctx


@pytest.fixture
def configured(admin):
    """Admin with an API key saved and a project created."""
    save_settings(admin, anthropic_api_key="fake-anthropic-key-0001")
    r = admin.post("/projects/new", name="Todo App", brief="Build a todo list")
    assert r.status_code == 303
    admin.pid = int(r.headers["location"].rsplit("/", 1)[1])
    return admin


def save_settings(ctx, **over):
    form = dict(model="claude-opus-5-5", effort="medium", cap_run="2", cap_day="8", cap_project="30",
                github_repo="", github_branch="", picopaas_url="")
    form.update(over)
    r = ctx.post("/settings", **form)
    assert r.status_code == 303, r.text
