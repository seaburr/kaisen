import gzip
import io
import tarfile

from .builder import normalize_path


def build_tarball(slug: str, files: list[tuple[str, str]]) -> bytes:
    """Deterministic tar.gz of (path, content) under a `<slug>/` directory. Held in memory."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path, content in sorted(files):
                safe = normalize_path(path)  # re-check: never trust stored names
                data = content.encode()
                info = tarfile.TarInfo(f"{slug}/{safe}")
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()
