import logging
import re

from kaisen import logging_setup


def _fmt(formatter_key: str, record: logging.LogRecord) -> str:
    cfg = logging_setup.config_dict("INFO")["formatters"][formatter_key]
    return cfg["()"](fmt=cfg["fmt"]).format(record)


def test_default_line_has_utc_time_location_and_function():
    rec = logging.LogRecord("kaisen.builder", logging.WARNING, "/srv/app/builder.py", 123, "app %s failed", ("7",), None,
                            func="run_step")
    line = _fmt("default", rec)
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z WARNING kaisen\.builder \[\w+\] builder\.py:123 "
                    r"run_step\(\) app 7 failed$", line), line


def test_access_line_from_uvicorn_args():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "h11_impl.py", 1, '%s - "%s %s HTTP/%s" %d',
                            ("10.0.0.1:5000", "GET", "/healthz", "1.1", 200), None)
    assert _fmt("access", rec).endswith('INFO    access 10.0.0.1:5000 "GET /healthz HTTP/1.1" 200')


def test_access_line_other_shape_falls_back():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "x.py", 1, "plain", None, None)
    assert '"plain"' in _fmt("access", rec)


def test_level_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert logging_setup.config_dict()["root"]["level"] == "DEBUG"


def test_noisy_libraries_stay_at_warning():
    loggers = logging_setup.config_dict("DEBUG")["loggers"]
    assert all(loggers[n]["level"] == "WARNING" for n in ("httpx", "httpx2", "httpcore"))
