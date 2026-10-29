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
