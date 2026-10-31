import pytest

from kaisen import builder, db
from kaisen.llm import ModelError, reply, tool_use
from kaisen.models import Event, InboxItem, ProjectFile, Run
from kaisen.worker import Lease
from tests.conftest import save_settings


def run_of(pid):
    with db.session_scope() as s:
        r = s.query(Run).filter(Run.project_id == pid).order_by(Run.id.desc()).first()
        s.expunge(r)
        return r


def build(ctx):
    r = ctx.post(f"/projects/{ctx.pid}/build")
    assert r.status_code == 303


def test_full_scripted_build_with_question(configured):
    c = configured
    c.model.script = [
        reply(tool_use("list_files", {}, "t1")),
        reply(tool_use("write_file", {"path": "README.md", "content": "# Todo"}, "t2"),
              tool_use("write_file", {"path": "app/main.py", "content": "print(1)"}, "t3")),
        reply(tool_use("ask_human", {"question": "Which database?"}, "t4")),
        reply(tool_use("write_file", {"path": "Dockerfile", "content": "FROM python:3.12-slim\nUSER 10001\n"}, "t5")),
        reply(tool_use("finish", {"summary": "All done"}, "t6")),
    ]
    build(c)
    assert c.tick() is True
    r = run_of(c.pid)
    assert r.status == "waiting" and len(c.model.calls) == 3
    inbox = c.client.get("/inbox").text
    assert "Which database?" in inbox
    with db.session_scope() as s:
        item = s.query(InboxItem).filter_by(kind="question").one()
        iid = item.id
    assert c.post(f"/inbox/{iid}/answer", answer="SQLite please").status_code == 303
    assert run_of(c.pid).status == "queued"
    c.tick()
    r = run_of(c.pid)
    assert r.status == "done" and r.summary == "All done"
    # answer was returned as a tool_result for the ask_human tool_use
    last_user = [m for m in c.model.calls[3] if m["role"] == "user"][-1]
    assert {"type": "tool_result", "tool_use_id": "t4", "content": "SQLite please"} in last_user["content"]
    with db.session_scope() as s:
        assert sorted(f.path for f in s.query(ProjectFile)) == ["Dockerfile", "README.md", "app/main.py"]
        assert s.query(Event).count() == 5 and s.query(Event).first().model == "claude-opus-5-5"
    page = c.client.get(f"/projects/{c.pid}").text
    assert "All done" in page and "Dockerfile" in page and "$0.0700" in page


def test_run_cap_stops(configured):
    c = configured
    save_settings(c, cap_run="0.01")
    c.model.script = [reply(tool_use("list_files", {}, "a")), reply(tool_use("list_files", {}, "b"))]
    build(c)
    c.tick()
    r = run_of(c.pid)
    assert r.status == "stopped" and "Run cap" in r.error
    assert len(c.model.calls) == 1
    assert "Run cap" in c.client.get("/inbox").text


def test_pause_stops_before_model_call(configured):
    c = configured
    c.post("/settings/pause")
    c.model.script = [reply(tool_use("list_files", {}))]
    build(c)
    c.tick()
    assert run_of(c.pid).status == "stopped" and c.model.calls == []


@pytest.mark.parametrize("cap,label", [("cap_day", "Daily cap"), ("cap_project", "Project cap")])
def test_day_and_project_caps(configured, cap, label):
    c = configured
    save_settings(c, **{cap: "0.001"})
    c.model.script = [reply(tool_use("list_files", {}, "a")), reply(tool_use("list_files", {}, "b"))]
    build(c)
    c.tick()
    assert label in run_of(c.pid).error and len(c.model.calls) == 1


def test_resume_after_cap_with_raised_caps(configured):
    c = configured
    save_settings(c, cap_run="0.01")
    c.model.script = [reply(tool_use("list_files", {}, "a")), reply(tool_use("finish", {"summary": "ok"}, "b"))]
    build(c)
    c.tick()
    assert run_of(c.pid).status == "stopped"
    save_settings(c, cap_run="2")
    build(c)
    c.tick()
    assert run_of(c.pid).status == "done"


@pytest.mark.parametrize("path", ["../x", "a/../../x", "/etc/passwd", ".git/config", ".github/workflows/a.yml",
                                  "a/.GIT/b", "", "a\\b", "."])
def test_path_rejections(path):
    with pytest.raises(builder.ToolError):
        builder.normalize_path(path)


def test_path_normalised():
    assert builder.normalize_path("a//b/./c.txt") == "a/b/c.txt"


def test_tool_path_and_size_rules(configured):
    c = configured
    big = "x" * (200 * 1024 + 1)
    c.model.script = [
        reply(tool_use("write_file", {"path": "../evil", "content": "x"}, "1"),
              tool_use("write_file", {"path": "big.txt", "content": big}, "2"),
              tool_use("write_file", {"path": ".github/w.yml", "content": "x"}, "3"),
              tool_use("write_file", {"path": "ok.txt", "content": "x"}, "4"),
              tool_use("write_file", {"path": "bad"}, "5")),
        reply(tool_use("finish", {"summary": "s"}, "6")),
    ]
    build(c)
    c.tick()
    results = c.model.calls[1][-1]["content"]
    errs = {r["tool_use_id"]: r.get("is_error") for r in results}
    assert errs == {"1": True, "2": True, "3": True, "4": None, "5": True}
    with db.session_scope() as s:
        assert [f.path for f in s.query(ProjectFile)] == ["ok.txt"]


def test_file_count_and_total_limits(configured):
    with db.session_scope() as s:
        ws = builder.Workspace(s, configured.pid)
        for i in range(300):
            ws.write(f"f{i}.txt", "x")
        with pytest.raises(builder.ToolError):
            ws.write("one-more.txt", "x")
        ws.write("f0.txt", "y")  # overwrite still fine


def test_repeat_detector(configured):
    c = configured
    c.model.script = [reply(tool_use("list_files", {}, f"r{i}")) for i in range(5)]
    build(c)
    c.tick()
    r = run_of(c.pid)
    assert r.status == "stopped" and "Repeat" in r.error and len(c.model.calls) == 3
    assert r.messages[-1]["role"] == "user"  # history still valid for resume


def test_refusal_stops_with_inbox_item(configured):
    c = configured
    c.model.script = [reply({"type": "text", "text": "no"}, stop_reason="refusal")]
    build(c)
    c.tick()
    r = run_of(c.pid)
    assert r.status == "stopped"
    with db.session_scope() as s:
        assert s.query(InboxItem).filter_by(kind="refusal").count() == 1


def test_max_tokens_is_step_error(configured):
    c = configured
    c.model.script = [reply({"type": "text", "text": "cut"}, stop_reason="max_tokens")]
    build(c)
    c.tick()
    assert run_of(c.pid).status == "error"


def test_model_error_stops(configured):
    c = configured
    c.model.script = [ModelError("Anthropic server error 500 (gave up after 4 retries)")]
    build(c)
    c.tick()
    r = run_of(c.pid)
    assert r.status == "error" and "500" in r.error


def test_metering_failure_fails_closed(configured, monkeypatch):
    c = configured
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(builder, "record_usage", boom)
    c.model.script = [reply(tool_use("write_file", {"path": "a.txt", "content": "x"}))]
    build(c)
    c.tick()
    r = run_of(c.pid)
    assert r.status == "error" and "Metering" in r.error
    with db.session_scope() as s:
        assert s.query(ProjectFile).count() == 0


def test_unknown_model_refuses(configured):
    c = configured
    with db.session_scope() as s:
        from kaisen import settings_store
        settings_store.set_value(s, "model", "claude-mystery-9")
    c.model.script = [reply(tool_use("list_files", {}))]
    build(c)
    c.tick()
    assert run_of(c.pid).status == "stopped" and c.model.calls == []


def test_no_api_key_stops_run(admin):
    r = admin.post("/projects/new", name="P", brief="b")
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    admin.post(f"/projects/{pid}/build")
    from kaisen.main import default_model_client
    from kaisen.worker import tick
    tick(default_model_client, admin.lease)
    assert run_of(pid).status == "error"


def test_wall_clock_limit(configured):
    c = configured
    c.model.script = [reply(tool_use("list_files", {}))]
    build(c)
    ticks = iter([0, 10_000, 10_000, 10_000])
    run = run_of(c.pid)
    builder.run_step(run.id, c.model, _held(c), clock=lambda: next(ticks))
    assert run_of(c.pid).status == "stopped" and "wall-clock" in run_of(c.pid).error


def _held(c):
    assert c.lease.acquire()
    return c.lease


def test_lease_fencing(configured):
    c = configured
    other = Lease()
    assert c.lease.acquire() is True
    assert other.acquire() is False  # live lease held by someone else
    assert c.lease.check() and not other.check()
    # expire it; other takes over with a new token
    from datetime import timedelta
    from kaisen.models import Lease as Row, utcnow
    with db.session_scope() as s:
        s.get(Row, "worker").expires = utcnow() - timedelta(seconds=1)
    assert other.acquire() is True and other.token == c.lease.token + 1
    assert not c.lease.check()
    # the old holder cannot run a build
    c.model.script = [reply(tool_use("list_files", {}))]
    build(c)
    run = run_of(c.pid)
    assert builder.run_step(run.id, c.model, c.lease) == "lease_lost"
    assert c.model.calls == []
    # standby tick does nothing
    assert c.tick() is False
