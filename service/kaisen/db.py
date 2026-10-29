"""Engine and session factory. configure() can be called again (tests)."""
import os
import ssl
from contextlib import contextmanager

from pymysql.constants import CLIENT
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
    if url.startswith("mysql://"):
        return "mysql+pymysql://" + url[len("mysql://"):]
    return url


def mysql_ssl_context(cafile: str) -> ssl.SSLContext:
    """Verify chain and hostname. Python 3.13+ sets VERIFY_X509_STRICT, which DO's managed MySQL CA fails
    ("Basic Constraints of CA cert not marked critical"); clear only that flag (same as PicoPaaS app/db_tls.py)."""
    ctx = ssl.create_default_context(cafile=cafile)
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


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
    # FOUND_ROWS: UPDATE reports matched rows, not changed rows. The lease CAS relies on rowcount, and a renew
    # within the same second (DATETIME has no fractions) would otherwise look like a lost lease.
    connect_args: dict = {"client_flag": CLIENT.FOUND_ROWS, "charset": "utf8mb4"}
    if ca_cert:
        with open(CA_PATH, "w") as f:
            f.write(ca_cert)
        os.chmod(CA_PATH, 0o600)
        connect_args["ssl"] = mysql_ssl_context(CA_PATH)
    # Two pods coexist during a rolling update; 2 + 2 stays well under the platform's 10 connections per app.
    return create_engine(url, pool_size=2, max_overflow=0, pool_pre_ping=True, pool_recycle=280,
                         connect_args=connect_args)


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
