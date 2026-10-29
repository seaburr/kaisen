import json
import gzip
import io
import tarfile

import httpx

from kaisen import db, export
from kaisen.github import GitHubError, RealGitHub
from kaisen.llm import AnthropicClient
from kaisen.models import ProjectFile
from kaisen.redact import redact, transcript_lines
from tests.conftest import save_settings


def add_files(pid, **files):
    with db.session_scope() as s:
        for p, c in files.items():
            s.add(ProjectFile(project_id=pid, path=p.replace("__", "/"), content=c, size=len(c)))


def test_tarball_contents_and_determinism(configured):
    c = configured
    add_files(c.pid, README_md="hi", src__a_py="print(1)")
    r = c.client.get(f"/projects/{c.pid}/download")
    assert r.status_code == 200 and "todo-app.tar.gz" in r.headers["content-disposition"]
    tf = tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz")
    assert sorted(tf.getnames()) == ["todo-app/README_md", "todo-app/src/a_py"]
    assert tf.extractfile("todo-app/src/a_py").read() == b"print(1)"
    assert all(m.mtime == 0 for m in tf.getmembers())
    assert r.content == c.client.get(f"/projects/{c.pid}/download").content


def test_tarball_refuses_unsafe_names():
    import pytest
    from kaisen.builder import ToolError
    with pytest.raises(ToolError):
        export.build_tarball("x", [("../evil", "a")])


def test_github_push_fake(configured):
    c = configured
    save_settings(c, github_repo="me/app", github_token="fake-github-token-wxyz")
    add_files(c.pid, README_md="hi")
    c.post(f"/projects/{c.pid}/push", repo="", open_pr="1")
    flash = c.client.get(f"/projects/{c.pid}").text
    assert "Pushed to https://github.com/me/app/tree/kaisen/todo-app" in flash and "pull/1" in flash
    assert c.gh.pushes[0]["files"] == {"README_md": "hi"}
    assert "fake-github-token-wxyz" not in flash


def test_github_push_empty_repo_seeded(configured):
    c = configured
    c.gh.empty = True
    save_settings(c, github_repo="me/app", github_token="fake-github-token-wxyz")
    add_files(c.pid, a="x")
    c.post(f"/projects/{c.pid}/push")
    assert "empty repo was seeded" in c.client.get(f"/projects/{c.pid}").text


def test_push_requires_settings(configured):
    configured.post(f"/projects/{configured.pid}/push")
    assert "Set a GitHub repo and token" in configured.client.get(f"/projects/{configured.pid}").text


def _gh_transport(empty: bool, log: list):
    state = {"seeded": not empty}

    def handler(req: httpx.Request) -> httpx.Response:
        log.append((req.method, req.url.path))
        assert req.headers["authorization"] == "Bearer fake-github-token-wxyz"
        p, m = req.url.path, req.method
        if p == "/repos/me/app" and m == "GET":
            return httpx.Response(200, json={"default_branch": "main", "permissions": {"push": True}})
        if p == "/repos/me/app/git/ref/heads/main":
            if not state["seeded"]:
                return httpx.Response(409, json={"message": "Git Repository is empty."})
            return httpx.Response(200, json={"object": {"sha": "b" * 40}})
        if p == "/repos/me/app/git/ref/heads/kaisen/app":
            return httpx.Response(404, json={})
        if p == "/repos/me/app/contents/README.md" and m == "PUT":
            state["seeded"] = True
            return httpx.Response(201, json={})
        if "/git/commits/" in p and m == "GET":
            return httpx.Response(200, json={"tree": {"sha": "t" * 40}})
        if p.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "c" * 40})
        if p.endswith("/git/trees"):
            log.append(("TREE", "base_tree" in json.loads(req.content)))
            return httpx.Response(201, json={"sha": "d" * 40})
        if p.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "e" * 40})
        if p.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if p.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/me/app/pull/2"})
        return httpx.Response(500, json={"message": "unexpected " + p})
    return httpx.MockTransport(handler)


def test_real_github_client_empty_repo_path():
    log = []
    gh = RealGitHub("fake-github-token-wxyz", transport=_gh_transport(True, log))
    res = gh.push("me/app", [("a.txt", "x")], "app", None, "msg", True)
    assert res.seeded and res.branch == "kaisen/app" and res.pr_url.endswith("/pull/2")
    assert ("PUT", "/repos/me/app/contents/README.md") in log
    assert log.index(("PUT", "/repos/me/app/contents/README.md")) < log.index(("POST", "/repos/me/app/git/refs"))
    assert ("TREE", False) in log  # kaisen/<slug> mirrors the project: deleted files do not linger


def test_real_github_client_errors_are_plain():
    import pytest
    gh = RealGitHub("fake-github-token-wxyz", transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    with pytest.raises(GitHubError, match="rejected the token"):
        gh.check_repo("me/app")


def test_redaction_in_transcript(configured):
    c = configured
    from kaisen.llm import reply, tool_use
    c.model.script = [reply({"type": "text", "text": "key is fake-anthropic-key-0001 ok"},
                            tool_use("finish", {"summary": "done fake-anthropic-key-0001"}))]
    c.post(f"/projects/{c.pid}/build")
    c.tick()
    page = c.client.get(f"/projects/{c.pid}").text
    assert "fake-anthropic-key-0001" not in page and "[REDACTED]" in page
    assert redact("a fake-anthropic-key-0001 b", ["fake-anthropic-key-0001"]) == "a [REDACTED] b"
    assert transcript_lines([{"role": "assistant", "content": [{"type": "thinking", "thinking": "secret"}]}], []) == []


def test_xss_escaped(configured):
    c = configured
    c.post("/projects/new", name="<script>alert(1)</script>", brief="b")
    assert "<script>alert(1)</script>" not in c.client.get("/").text


class _Stream:
    def __init__(self, msg):
        self.msg = msg
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get_final_message(self):
        return self.msg


def test_anthropic_client_call_shape():
    from types import SimpleNamespace as NS
    seen = {}

    class Msgs:
        def stream(self, *, model, max_tokens, system, tools, messages, output_config=None, extra_body=None):
            seen.update(model=model, max_tokens=max_tokens, system=system, output_config=output_config)
            blk = NS(model_dump=lambda **k: {"type": "text", "text": "hi"})
            return _Stream(NS(content=[blk], stop_reason="end_turn", usage=NS(
                input_tokens=1, cache_creation_input_tokens=2, cache_read_input_tokens=3, output_tokens=4)))

    cl = AnthropicClient(client=NS(messages=Msgs()))
    r = cl.create(model="claude-opus-5-5", messages=[], effort="high")
    assert seen["output_config"] == {"effort": "high"} and seen["max_tokens"] == 32000
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert r.usage == {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}
