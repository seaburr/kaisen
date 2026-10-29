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
