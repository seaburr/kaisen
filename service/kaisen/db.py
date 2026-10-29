"""Engine and session factory. configure() can be called again (tests)."""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

CA_PATH = "/tmp/db-ca.pem"


class Base(DeclarativeBase):
    pass


engine: Engine | None = None
SessionLocal: sessionmaker | None = None


def make_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def build_engine(url: str, ca_cert: str | None = None) -> Engine:
    url = make_url(url)
    if url.startswith("sqlite"):
        kw: dict = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kw["poolclass"] = StaticPool
        else:
            path = url.split("///", 1)[-1]
            if os.path.dirname(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
        return create_engine(url, **kw)
    connect_args = {}
    if ca_cert:
        with open(CA_PATH, "w") as f:
            f.write(ca_cert)
        os.chmod(CA_PATH, 0o600)
        connect_args = {"sslmode": "verify-full", "sslrootcert": CA_PATH}
    return create_engine(url, pool_size=2, max_overflow=0, pool_pre_ping=True, connect_args=connect_args)


def configure(url: str, ca_cert: str | None = None) -> Engine:
    global engine, SessionLocal
    engine = build_engine(url, ca_cert)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    return engine


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
