import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("kaisen_boot", Path(__file__).resolve().parents[2] / "runtime" / "boot.py")
boot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(boot)


def make_tar(path, entries):
    """entries: (name, data | tarfile.TarInfo-customiser)"""
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, data
                tar.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, data
                tar.addfile(info)
            elif kind == "device":
                info.type = tarfile.CHRTYPE
                tar.addfile(info)


def test_manifest_defaults_and_values():
    assert boot.parse_manifest(None) == {"runtime": "static", "start": "", "health": "/"}
    assert boot.parse_manifest('{"runtime": "node"}')["start"] == "npm start"
    m = boot.parse_manifest('{"runtime": "python", "start": "python app.py", "health": "/up"}')
    assert m == {"runtime": "python", "start": "python app.py", "health": "/up"}


@pytest.mark.parametrize("text", ["nope", "[]", '{"runtime": "ruby"}', '{"runtime": "python"}', '{"start": 1}'])
def test_manifest_rejects(text):
    with pytest.raises(boot.BootError):
        boot.parse_manifest(text)


def test_safe_extract_ok(tmp_path):
    t = tmp_path / "b.tar.gz"
    make_tar(t, [("proj", "dir", None), ("proj/kaisen.json", "file", b"{}"), ("proj/public/index.html", "file", b"hi")])
    dest = tmp_path / "out"
    assert boot.safe_extract(str(t), str(dest)) == 2
    assert (dest / "proj/public/index.html").read_text() == "hi"
    assert boot.project_root(str(dest)) == str(dest / "proj")


@pytest.mark.parametrize("entry", [
    ("/etc/evil", "file", b"x"), ("../evil", "file", b"x"), ("a/../../evil", "file", b"x"),
    ("a/link", "symlink", "/etc/passwd"), ("a/hard", "hardlink", "a/x"), ("a/dev", "device", None),
])
def test_safe_extract_rejects(tmp_path, entry):
    t = tmp_path / "b.tar.gz"
    make_tar(t, [("a/ok", "file", b"x"), entry])
    dest = tmp_path / "out"
    with pytest.raises(boot.BootError):
        boot.safe_extract(str(t), str(dest))
    assert not (dest / "a/ok").exists()  # validated before anything is written


def test_sha_check(tmp_path):
    f = tmp_path / "x"
    f.write_bytes(b"hello")
    boot.verify_sha(str(f), hashlib.sha256(b"hello").hexdigest())
    with pytest.raises(boot.BootError, match="sha256 mismatch"):
        boot.verify_sha(str(f), "0" * 64)


def test_download_retries_then_fails(tmp_path, monkeypatch, capsys):
    calls = []

    def fail(url, timeout):
        calls.append(url)
        raise OSError("boom")
    monkeypatch.setattr(boot.urllib.request, "urlopen", fail)
    with pytest.raises(boot.BootError, match="could not download"):
        boot.download("http://x/y", str(tmp_path / "o"), attempts=3, sleep=lambda n: None)
    assert len(calls) == 3 and "[kaisen-runtime] download attempt 3/3 failed" in capsys.readouterr().out


class _Resp:
    def __init__(self, data):
        self.buf = io.BytesIO(data)

    def read(self, n):
        return self.buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_success_after_retry(tmp_path, monkeypatch):
    seq = [OSError("flaky"), _Resp(b"payload")]
    slept = []

    def fake(url, timeout):
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(boot.urllib.request, "urlopen", fake)
    out = tmp_path / "o"
    boot.download("http://x/y", str(out), sleep=slept.append)
    assert out.read_bytes() == b"payload" and slept == [2.0]


def test_download_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "MAX_BUNDLE_BYTES", 4)
    monkeypatch.setattr(boot.urllib.request, "urlopen", lambda url, timeout: _Resp(b"0123456789"))
    with pytest.raises(boot.BootError, match="larger than"):
        boot.download("http://x/y", str(tmp_path / "o"), sleep=lambda n: None)


def test_safe_extract_rejects_oversize_total(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "MAX_EXTRACT_BYTES", 3)
    t = tmp_path / "b.tar.gz"
    make_tar(t, [("a/f", "file", b"12345")])
    with pytest.raises(boot.BootError, match="too large"):
        boot.safe_extract(str(t), str(tmp_path / "out"))


def test_project_root_flat(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "d").mkdir()
    assert boot.project_root(str(tmp_path)) == str(tmp_path)


def test_run_success_failure_timeout(tmp_path):
    env = dict(boot.os.environ)
    boot.run([boot.sys.executable, "-c", "pass"], str(tmp_path), env)
    with pytest.raises(boot.BootError, match="exit code 3"):
        boot.run("exit 3", str(tmp_path), env, shell=True)
    with pytest.raises(boot.BootError, match="timed out"):
        boot.run([boot.sys.executable, "-c", "import time; time.sleep(5)"], str(tmp_path), env, timeout=1)


def test_serve_static_prefers_public(tmp_path, monkeypatch):
    (tmp_path / "public").mkdir()
    seen = {}

    class FakeServer:
        def __init__(self, addr, handler):
            seen["addr"], seen["handler"] = addr, handler

        def serve_forever(self):
            seen["served"] = True
    monkeypatch.setattr(boot.http.server, "ThreadingHTTPServer", FakeServer)
    boot.serve_static(str(tmp_path), 9999)
    assert seen["addr"] == ("0.0.0.0", 9999) and seen["handler"].keywords["directory"] == str(tmp_path / "public")
    boot.serve_static(str(tmp_path / "public"), 1)  # no public/ below it: falls back to the root
    assert seen["handler"].keywords["directory"] == str(tmp_path / "public")


def _bundle(tmp_path, entries):
    t = tmp_path / "bundle.tar.gz"
    make_tar(t, entries)
    return t, hashlib.sha256(t.read_bytes()).hexdigest()


def _boot_env(monkeypatch, tmp_path, t, sha):
    monkeypatch.setattr(boot, "HOME", str(tmp_path / "home"))
    monkeypatch.setattr(boot, "SRC", str(tmp_path / "home" / "src"))
    monkeypatch.setenv("KAISEN_BUNDLE_URL", "http://bundle.test/b")
    monkeypatch.setenv("KAISEN_BUNDLE_SHA256", sha)

    def fake_download(url, dest, **kw):
        shutil.copy(t, dest)
    monkeypatch.setattr(boot, "download", fake_download)


import shutil  # noqa: E402


def test_main_requires_env(monkeypatch):
    monkeypatch.delenv("KAISEN_BUNDLE_URL", raising=False)
    with pytest.raises(boot.BootError, match="must be set"):
        boot.main()


def test_main_static_site(tmp_path, monkeypatch, capsys):
    t, sha = _bundle(tmp_path, [("p/public/index.html", "file", b"hi")])
    _boot_env(monkeypatch, tmp_path, t, sha)
    served = []
    monkeypatch.setattr(boot, "serve_static", lambda root, port: served.append((root, port)))
    assert boot.main() == 0
    assert served and served[0][1] == 8080
    assert "no kaisen.json found" in capsys.readouterr().out


def test_main_sha_mismatch(tmp_path, monkeypatch):
    t, _ = _bundle(tmp_path, [("p/x", "file", b"hi")])
    _boot_env(monkeypatch, tmp_path, t, "0" * 64)
    with pytest.raises(boot.BootError, match="sha256 mismatch"):
        boot.main()


def test_main_python_installs_then_execs(tmp_path, monkeypatch):
    t, sha = _bundle(tmp_path, [("p/kaisen.json", "file", b'{"runtime":"python","start":"python app.py"}'),
                                ("p/requirements.txt", "file", b"")])
    _boot_env(monkeypatch, tmp_path, t, sha)
    cmds, execs = [], []
    monkeypatch.setattr(boot, "run", lambda cmd, cwd, env, **kw: cmds.append(cmd))
    monkeypatch.setattr(boot.os, "chdir", lambda p: None)
    monkeypatch.setattr(boot.os, "execvpe", lambda f, a, e: execs.append((a, e)))
    boot.main()
    assert cmds[0][1:3] == ["-m", "venv"] and cmds[1][:2] == ["pip", "install"]
    assert execs[0][0] == ["sh", "-c", "python app.py"]
    assert execs[0][1]["PORT"] == "8080" and "venv" in execs[0][1]["VIRTUAL_ENV"]


def test_main_node_npm_install(tmp_path, monkeypatch):
    t, sha = _bundle(tmp_path, [("p/kaisen.json", "file", b'{"runtime":"node"}'), ("p/package.json", "file", b"{}")])
    _boot_env(monkeypatch, tmp_path, t, sha)
    cmds, execs = [], []
    monkeypatch.setattr(boot, "run", lambda cmd, cwd, env, **kw: cmds.append(cmd))
    monkeypatch.setattr(boot.os, "chdir", lambda p: None)
    monkeypatch.setattr(boot.os, "execvpe", lambda f, a, e: execs.append(a))
    boot.main()
    assert cmds == [["npm", "install", "--omit=dev"]] and execs[0][2] == "npm start"
