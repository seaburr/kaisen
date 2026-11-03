import os
import ssl

import pytest

from kaisen import db


def test_make_url_rewrites_mysql_scheme():
    assert db.make_url("mysql://u:p@h/db").startswith("mysql+pymysql://")
    assert db.make_url("sqlite://") == "sqlite://"


def test_mysql_ssl_context_requires_verification():
    import certifi
    ctx = db.mysql_ssl_context(certifi.where())
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert not ctx.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0)


def test_sqlite_file_engine_creates_parent_dir(tmp_path):
    path = tmp_path / "sub" / "k.db"
    eng = db.build_engine(f"sqlite:///{path}")
    assert os.path.isdir(path.parent)
    eng.dispose()


def test_mysql_engine_uses_small_pool_and_writes_ca(tmp_path, monkeypatch):
    import certifi
    ca = tmp_path / "ca.pem"
    monkeypatch.setattr(db, "CA_PATH", str(ca))
    pem = open(certifi.where()).read().split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"
    eng = db.build_engine("mysql://u:p@127.0.0.1/db", ca_cert=pem)
    assert eng.pool.size() == 2
    assert ca.read_text() == pem
    assert oct(os.stat(ca).st_mode)[-3:] == "600"
    eng.dispose()


def test_session_scope_rolls_back_on_error():
    db.configure("sqlite://")
    with pytest.raises(RuntimeError):
        with db.session_scope():
            raise RuntimeError("boom")
