import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_on_temp_sqlite(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "m.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}", "ENVIRONMENT": "test"}
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    tables = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert {"settings", "users", "projects", "project_files", "runs", "events", "inbox", "lease"} <= tables


def test_mysql_engine_settings(tmp_path):
    from pymysql.constants import CLIENT
    from sqlalchemy.dialects import mysql
    from kaisen import db, models
    assert db.make_url("mysql://u:p@h:25060/app_1") == "mysql+pymysql://u:p@h:25060/app_1"
    eng = db.build_engine("mysql://u:p@h:25060/app_1")  # no connection is opened here
    assert eng.dialect.name == "mysql" and eng.pool.size() == 2
    assert models.ProjectFile.__table__.c.content.type.dialect_impl(mysql.dialect()).__class__.__name__ == "MEDIUMTEXT"
    assert CLIENT.FOUND_ROWS
