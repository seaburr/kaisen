"""Kaisen runtime boot script (stdlib only).

Env: KAISEN_BUNDLE_URL, KAISEN_BUNDLE_SHA256. Downloads the bundle, verifies it, safely extracts it to
/home/app/src, reads kaisen.json, installs dependencies and starts the app on $PORT (8080).
Every phase logs a line prefixed [kaisen-runtime]; any failure exits non-zero.
"""
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

PREFIX = "[kaisen-runtime]"
HOME = os.environ.get("HOME", "/home/app")
SRC = os.path.join(HOME, "src")
RUNTIMES = ("static", "python", "node")
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_EXTRACT_BYTES = 50 * 1024 * 1024


class BootError(Exception):
    pass


def log(msg: str) -> None:
    print(f"{PREFIX} {msg}", flush=True)


def parse_manifest(text: str | None) -> dict:
    """kaisen.json -> {"runtime", "start", "health"}. Missing file means a static site."""
    if text is None:
        return {"runtime": "static", "start": "", "health": "/"}
    try:
        data = json.loads(text)
    except ValueError as e:
        raise BootError(f"kaisen.json is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise BootError("kaisen.json must be a JSON object")
    runtime = data.get("runtime", "static")
    if runtime not in RUNTIMES:
        raise BootError(f"kaisen.json runtime must be one of {', '.join(RUNTIMES)} (got {runtime!r})")
    start = data.get("start", "")
    health = data.get("health", "/")
    if not isinstance(start, str) or not isinstance(health, str):
        raise BootError("kaisen.json 'start' and 'health' must be strings")
    if runtime == "python" and not start.strip():
        raise BootError("kaisen.json runtime 'python' needs a 'start' command")
    if runtime == "node" and not start.strip():
        start = "npm start"
    return {"runtime": runtime, "start": start.strip(), "health": health or "/"}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha(path: str, expected: str) -> None:
    got = sha256_file(path)
    if got != expected.strip().lower():
        raise BootError(f"sha256 mismatch: expected {expected[:12]}..., got {got[:12]}...")


def _inside(dest: str, target: str) -> bool:
    dest, target = os.path.realpath(dest), os.path.realpath(target)
    return target == dest or target.startswith(dest + os.sep)


def safe_extract(tar_path: str, dest: str) -> int:
    """Extract regular files and directories only. Rejects absolute paths, '..', links and devices."""
    os.makedirs(dest, exist_ok=True)
    total = count = 0
    with tarfile.open(tar_path, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            name = m.name
            if name.startswith("/") or "\\" in name or "\x00" in name or ".." in name.split("/"):
                raise BootError(f"unsafe path in bundle: {name!r}")
            if not (m.isfile() or m.isdir()):
                raise BootError(f"unsupported entry in bundle (links/devices are not allowed): {name!r}")
            if not _inside(dest, os.path.join(dest, name)):
                raise BootError(f"path escapes the target directory: {name!r}")
            total += m.size
            if total > MAX_EXTRACT_BYTES:
                raise BootError("bundle is too large when extracted")
        for m in members:
            target = os.path.join(dest, m.name)
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tar.extractfile(m)
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o644)
            count += 1
    return count


def project_root(dest: str) -> str:
    """Bundles hold one top-level <slug>/ directory; use it as the project root."""
    entries = os.listdir(dest)
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
        return os.path.join(dest, entries[0])
    return dest


def download(url: str, dest: str, attempts: int = 5, sleep=time.sleep) -> None:
    delay = 2.0
    for n in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as r, open(dest, "wb") as out:
                size = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BUNDLE_BYTES:
                        raise BootError("bundle is larger than 20 MB")
                    out.write(chunk)
            return
        except BootError:
            raise
        except Exception as e:  # network errors, HTTP errors
            log(f"download attempt {n}/{attempts} failed: {type(e).__name__}: {e}")
            if n == attempts:
                raise BootError("could not download the bundle (is KAISEN_BUNDLE_URL reachable from this app?)")
            sleep(delay)
            delay = min(delay * 2, 20)


def run(cmd: list[str] | str, cwd: str, env: dict, shell: bool = False, timeout: int = 900) -> None:
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, shell=shell, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BootError(f"command timed out after {timeout}s: {cmd}")
    if r.returncode != 0:
        raise BootError(f"command failed with exit code {r.returncode}: {cmd}")


def serve_static(root: str, port: int) -> None:
    directory = os.path.join(root, "public")
    if not os.path.isdir(directory):
        directory = root
    log(f"serving static files from {directory} on 0.0.0.0:{port}")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    http.server.ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


def main() -> int:
    url = os.environ.get("KAISEN_BUNDLE_URL", "")
    want = os.environ.get("KAISEN_BUNDLE_SHA256", "")
    if not url or not want:
        raise BootError("KAISEN_BUNDLE_URL and KAISEN_BUNDLE_SHA256 must be set")
    port = int(os.environ.get("PORT", "8080"))
    os.makedirs(HOME, exist_ok=True)
    archive = os.path.join(HOME, "bundle.tar.gz")

    log("phase 1/4: downloading the project bundle")
    download(url, archive)
    log("phase 2/4: verifying and extracting")
    verify_sha(archive, want)
    shutil.rmtree(SRC, ignore_errors=True)
    n = safe_extract(archive, SRC)
    root = project_root(SRC)
    log(f"extracted {n} files")

    mf_path = os.path.join(root, "kaisen.json")
    manifest = parse_manifest(open(mf_path).read() if os.path.isfile(mf_path) else None)
    if not os.path.isfile(mf_path):
        log("no kaisen.json found: treating the project as a static site")
    log(f"runtime={manifest['runtime']} start={manifest['start']!r}")

    env = dict(os.environ, PORT="8080")
    if manifest["runtime"] == "static":
        log("phase 3/4: nothing to install")
        log("phase 4/4: starting")
        serve_static(root, port)
        return 0

    log("phase 3/4: installing dependencies")
    if manifest["runtime"] == "python":
        venv = os.path.join(HOME, "venv")
        run([sys.executable, "-m", "venv", venv], root, env)
        env["PATH"] = os.path.join(venv, "bin") + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = venv
        if os.path.isfile(os.path.join(root, "requirements.txt")):
            run(["pip", "install", "--no-cache-dir", "-r", "requirements.txt"], root, env)
    else:
        if os.path.isfile(os.path.join(root, "package.json")):
            run(["npm", "install", "--omit=dev"], root, env)
    log("phase 4/4: starting the app")
    sys.stdout.flush()
    os.chdir(root)
    os.execvpe("sh", ["sh", "-c", manifest["start"]], env)
    return 0  # unreachable


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BootError as e:
        log(f"FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"FAILED: unexpected {type(e).__name__}: {e}")
        sys.exit(1)
